import io
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from zipfile import ZipFile

import admit
import credit
import errcatch
import gui
import makeup
import scan


class ReportedBugs(unittest.TestCase):
    def test_home_navigates_without_back_or_app_restart(self):
        device = Mock()
        pages = ['<hierarchy><node text="我的"/></hierarchy>',
                 '<hierarchy><node text="我的活动"/></hierarchy>',
                 '<hierarchy><node text="管理" resource-id="app:id/radio_activity"/></hierarchy>',
                 '<hierarchy><node resource-id="app:id/text_searchcontent"/>'
                 '<node resource-id="app:id/radio_activity" checked="true"/></hierarchy>']
        state = {"page": 0}
        device.dump_hierarchy.side_effect = lambda: pages[state["page"]]
        def click(d, text, **kw):
            state["page"] += 1
        with patch.object(scan, "wake"), patch.object(scan.guard, "foreground", return_value=scan.PKG), \
             patch.object(scan.guard, "ensure_foreground"), patch.object(scan, "click_text", side_effect=click):
            scan.navigate(device)
        device.press.assert_not_called()
        device.app_start.assert_not_called()
        self.assertEqual(state["page"], 3)

    def test_unknown_page_does_not_blindly_back_out(self):
        device = Mock()
        device.dump_hierarchy.return_value = '<hierarchy><node text="未知页面"/></hierarchy>'
        with patch.object(scan, "wake"), patch.object(scan.guard, "foreground", return_value=scan.PKG), \
             patch.object(scan.guard, "ensure_foreground"), self.assertRaises(scan.guard.GuardError):
            scan.navigate(device)
        device.press.assert_not_called()
        device.app_start.assert_not_called()

    def test_participation_is_not_management_list(self):
        device = Mock()
        device.dump_hierarchy.return_value = '<hierarchy><node resource-id="app:id/text_searchcontent"/>' \
            '<node resource-id="app:id/radio_activity" checked="false"/></hierarchy>'
        self.assertFalse(scan.at_list(device))

    def test_reset_activity_list_never_uses_back(self):
        device = Mock()
        with patch.object(scan, "_search_activities") as search, \
             patch.object(scan, "at_list", return_value=True), patch.object(scan, "navigate") as navigate, \
             patch.object(scan.guard, "ensure_foreground"), patch.object(scan.time, "sleep"), \
             patch.object(scan, "_scroll_activities") as scroll:
            scan.reset_list_top(device)
        device.press.assert_not_called()
        scroll.assert_not_called()
        search.assert_called_once_with(device, "")
        navigate.assert_called_once_with(device)

    def test_activity_search_clicks_search_without_any_swipe(self):
        device = Mock()
        with patch.object(scan.guard, "ensure_foreground"), patch.object(scan.time, "sleep"), \
             patch.object(scan, "wait_loaded", return_value=True):
            scan._search_activities(device, "目标活动")
        device.swipe.assert_not_called()
        device.press.assert_not_called()
        device.return_value.set_text.assert_called_once_with("目标活动")
        device.return_value.click.assert_called_once()

    def test_scan_filters_skipped_but_stops_at_completed(self):
        def fill(d, merged, order, *args):
            for i, state in enumerate(["待审核", "进行中", "报名中", "已结束", "已完结", "已结束"]):
                name = str(i)
                merged[name] = {"name": name, "state": state}
                order.append(name)
        with patch.object(scan, "reset_list_top"), patch.object(scan, "wait_loaded", return_value=True), \
             patch.object(scan, "_scan_pass", side_effect=fill):
            self.assertEqual([r["name"] for r in scan.scan(object())], ["2", "3"])

    def test_future_years_are_available(self):
        self.assertIn("2027级", gui.grade_choices(2026))
        self.assertIn("2028级", gui.grade_choices(2027))
        self.assertLessEqual(len(gui.grade_choices(2030)), 6)

    def test_floating_publish_button_blocks_management_click(self):
        xml = '<hierarchy><node resource-id="app:id/gridview_activitys" bounds="[0,0][100,600]">' \
            '<node><node resource-id="app:id/tv_activity_name" text="目标" bounds="[0,200][100,250]"/>' \
            '<node resource-id="app:id/tv_tag_bb" text="管理活动" bounds="[40,400][100,450]"/>' \
            '</node></node><node text="发布活动" resource-id="app:id/btn_mangeractivity" bounds="[30,390][90,460]"/></hierarchy>'
        self.assertIsNone(makeup.activity_button(xml, "目标"))

    def test_grade_button_opens_selected_activity_before_reading(self):
        events = []
        app = SimpleNamespace(running=False, selected=lambda: {"name": "10月30日上午", "state": "已结束"},
                              cb_grade=SimpleNamespace(get=lambda: "2040级"), connect=lambda: "device",
                              run=lambda fn, **kw: fn(), _check_stop=lambda: None, _work_result=Mock(), _post=Mock(), _show_work_result=Mock())
        with patch.object(makeup, "open_unsigned", side_effect=lambda *a: events.append(("open", a)) or True), \
             patch.object(gui.work_report, "WorkReport"), \
             patch.object(makeup, "walk_select", side_effect=lambda *a, **kw: events.append(("walk", kw["want"]))):
            gui.App._select_makeup(app)
        self.assertEqual(events, [("open", ("device", "10月30日上午")), ("walk", "2040")])

    def test_makeup_top_never_clicks_signed_tab(self):
        device = Mock()
        device.return_value.wait.return_value = True
        with patch.object(makeup.guard, "ensure_foreground"), \
             patch.object(makeup, "_ready_snapshot", return_value=([], "补签0人")), \
             patch.object(makeup, "_scroll_people") as scroll:
            makeup.reset_top(device)
        self.assertEqual(device.call_args_list, [unittest.mock.call(text="未签到")])
        self.assertEqual(scroll.call_count, 3)

    def test_people_and_credit_use_same_card_navigation(self):
        device = Mock()
        with patch.object(scan, "open_activity") as enter, patch.object(admit.guard, "ensure_foreground"), \
             patch.object(admit, "find", return_value={"x": 100, "y": 200}), patch.object(admit.time, "sleep"):
            admit.open_people(device, "所选报名活动")
            enter.assert_called_once_with(device, "所选报名活动")
        with patch.object(scan, "open_activity") as enter, patch.object(scan, "click_text"), \
             patch.object(credit.time, "sleep"):
            credit.enter_credit(device, "所选学分活动")
            enter.assert_called_once_with(device, "所选学分活动")

    def test_gui_write_survives_in_exported_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            app = SimpleNamespace(log=Mock())
            with patch.object(gui, "ROOT", folder), patch.object(errcatch, "ROOT", folder), \
                 patch.object(errcatch, "OUT", folder / "out"), patch.object(errcatch, "_env_info", return_value="测试环境"):
                gui.App.write(app, "实际任务异常：未签到列表超时\n")
                package = errcatch.export_package()
                with ZipFile(package) as z:
                    self.assertIn("实际任务异常", z.read("out/gui.log").decode("utf8"))
                    self.assertIn("实际任务异常", z.read("out/gui-最近200行.log").decode("utf8"))

    def test_task_output_is_queued_before_completion_and_failure_recorded(self):
        app = SimpleNamespace(running=False, _q=queue.Queue(), write=Mock(), _done=Mock(),
                              _stop_event=gui.threading.Event(), btn_stop=Mock())
        app._post = lambda fn, *args: app._q.put((fn, args))
        class ImmediateThread:
            def __init__(self, target, **kw): self.target = target
            def start(self): self.target()
        def job():
            print("已经进入目标活动")
            self.assertFalse(app._q.empty())
            raise RuntimeError("测试异常")
        with patch.object(gui.threading, "Thread", ImmediateThread), patch.object(errcatch, "record") as record:
            gui.App.run(app, job)
        record.assert_called_once()
        contents = []
        while not app._q.empty():
            fn, args = app._q.get()
            contents.extend(str(a) for a in args)
        self.assertIn("已经进入目标活动", "".join(contents))
        self.assertIn("测试异常", "".join(contents))


if __name__ == "__main__":
    unittest.main()
