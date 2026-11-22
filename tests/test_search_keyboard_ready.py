"""真实页面结构：搜索已成功，但键盘遮挡人数按钮。"""
import itertools
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

import roster_device as rd

KEYBOARD = (Path(__file__).parent/'fixtures/search_keyboard_footer_hidden.xml').read_text(encoding='utf8')


def closed_page():
    root = ET.fromstring(KEYBOARD)
    for node in list(root):
        if node.get('package') == 'com.baidu.input_mi':
            root.remove(node)
    ET.SubElement(root, 'node', {'resource-id': rd.SELECT_ALL_ID, 'text': '颁发0人'})
    return ET.tostring(root, encoding='unicode')


class SearchKeyboardReadyTests(unittest.TestCase):
    def backend(self):
        backend = rd.DeviceBackend(Mock())
        backend.task = 'credit'
        backend._original_ime = 'com.baidu.input_mi/.ImeService'
        backend.d.shell.return_value = Mock(exit_code=0, output='mInputShown=true')
        return backend

    def run_ready(self, backend):
        ticks = itertools.count(step=.1)
        with patch.object(rd.time, 'sleep'), patch.object(rd.time, 'monotonic', side_effect=lambda: next(ticks)):
            return backend._ready_rows(timeout=3, query='范')

    def test_current_page_has_results_but_no_footer_until_keyboard_closes(self):
        backend = self.backend()
        self.assertEqual([r['name'] for r in backend._rows(KEYBOARD)], ['范甲','范乙'])
        self.assertIsNone(backend._footer(KEYBOARD))
        backend._xml = lambda: closed_page() if backend.d.press.called else KEYBOARD
        self.assertEqual([r['name'] for r in self.run_ready(backend)], ['范甲','范乙'])
        backend.d.press.assert_called_once_with('back')
        backend.d.click.assert_not_called()

    def test_keyboard_appearing_after_first_snapshot_is_handled(self):
        backend = self.backend()
        pages = iter([closed_page()])
        backend._xml = lambda: next(pages, closed_page() if backend.d.press.called else KEYBOARD)
        self.assertEqual(len(self.run_ready(backend)), 2)
        backend.d.press.assert_called_once_with('back')

    def test_failed_hide_does_not_retry_back(self):
        backend = self.backend()
        backend._xml = lambda: KEYBOARD
        with self.assertRaisesRegex(RuntimeError, '键盘.*未收起'):
            self.run_ready(backend)
        backend.d.press.assert_called_once_with('back')

    def test_stale_keyboard_nodes_with_system_hidden_never_press_back(self):
        backend = self.backend()
        backend.d.shell.return_value = Mock(exit_code=0, output='mInputShown=false\nmIsInputViewShown=true')
        page = closed_page().replace('</hierarchy>', '<node package="com.baidu.input_mi"/></hierarchy>')
        backend._xml = lambda: page
        self.assertEqual(len(self.run_ready(backend)), 2)
        backend.d.press.assert_not_called()

    def test_keyboard_auto_hides_during_wait_never_gets_back(self):
        backend = self.backend()
        responses = iter(['mInputShown=true', 'mInputShown=false'])
        backend.d.shell.side_effect = lambda *a, **kw: Mock(exit_code=0, output=next(responses, 'mInputShown=false'))
        page = closed_page().replace('</hierarchy>', '<node package="com.baidu.input_mi"/></hierarchy>')
        backend._xml = lambda: page
        self.assertEqual(len(self.run_ready(backend)), 2)
        backend.d.press.assert_not_called()

    def test_unknown_system_keyboard_state_never_gets_back(self):
        for response in (Mock(exit_code=0, output='mIsInputViewShown=true'),
                         Mock(exit_code=1, output='mInputShown=true')):
            backend = self.backend()
            backend.d.shell.return_value = response
            backend._xml = lambda: KEYBOARD
            with self.assertRaisesRegex(RuntimeError, '键盘状态'):
                self.run_ready(backend)
            backend.d.press.assert_not_called()

    def test_missing_people_page_reports_observation_not_assumed_navigation(self):
        backend = self.backend()
        backend._xml = lambda: '<hierarchy/>'
        with self.assertRaisesRegex(RuntimeError, '页面恢复超时.*未读到人员搜索框'):
            self.run_ready(backend)
        backend.d.press.assert_not_called()

    def test_persistent_keyboard_is_bounded_and_not_called_search_failure(self):
        backend = self.backend()
        backend._xml = lambda: KEYBOARD
        with self.assertRaisesRegex(RuntimeError, '键盘.*未收起'):
            self.run_ready(backend)
        backend.d.press.assert_called_once_with('back')
        backend.d.click.assert_not_called()

    def test_different_query_or_page_never_gets_back_pressed(self):
        for xml in (KEYBOARD.replace('text="范"', 'text="龙"'), '<hierarchy/>'):
            with self.subTest(xml=xml[:30]):
                backend = self.backend()
                backend._xml = lambda: xml
                with self.assertRaises(RuntimeError):
                    self.run_ready(backend)
                backend.d.press.assert_not_called()

    def test_type_search_with_delayed_xml_never_sends_second_back(self):
        from test_keyboard_session import KeyboardDevice
        class DelayedDevice(KeyboardDevice):
            stale_reads = 0
            departed = False
            def press(self, key):
                if not self.keyboard:
                    self.departed = True
                    self.actions.append('误退人员页')
                    return
                super().press(key)
                self.stale_reads = 4
            def xml(self):
                if self.departed:
                    return '<hierarchy/>'
                if self.stale_reads:
                    self.stale_reads -= 1
                    self.keyboard = True
                    snapshot = super().xml()
                    self.keyboard = False
                    return snapshot
                return super().xml()
        device = DelayedDevice()
        backend = rd.DeviceBackend(device)
        backend._xml = device.xml
        ticks = itertools.count(step=.05)
        with patch.object(rd.time, 'sleep'), patch.object(rd.time, 'monotonic', side_effect=lambda: next(ticks)):
            backend._type_search('陈')
        self.assertFalse(device.departed)
        self.assertEqual(device.actions, ['搜索', '收起键盘'])

    def test_already_hidden_keyboard_never_goes_back(self):
        backend = self.backend()
        backend._xml = lambda: closed_page()
        self.assertEqual(len(self.run_ready(backend)), 2)
        backend.d.press.assert_not_called()


if __name__ == '__main__':
    unittest.main()
