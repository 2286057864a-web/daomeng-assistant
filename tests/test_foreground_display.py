"""投屏/多显示区域下的前台检测；不连接手机。"""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import guard


class ForegroundDisplayTests(unittest.TestCase):
    def test_empty_virtual_display_does_not_hide_main_display(self):
        device = Mock()
        device.shell.return_value = SimpleNamespace(output=(
            'Display: mDisplayId=23\n  mCurrentFocus=null\n'
            'mImeWindow=null  Display: mDisplayId=0 (organized)\n'
            'mCurrentFocus=Window{abc u0 com.jingcai.apps.qualitydev/.Main}'))
        device.app_current.return_value = {"package": "stale.app", "activity": ".Old"}
        self.assertEqual(guard.foreground_info(device), {"package": guard.PKG, "activity": ".Main"})
        device.app_current.assert_not_called()
        self.assertNotIn('-m1', device.shell.call_args.args[0])
        self.assertLessEqual(device.shell.call_args.kwargs['timeout'], 5)

    def test_main_display_wins_over_nonempty_virtual_display(self):
        device = Mock()
        device.shell.return_value = (
            'Display: mDisplayId=23\nmCurrentFocus=Window{a u0 virtual.app/.Main}\n'
            'Display: mDisplayId=0\nmCurrentFocus=Window{b u0 main.app/.Main}')
        self.assertEqual(guard.foreground_info(device)['package'], 'main.app')

    def test_unfocused_main_display_does_not_borrow_virtual_focus(self):
        device = Mock()
        device.shell.return_value = (
            'Display: mDisplayId=23\nmCurrentFocus=Window{a u0 virtual.app/.Main}\n'
            'Display: mDisplayId=0\nmCurrentFocus=null')
        self.assertEqual(guard.foreground_info(device)['package'], '')
        device.app_current.assert_not_called()

    def test_legacy_single_display_output_still_works(self):
        device = Mock()
        device.shell.return_value = ('mCurrentFocus=Window{a u0 main.app/.Main}', 0)
        self.assertEqual(guard.foreground_info(device)['package'], 'main.app')

    def test_shell_failure_does_not_use_unbounded_fallback(self):
        device = Mock()
        device.shell.side_effect = TimeoutError('query timed out')
        self.assertEqual(guard.foreground_info(device), {"package": "", "activity": ""})
        device.app_current.assert_not_called()

    def test_multiple_unlabelled_focuses_are_ambiguous(self):
        device = Mock()
        device.shell.return_value = ('mCurrentFocus=Window{a u0 first.app/.Main}\n'
                                     'mCurrentFocus=Window{b u0 second.app/.Main}')
        self.assertEqual(guard.foreground_info(device)['package'], '')


if __name__ == '__main__':
    unittest.main()
