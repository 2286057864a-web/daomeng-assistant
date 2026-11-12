"""任务只执行一轮；小步滚动不截断搜索，不把未命中当成结束。"""
import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gui
import makeup
import roster_device
import roster_workflow
from verify_roster import FakeBackend, Candidate, workbook, read


class TraversalTests(unittest.TestCase):
    def test_open_unsigned_does_not_preload_roster(self):
        app = SimpleNamespace(selected=lambda: {"name": "目标活动"}, connect=lambda: object(),
                              run=lambda job: job())
        with patch.object(makeup, "open_unsigned", return_value=True), \
             patch.object(makeup, "_ready_snapshot", return_value=([], "补签0人")), \
             patch.object(makeup, "wait_rows", return_value=([], "补签0人")), \
             patch.object(makeup, "reset_top"), \
             patch.object(makeup, "collect", side_effect=AssertionError("不应先完整读取名单")):
            gui.App._open_unsigned(app)

    def test_makeup_needs_three_scrolls_without_new_people(self):
        state = {"swipes": 0}
        row = {"name": "甲", "info": "2024级·班级", "grade": "2024", "cx": 100, "cy": 200}
        def scroll(d):
            state["swipes"] += 1
        with patch.object(makeup, "_ready_snapshot", return_value=([row], "补签0人")), \
             patch.object(makeup, "snap", return_value=([row], "补签0人")), \
             patch.object(makeup, "reset_top"), patch.object(makeup, "_scroll_people", scroll):
            self.assertEqual(makeup.walk_select(object(), "2023"), [])
        self.assertEqual(state["swipes"], 3)

    def test_person_swipe_is_small_and_inside_list(self):
        xml = '<hierarchy><node resource-id="app:id/recycleview" bounds="[0,600][1440,2900]"/></hierarchy>'
        swipes = []
        device = SimpleNamespace(dump_hierarchy=lambda: xml, swipe=lambda *args: swipes.append(args))
        with patch.object(makeup.time, "sleep"):
            makeup._scroll_people(device)
        x1, y1, x2, y2, duration = swipes[0]
        self.assertEqual(x1, x2)
        self.assertGreater(y2, 600)
        self.assertLess(y1, 2900)
        self.assertAlmostEqual(y1 - y2, (2900-600)*.35, delta=1)

    def test_person_scroll_back_is_also_small(self):
        xml = '<hierarchy><node resource-id="app:id/recycleview" bounds="[0,600][1440,2900]"/></hierarchy>'
        swipes = []
        device = SimpleNamespace(dump_hierarchy=lambda: xml, swipe=lambda *args: swipes.append(args))
        with patch.object(makeup.time, "sleep"):
            makeup._scroll_people(device, toward_top=True)
        x1, y1, x2, y2, duration = swipes[0]
        self.assertGreater(y2, y1)
        self.assertAlmostEqual(y2-y1, (2900-600)*.35, delta=1)

    def backend(self, pages):
        state = {"page": 0, "swipes": 0}
        backend = roster_device.DeviceBackend(None)
        def scroll():
            state["page"] = min(state["page"] + 1, len(pages)-1)
            state["swipes"] += 1
        backend._rows = lambda *args: pages[state["page"]]
        backend._ready_rows = backend._rows
        backend._swipe_down = scroll
        backend._back_to_top = lambda: state.update(page=0)
        backend._type_search = lambda query: state.update(page=0)
        return backend, state

    @staticmethod
    def row(name, y=200):
        return {"name": name, "info": "班级", "cy": y, "cx": 1000}

    def test_search_includes_people_beyond_five_screens(self):
        backend, state = self.backend([[self.row("张" + str(i))] for i in range(8)])
        self.assertEqual(len(backend.search("张")), 8)

    def test_overlapping_screens_do_not_hide_later_people(self):
        first = self.row("张甲")
        backend, state = self.backend([[first], [first], [first], [self.row("张乙")]])
        self.assertEqual([r["name"] for r in backend._collect()], ["张甲", "张乙"])

    def test_search_limit_is_incomplete_not_success(self):
        backend, state = self.backend([[self.row("张" + str(i))] for i in range(8)])
        with self.assertRaisesRegex(RuntimeError, "未确认.*结束"):
            backend._collect(limit=3)

    def test_loading_is_not_counted_as_empty_search(self):
        prefix = '<hierarchy><node resource-id="' + roster_device.SEARCH_ID + '"/>'
        footer = '<node resource-id="' + roster_device.FOOTER_ID + '" text="录取0人"/></hierarchy>'
        loading = prefix + '<node class="android.widget.ProgressBar"/>' + footer
        ready = prefix + footer
        backend = roster_device.DeviceBackend(None)
        frames = iter([loading, loading, loading, ready, ready])
        backend._xml = lambda: next(frames)
        backend._rows = lambda xml: []
        with patch.object(roster_device.time, "sleep"):
            self.assertEqual(backend._ready_rows(), [])
        with self.assertRaises(StopIteration):
            next(frames)

    def test_select_can_reach_beyond_twelve_small_swipes(self):
        backend, state = self.backend([[self.row("张" + str(i))] for i in range(15)])
        selected = []
        backend.d = SimpleNamespace(click=lambda *args: selected.append(args))
        backend.count = lambda: len(selected)
        with patch("device_checks.wait_count", side_effect=lambda fn, **kw: fn()):
            backend.select("张14\x1f班级")
        self.assertEqual(len(selected), 1)
        self.assertEqual(state["page"], 14)

    def test_failed_selection_never_reclicks(self):
        backend, state = self.backend([[self.row("张甲")]])
        clicks = []
        backend.d = SimpleNamespace(click=lambda *args: clicks.append(args))
        backend.count = lambda: 0
        def wait(fn, **kwargs):
            if "expected" in kwargs:
                raise RuntimeError("人数没有 +1")
            return fn()
        with patch("device_checks.wait_count", side_effect=wait), self.assertRaises(RuntimeError):
            backend.select("张甲\x1f班级")
        self.assertEqual(len(clicks), 1)

    def test_credit_returns_to_top_of_current_search(self):
        backend, state = self.backend([[self.row("张" + str(i))] for i in range(7)])
        backend.task = "credit"
        state["page"] = 6
        def up(device, toward_top=False):
            self.assertTrue(toward_top)
            state["page"] = max(0, state["page"]-1)
        with patch.object(makeup, "_scroll_people", side_effect=up):
            roster_device.DeviceBackend._back_to_top(backend)
        self.assertEqual(state["page"], 0)

    def test_input_roster_continues_after_missing_people(self):
        for task in ("admit", "credit"):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as temporary:
                folder = Path(temporary)
                source = workbook(folder / "名单.xlsx", [["姓名", "学号"],
                    ["李甲", "20230000001"], ["王乙", "20230000002"], ["张丙", "20230000003"]])
                backend = FakeBackend([Candidate("last", "张丙", "20230000003")])
                log = io.StringIO()
                with patch.object(roster_workflow, "wait_count", side_effect=lambda fn, **kw: fn()), \
                     contextlib.redirect_stdout(log):
                    result = roster_workflow.run_task(source, task, "目标活动", folder,
                        score="0.5" if task == "credit" else None, real=True, confirmed=True,
                        backend_factory=lambda: backend)
                self.assertFalse(result.error)
                self.assertEqual(backend.queries, ["李", "王", "张"])
                self.assertEqual(backend.clicks, ["last"])
                self.assertEqual(len(read(result.feedback_path)["反馈"])-1, 3)
                self.assertIn("名单处理完成", log.getvalue())


if __name__ == "__main__":
    unittest.main()
