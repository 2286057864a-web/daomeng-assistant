import unittest
from unittest.mock import Mock, patch

import roster_device as rd


def page(with_key=False, keyboard=True):
    key = ('<node package="phone.keyboard" text="搜索" clickable="true" '
           'bounds="[600,1000][800,1100]"/>') if with_key else ''
    area = (f'<node package="phone.keyboard" resource-id="android:id/inputArea" '
            f'bounds="[0,800][800,1200]">{key}</node>') if keyboard else ''
    return f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="邓"/>{area}</hierarchy>'


class NoImeSwitchTests(unittest.TestCase):
    def backend(self):
        device = Mock()
        device.current_ime.return_value = 'phone.keyboard/.IME'
        device.return_value.get_text.return_value = '邓'
        for name in ('set_input_ime', 'send_keys', 'send_action', '_must_broadcast', 'shell'):
            getattr(device, name).side_effect = AssertionError('禁止切换输入法或使用备用输入')
        backend = rd.DeviceBackend(device)
        backend._ready_rows = Mock(return_value=[])
        return backend, device

    def test_opaque_keyboard_uses_screenshot_search_without_switch(self):
        backend, device = self.backend()
        backend._xml = Mock(return_value=page())
        with patch('keyboard_search.find_search_key', return_value=(700, 1050)) as find, \
             patch.object(rd.time, 'sleep'):
            backend._type_search('邓')
        self.assertEqual(find.call_args.args[1], (0,800,800,1200))
        device.click.assert_called_once_with(700,1050)
        device.press.assert_not_called()
        backend._ready_rows.assert_called_once_with(timeout=12,query='邓')

    def test_failed_visual_recognition_never_switches_or_clicks(self):
        backend, device = self.backend()
        backend._xml = Mock(return_value=page())
        with patch('keyboard_search.find_search_key', side_effect=RuntimeError('无法识别搜索键')), \
             patch.object(rd.time, 'sleep'), self.assertRaisesRegex(RuntimeError,'无法识别'):
            backend._type_search('邓')
        device.click.assert_not_called()
        backend._ready_rows.assert_not_called()

    def test_input_failure_never_calls_send_keys(self):
        backend, device = self.backend()
        device.return_value.set_text.side_effect = RuntimeError('无法直接输入')
        with patch.object(rd.time, 'sleep'), self.assertRaisesRegex(RuntimeError,'输入'):
            backend._type_search('邓')
        device.send_keys.assert_not_called()
        device.set_input_ime.assert_not_called()
        device.click.assert_not_called()

    def test_keyboard_moves_during_ocr_stops_before_click(self):
        backend, device = self.backend()
        backend._xml = Mock(side_effect=[page(),page().replace('[0,800][800,1200]','[0,600][800,1000]')])
        with patch('keyboard_search.find_search_key', return_value=(700,1050)), \
             patch.object(rd.time,'sleep'), self.assertRaisesRegex(RuntimeError,'键盘.*改变'):
            backend._type_search('邓')
        device.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
