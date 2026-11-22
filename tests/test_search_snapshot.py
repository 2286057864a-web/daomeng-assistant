"""搜索动作后短暂缺少控件，不能据此断言用户已经离页。"""
import itertools
import unittest
from unittest.mock import Mock, patch

import roster_device as rd
from device_checks import PageNotReady
from test_keyboard_session import KeyboardDevice
from test_search_keyboard_ready import KEYBOARD, closed_page


class SearchSnapshotTests(unittest.TestCase):
    def run_ready(self, backend, query='范'):
        ticks=itertools.count(step=.1)
        with patch.object(rd.time, 'sleep'), patch.object(rd.time, 'monotonic', side_effect=lambda: next(ticks)):
            return backend._ready_rows(timeout=3, query=query)

    def test_incomplete_snapshot_before_results_recovers(self):
        backend=rd.DeviceBackend(Mock()); backend.task='credit'
        pages=iter(['<hierarchy/>', '<hierarchy><node class="android.widget.ProgressBar"/></hierarchy>'])
        backend._xml=lambda: next(pages, closed_page())
        self.assertEqual(len(self.run_ready(backend)),2)
        backend.d.press.assert_not_called()
        backend.d.click.assert_not_called()

    def test_full_search_recovers_first_post_click_snapshot_without_search_box(self):
        class UpdatingDevice(KeyboardDevice):
            missing=0
            def click(self,x,y):
                super().click(x,y)
                self.missing=2
            def xml(self):
                if self.missing:
                    self.missing-=1
                    return '<hierarchy><node package="phone.keyboard"/></hierarchy>'
                return super().xml()
        device=UpdatingDevice(); backend=rd.DeviceBackend(device); backend._xml=device.xml
        ticks=itertools.count(step=.05)
        with patch.object(rd.time,'sleep'), patch.object(rd.time,'monotonic',side_effect=lambda:next(ticks)):
            backend._type_search('陈')
        self.assertEqual(device.actions,['搜索','收起键盘'])
        self.assertEqual(device.applied,'陈')

    def test_query_temporarily_missing_never_triggers_back_until_restored(self):
        backend=rd.DeviceBackend(Mock()); backend.task='credit'
        backend._original_ime='com.baidu.input_mi/.ImeService'
        pages=iter([KEYBOARD.replace('text="范"','text=""')])
        backend._xml=lambda: next(pages, closed_page())
        self.assertEqual(len(self.run_ready(backend)),2)
        backend.d.press.assert_not_called()

    def test_persistently_wrong_query_stops_without_search_or_back(self):
        backend=rd.DeviceBackend(Mock()); backend.task='credit'
        backend._xml=lambda: closed_page().replace('text="范"','text="龙"')
        with self.assertRaises(RuntimeError):self.run_ready(backend)
        backend.d.press.assert_not_called();backend.d.click.assert_not_called()

    def test_begin_group_waits_for_count_instead_of_failing_first_frame(self):
        backend=rd.DeviceBackend(Mock())
        backend.count=Mock(side_effect=[PageNotReady('底栏加载中'),0,0])
        with patch('device_checks.time.sleep'):
            backend.begin_group('范')
        self.assertEqual(backend.count.call_count,3)
        backend.d.press.assert_not_called();backend.d.click.assert_not_called()

    def test_begin_group_still_rejects_existing_selection(self):
        backend=rd.DeviceBackend(Mock());backend.count=Mock(return_value=1)
        with patch('device_checks.time.sleep'),self.assertRaisesRegex(RuntimeError,'已有 1 人'):
            backend.begin_group('范')
        backend.d.click.assert_not_called()
