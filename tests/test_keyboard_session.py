import contextlib
import io
import itertools
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import roster_device as rd
import roster_workflow as rw
from verify_roster import workbook


class KeyboardDevice:
    """模型：展开原键盘隐藏计数，点击搜索生效，收起键盘后恢复计数。"""
    def __init__(self):
        self.ime = "phone.keyboard/.IME"
        self.keyboard = False
        self.query = ""
        self.applied = ""
        self.switches = []
        self.actions = []

    def __call__(self, *, resourceId):
        device = self
        class Element:
            def wait(self, timeout): return True
            def click(self): device.keyboard = True
            def set_text(self, text): device.query = text
            def get_text(self): return device.query
            @property
            def exists(self): return resourceId != rd.FOOTER_ID or not device.keyboard
            @property
            def info(self): return {"text": "补签0人"}
        return Element()

    def current_ime(self): return self.ime

    def set_input_ime(self, enabled):
        raise AssertionError("不允许切换输入法")

    def shell(self, command, timeout):
        assert command == ['dumpsys', 'input_method'], "只允许读取键盘状态"
        return Mock(exit_code=0, output='mInputShown=' + str(self.keyboard).lower())

    def _must_broadcast(self, action, extras=None):
        raise AssertionError("不允许使用备用输入法广播")

    def click(self, x, y):
        assert (x, y) == (700, 1150)
        self.actions.append("搜索")
        self.applied = self.query

    def press(self, key):
        assert key == "back" and self.keyboard
        self.actions.append("收起键盘")
        self.keyboard = False

    def xml(self):
        names = [name for name in ("陈甲", "龙甲") if self.applied in name]
        xml = (f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="{self.query}"/>'
               f'<node resource-id="{rd.PKG}:id/recycleview" bounds="[0,200][800,1000]">')
        for i, name in enumerate(names):
            y = 250 + i*150
            xml += (f'<node><node resource-id="{rd.PKG}:id/title" text="{name}" bounds="[10,{y}][100,{y+30}]"/>'
                    f'<node resource-id="{rd.PKG}:id/tv_school_college" text="2026级·班级" bounds="[10,{y+40}][400,{y+70}]"/>'
                    f'<node resource-id="{rd.PKG}:id/ll_imgcheck" bounds="[600,{y}][700,{y+80}]"/></node>')
        xml += '</node>'
        if not self.keyboard:
            xml += f'<node resource-id="{rd.FOOTER_ID}" text="补签0人"/>'
        else:
            xml += '<node package="phone.keyboard" text="搜索" clickable="true" bounds="[600,1100][800,1200]"/>'
        return xml + '</hierarchy>'


class KeyboardSessionTests(unittest.TestCase):
    def backend(self):
        device = KeyboardDevice()
        backend = rd.DeviceBackend(device)
        backend._xml = device.xml
        backend.open = Mock()
        backend._swipe_down = Mock()
        backend._back_to_top = Mock()
        return backend, device

    def test_two_queries_keep_keyboard_hidden_through_collection_and_count(self):
        backend, device = self.backend()
        ticks = itertools.count(step=.05)
        with patch.object(rd.time, "sleep"), patch.object(rd.time, "monotonic", side_effect=lambda: next(ticks)):
            with backend.input_session():
                for query in ("陈", "龙"):
                    result = backend.search(query)
                    self.assertEqual([c.name for c in result], [query+"甲"])
                    self.assertFalse(device.keyboard)
                    self.assertEqual(backend.count(), 0)
                self.assertEqual(device.switches, [])
        self.assertEqual(device.ime, "phone.keyboard/.IME")
        self.assertEqual(device.actions, ["搜索", "收起键盘"] * 2)

    def test_whole_workflow_has_one_input_session(self):
        backend, device = self.backend()
        ticks = itertools.count(step=.05)
        with tempfile.TemporaryDirectory() as tmp, patch.object(rd.time, "sleep"), \
             patch.object(rd.time, "monotonic", side_effect=lambda: next(ticks)), \
             patch.object(rw, "wait_count", side_effect=lambda fn, **kw: fn()), \
             contextlib.redirect_stdout(io.StringIO()):
            source = workbook(Path(tmp)/"名单.xlsx", [["姓名"], ["陈乙"], ["龙乙"]])
            result = rw.run_task(source, "makeup", "目标活动", Path(tmp), backend_factory=lambda: backend)
        self.assertFalse(result.error, result.error)
        self.assertEqual(device.switches, [])
        self.assertEqual(device.ime, "phone.keyboard/.IME")

    def test_collection_failure_keeps_original_keyboard_and_reports_stop(self):
        backend, device = self.backend()
        backend._collect = Mock(side_effect=RuntimeError("读取失败"))
        with tempfile.TemporaryDirectory() as tmp, patch.object(rd.time, "sleep"), \
             patch.object(rw, "wait_count", side_effect=lambda fn, **kw: fn()), \
             contextlib.redirect_stdout(io.StringIO()):
            source = workbook(Path(tmp)/"名单.xlsx", [["姓名"], ["陈乙"]])
            result = rw.run_task(source, "makeup", "目标活动", Path(tmp), backend_factory=lambda: backend)
        self.assertIn("读取失败", result.error)
        self.assertEqual(device.switches, [])
        self.assertEqual(device.ime, "phone.keyboard/.IME")

    def test_failed_keyboard_hide_stops_before_reading_results(self):
        device = Mock()
        device.current_ime.return_value = "phone.keyboard/.IME"
        device.return_value.get_text.return_value = "陈"
        device.press.side_effect = RuntimeError("收起键盘失败")
        backend = rd.DeviceBackend(device)
        device.shell.return_value = Mock(exit_code=0, output='mInputShown=true')
        backend._xml = Mock(return_value=(f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="陈"/>'
            '<node package="phone.keyboard" text="搜索" clickable="true" bounds="[600,1100][800,1200]"/></hierarchy>'))
        ticks = itertools.count(step=.1)
        with patch.object(rd.time, "sleep"), patch.object(rd.time, "monotonic", side_effect=lambda: next(ticks)), self.assertRaisesRegex(RuntimeError, "收起键盘失败"):
            backend._type_search("陈")
        device.press.assert_called_once_with('back')
        self.assertTrue(all(call.args[0] == ['dumpsys', 'input_method'] for call in device.shell.call_args_list))
        device.set_input_ime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
