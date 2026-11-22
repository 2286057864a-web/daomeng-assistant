"""目标已出现时不强制扫到底再回顶；完整姓名匹配后直接勾选。"""
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import roster_device as rd
import roster_workflow as rw
from verify_roster import workbook


def row(name, y=800):
    return dict(name=name, info='2024级·测试学院·测试班', cx=1300, cy=y)


class VisiblePersonTests(unittest.TestCase):
    def backend(self, pages):
        backend = rd.DeviceBackend(Mock())
        state = dict(page=0, scrolls=0)
        backend._type_search = Mock(return_value=None)
        backend._ready_rows = lambda **kw: pages[state['page']]
        def scroll():
            state['scrolls'] += 1
            state['page'] = min(state['page']+1, len(pages)-1)
        backend._swipe_down = scroll
        backend._back_to_top = Mock(side_effect=AssertionError('不应先回顶'))
        return backend, state

    def test_search_returns_without_rewinding_before_matching(self):
        backend, state = self.backend([[row('陈佳欣')]])
        hits = backend.search('20240000001')
        self.assertEqual([c.name for c in hits], ['陈佳欣'])
        backend._back_to_top.assert_not_called()

    def test_surname_stops_on_page_with_all_roster_names(self):
        backend, state = self.backend([[row('陈甲')], [row('陈佳欣')], [row('陈乙')]])
        hits = backend.search('陈', target_names={'陈佳欣'})
        self.assertIn('陈佳欣', [c.name for c in hits])
        self.assertEqual(state['page'], 1)
        self.assertEqual(state['scrolls'], 1)
        backend._back_to_top.assert_not_called()

    def test_all_group_names_required_before_ending_scan(self):
        backend, state = self.backend([[row('陈佳欣')], [row('陈甲')], [row('陈乙')]])
        hits = backend.search('陈', target_names={'陈佳欣', '陈乙'})
        self.assertEqual(state['scrolls'], 2)
        self.assertEqual({c.name for c in hits}, {'陈佳欣', '陈甲', '陈乙'})

    def test_missing_target_still_checks_to_bottom(self):
        backend, state = self.backend([[row('陈甲')], [row('陈乙')]])
        hits = backend.search('陈', target_names={'陈佳欣'})
        self.assertEqual(state['scrolls'], 4)
        self.assertNotIn('陈佳欣', [c.name for c in hits])

    def test_name_only_search_still_finds_later_duplicate(self):
        other = dict(row('陈佳欣'), info='2023级·另一个班')
        backend, state = self.backend([[row('陈佳欣')], [other]])
        hits = backend.search('陈')
        self.assertEqual(len(hits), 2)
        self.assertEqual(state['scrolls'], 4)

    def test_student_id_search_never_uses_surname_early_stop(self):
        backend, state = self.backend([[row('陈佳欣')], [row('陈乙')]])
        hits = backend.search('20240000001', target_names={'陈佳欣'})
        self.assertEqual(len(hits), 2)
        self.assertEqual(state['scrolls'], 4)

    def test_visible_student_is_clicked_before_any_scroll(self):
        backend, state = self.backend([[row('陈佳欣')]])
        clicks = []
        backend.d.click = lambda *xy: clicks.append(xy)
        backend.count = lambda: len(clicks)
        backend.select('陈佳欣\x1f2024级·测试学院·测试班')
        self.assertEqual(clicks, [(1300,800)])
        self.assertEqual(state['scrolls'], 0)

    def test_surname_then_full_name_selection_without_student_id_search(self):
        for result_name in ('陈佳欣', '陈嘉欣'):
            with self.subTest(result_name=result_name), TemporaryDirectory() as tmp:
                backend, state = self.backend([[row('陈佳欣')]])
                events = []
                selected = []
                backend.open = Mock()
                backend.input_session = nullcontext
                backend.begin_group = lambda query: None
                backend.end_group = lambda: selected.clear()
                backend.clear_selection = selected.clear
                def search(query):
                    events.append(query)
                    state['query'] = query
                backend._type_search = search
                backend._ready_rows = lambda **kw: [row(result_name)]
                # 勾选前须读完该姓氏结果，以发现后续屏幕上的同名者。
                backend._swipe_down = Mock()
                backend.d.click = lambda *xy: (selected.append(xy), events.append('勾选'))
                backend.count = lambda: len(selected)
                backend.submit = Mock(side_effect=AssertionError('演练不应提交'))
                path = workbook(Path(tmp)/'名单.xlsx', [['姓名','学号'],['陈佳欣','20240000001']])
                wait = rw.wait_count
                with (redirect_stdout(StringIO()),
                      patch.object(rw, 'wait_count', wraps=wait) as group_wait,
                      patch('device_checks.wait_count', wraps=wait) as person_wait):
                    result = rw.run_task(path, 'credit', '测试活动', Path(tmp), score='3',
                                         backend_factory=lambda: backend)
                self.assertFalse(result.error, result.error)
                self.assertEqual(group_wait.call_count, 4)
                self.assertEqual([call.kwargs for call in person_wait.call_args_list],
                                 [{}, {'expected': 1}] if result_name == '陈佳欣' else [])
                self.assertEqual(events, ['陈'] + (['勾选'] if result_name=='陈佳欣' else []))
                self.assertGreaterEqual(backend._swipe_down.call_count, 3)
                backend.submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()
