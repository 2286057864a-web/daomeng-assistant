"""分组衔接直接替换查询，不提交空查询等待“全部人员”。"""
import unittest
from unittest.mock import Mock, patch

import roster_device as rd
from test_keyboard_session import KeyboardDevice


class RejectEmptySearchDevice(KeyboardDevice):
    def __init__(self):
        super().__init__()
        self.searches = []

    def click(self, x, y):
        self.searches.append(self.query)
        if not self.query:
            raise RuntimeError('空搜索不能用于开始或结束分组')
        super().click(x, y)


class NoEmptySearchTests(unittest.TestCase):
    def backend(self, task):
        device = RejectEmptySearchDevice()
        backend = rd.DeviceBackend(device)
        backend.task = task
        backend._xml = device.xml
        backend.count = Mock(return_value=0)
        backend._swipe_down = Mock()
        return backend, device

    def test_two_groups_only_submit_the_requested_queries(self):
        for task in ('makeup', 'admit', 'credit'):
            with self.subTest(task=task), patch.object(rd.time, 'sleep'):
                backend, device = self.backend(task)
                if task == 'credit':
                    backend._xml = lambda: device.xml().replace('补签0人', '颁发0人')
                for query in ('陈', '龙'):
                    backend.begin_group(query)
                    hits = backend.search(query)
                    self.assertEqual([c.name for c in hits], [query+'甲'])
                    backend.end_group()
                self.assertEqual(device.searches, ['陈', '龙'])
                self.assertEqual(device.query, '龙')
                self.assertEqual(device.switches, [])

    def test_begin_group_rejects_existing_selection_without_keyboard_actions(self):
        backend, device = self.backend('credit')
        backend.count.return_value = 1
        with self.assertRaisesRegex(RuntimeError, '已有 1 人'):
            backend.begin_group('陈')
        self.assertEqual(device.searches, [])
        self.assertFalse(device.keyboard)

    def test_end_group_does_not_submit_blank_query(self):
        for task in ('makeup', 'admit', 'credit'):
            with self.subTest(task=task):
                backend, device = self.backend(task)
                device.query = device.applied = '陈'
                backend.end_group()
                self.assertEqual(device.searches, [])
                self.assertEqual(device.query, '陈')


if __name__ == '__main__':
    unittest.main()
