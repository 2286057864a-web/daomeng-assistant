import itertools
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import roster_device as rd


def keyboard(key=True, package='phone.keyboard', extra=''):
    key_xml = (f'<node package="{package}" text="搜索" clickable="true" '
               'bounds="[600,1000][800,1100]"/>') if key else ''
    return (f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="邓"/>'
            f'{key_xml}{extra}</hierarchy>')


class NativeSearchTests(unittest.TestCase):
    def backend(self, xml):
        device = Mock()
        device.current_ime.return_value = 'phone.keyboard/.IME'
        device.return_value.get_text.return_value = '邓'
        backend = rd.DeviceBackend(device)
        backend._xml = Mock(return_value=xml)
        backend._ready_rows = Mock(return_value=[])
        return backend, device

    def test_available_keyboard_search_key_never_switches_ime(self):
        backend, device = self.backend(keyboard())
        backend._xml.side_effect = [keyboard(), keyboard(False)]
        with patch.object(rd.time, 'sleep'):
            backend._type_search('邓')
        device.click.assert_called_once_with(700, 1050)
        device.set_input_ime.assert_not_called()
        device.shell.assert_not_called()
        device._must_broadcast.assert_not_called()
        device.press.assert_not_called()
        backend._ready_rows.assert_called_once_with(timeout=12, query='邓')

    def test_remaining_keyboard_is_handled_only_by_result_wait(self):
        backend, device = self.backend(keyboard())
        with patch.object(rd.time, 'sleep'):
            backend._type_search('邓')
        device.press.assert_not_called()
        backend._ready_rows.assert_called_once_with(timeout=12, query='邓')
        device.set_input_ime.assert_not_called()

    def test_missing_keyboard_region_stops_without_switching(self):
        backend, device = self.backend(keyboard(False))
        with patch.object(rd.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '按键区域'):
            backend._type_search('邓')
        device.set_input_ime.assert_not_called()
        device.shell.assert_not_called()
        device._must_broadcast.assert_not_called()

    def test_app_search_label_is_not_a_keyboard_action(self):
        backend, device = self.backend(keyboard(package=rd.PKG))
        with patch.object(rd.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '按键区域'):
            backend._type_search('邓')
        device.click.assert_not_called()
        device.set_input_ime.assert_not_called()

    def test_ambiguous_keyboard_actions_are_not_clicked(self):
        backend, device = self.backend(keyboard(extra=(
            '<node package="phone.keyboard" content-desc="搜索" clickable="true" '
            'bounds="[400,800][500,900]"/>')))
        with patch.object(rd.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '多个搜索键'):
            backend._type_search('邓')
        device.click.assert_not_called()
        device.set_input_ime.assert_not_called()

    def test_native_search_failure_stops_without_switching_or_retrying(self):
        backend, device = self.backend(keyboard())
        device.click.side_effect = RuntimeError('搜索按键点击失败')
        with patch.object(rd.time, 'sleep'), self.assertRaisesRegex(RuntimeError, '点击失败'):
            backend._type_search('邓')
        device.set_input_ime.assert_not_called()
        device._must_broadcast.assert_not_called()
        backend._ready_rows.assert_not_called()

    def test_actual_baidu_keyboard_exposes_bounded_input_area(self):
        from keyboard_search import keyboard_region
        xml = (Path(__file__).parent/'fixtures/opaque_keyboard.xml').read_text(encoding='utf8')
        self.assertIn('com.baidu.input_mi', xml)
        self.assertEqual(keyboard_region(xml, 'com.baidu.input_mi'), (0,2136,1440,2994))

    def test_persistently_changed_query_stops_after_read_only_wait(self):
        backend, device = self.backend(keyboard())
        del backend._ready_rows
        pages = iter([keyboard()])
        backend._xml = lambda: next(pages, keyboard().replace('text="邓"', 'text="万"'))
        ticks = itertools.count(step=.5)
        with patch.object(rd.time, 'sleep'), patch.object(rd.time, 'monotonic', side_effect=lambda: next(ticks)), self.assertRaisesRegex(RuntimeError, '页面恢复超时'):
            backend._type_search('邓')
        device.press.assert_not_called()
        device._must_broadcast.assert_not_called()
        device.click.assert_called_once_with(700, 1050)

    def test_missing_search_page_stops_after_read_only_wait_without_back(self):
        backend, device = self.backend(keyboard())
        del backend._ready_rows
        pages = iter([keyboard()])
        backend._xml = lambda: next(pages, '<hierarchy/>')
        ticks = itertools.count(step=.5)
        with patch.object(rd.time, 'sleep'), patch.object(rd.time, 'monotonic', side_effect=lambda: next(ticks)), self.assertRaisesRegex(RuntimeError, '页面恢复超时'):
            backend._type_search('邓')
        device.press.assert_not_called()
        device.click.assert_called_once_with(700, 1050)


if __name__ == '__main__':
    unittest.main()
