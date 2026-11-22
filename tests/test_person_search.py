import unittest
from unittest.mock import Mock, call, patch

from device_checks import PageNotReady
import roster_device as rd


def empty_page(extra="", search=True):
    return ('<hierarchy>' +
            (f'<node resource-id="{rd.SEARCH_ID}" text="陈"/>' if search else '') +
            f'<node resource-id="{rd.PKG}:id/empty_view">'
            f'<node resource-id="{rd.PKG}:id/tv_empty_" text="这里暂无数据，逛逛其它吧！"/>'
            '</node>' + extra + '</hierarchy>')


class PersonSearchTests(unittest.TestCase):
    def setUp(self):
        self.device = Mock()
        self.box = self.device.return_value
        self.box.wait.return_value = True
        self.box.get_text.return_value = "陈"
        self.device.current_ime.return_value = "phone.keyboard/.IME"
        self.backend = rd.DeviceBackend(self.device)
        self.backend._xml = Mock(return_value=empty_page())
        self.backend._native_search_key = Mock(return_value=(700, 1050))
        self.backend._ready_rows = Mock(return_value=[])

    def test_focus_type_and_confirm_search_in_order(self):
        with patch.object(rd.time, "sleep"):
            self.backend._type_search("陈")
        self.device.set_input_ime.assert_not_called()
        self.device._must_broadcast.assert_not_called()
        self.device.click.assert_called_once_with(700, 1050)
        self.device.press.assert_not_called()
        self.assertLess(self.device.mock_calls.index(call().set_text("陈")),
                        self.device.mock_calls.index(call.click(700, 1050)))
        self.backend._ready_rows.assert_called_once_with(timeout=12, query="陈")

    def test_consecutive_searches_use_only_their_own_verified_first_page(self):
        self.box.get_text.side_effect = ["陈", "范"]
        self.backend._ready_rows.side_effect = [
            rd.SingleScreenRows([{"name": "陈甲", "info": "测试班"}]), []]
        with patch.object(rd.time, "sleep"), patch.object(self.backend, "_swipe_down") as swipe:
            self.assertEqual([r.name for r in self.backend.search("陈")], ["陈甲"])
            self.assertEqual(self.backend.search("范"), [])
        self.assertEqual(self.backend._ready_rows.call_args_list,
                         [call(timeout=12, query="陈"), call(timeout=12, query="范")])
        swipe.assert_not_called()

    def test_input_failure_stops_without_switching(self):
        self.box.set_text.side_effect = RuntimeError("输入接口失败")
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "填写搜索框"):
            self.backend._type_search("陈")
        self.device.send_keys.assert_not_called()
        self.device.set_input_ime.assert_not_called()
        self.device.click.assert_not_called()

    def test_clear_search_confirms_empty_query(self):
        self.box.get_text.return_value = ""
        self.backend._xml = Mock(return_value=empty_page().replace('text="陈"', 'text=""'))
        with patch.object(rd.time, "sleep"):
            self.backend._clear_search()
        self.box.click.assert_called()
        self.box.set_text.assert_called_once_with("")
        self.device.click.assert_called_once_with(700, 1050)
        self.device._must_broadcast.assert_not_called()
        self.backend._ready_rows.assert_called_once_with(timeout=12, query="")

    def test_missing_search_box_does_not_press_enter(self):
        self.box.wait.return_value = False
        self.backend._clear_search()
        self.device.press.assert_not_called()
        self.device._must_broadcast.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, "找不到搜索框"):
            self.backend._type_search("陈")
        self.device.press.assert_not_called()
        self.device._must_broadcast.assert_not_called()

    def test_failed_input_does_not_confirm_old_query(self):
        self.box.set_text.side_effect = RuntimeError("输入失败")
        self.device.send_keys.side_effect = RuntimeError("备用输入也失败")
        with patch.object(rd.time, "sleep"), self.assertRaises(RuntimeError):
            self.backend._type_search("陈")
        self.device.press.assert_not_called()
        self.device._must_broadcast.assert_not_called()

    def test_explicit_empty_search_is_ready_without_footer(self):
        del self.backend._ready_rows
        self.backend._xml = Mock(return_value=empty_page())
        with patch.object(rd.time, "sleep"):
            self.assertEqual(self.backend._ready_rows(timeout=1), [])
        self.assertEqual(self.backend._xml.call_count, 2)

    def test_empty_search_count_is_zero_without_footer_info_wait(self):
        self.box.exists = False
        self.backend._xml = Mock(return_value=empty_page())
        self.assertEqual(self.backend.count(), 0)
        self.assertNotIn("info", self.box.__dict__.get("_mock_children", {}))

    def test_loading_or_wrong_page_is_not_zero(self):
        self.box.exists = False
        for xml in (empty_page('<node class="android.widget.ProgressBar"/>'),
                    empty_page(search=False),
                    f'<hierarchy><node resource-id="{rd.SEARCH_ID}"/></hierarchy>'):
            with self.subTest(xml=xml):
                self.backend._xml = Mock(return_value=xml)
                with self.assertRaises(PageNotReady):
                    self.backend.count()

    def test_loading_empty_screen_is_not_ready(self):
        del self.backend._ready_rows
        self.backend._xml = Mock(return_value=empty_page('<node class="android.widget.ProgressBar"/>'))
        with patch.object(rd.time, "monotonic", side_effect=[0, .1, 2]), \
             patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "超时"):
            self.backend._ready_rows(timeout=1)

    def test_empty_results_do_not_scroll(self):
        self.backend._ready_rows = Mock(return_value=[])
        self.backend._swipe_down = Mock()
        self.assertEqual(self.backend._collect(), [])
        self.backend._swipe_down.assert_not_called()
        with patch("makeup._scroll_people") as scroll:
            self.backend._back_to_top()
        scroll.assert_not_called()

    def test_existing_footer_count_is_preserved(self):
        self.box.exists = True
        self.box.info = {"text": "补签3人"}
        self.assertEqual(self.backend.count(), 3)

    def test_empty_query_result_finishes_without_repeating_search(self):
        self.backend._type_search = Mock()
        self.backend._collect = Mock(return_value=[])
        self.backend._back_to_top = Mock()
        self.assertEqual(self.backend.search("陈"), [])
        self.backend._type_search.assert_called_once_with("陈")
        self.backend._back_to_top.assert_not_called()

    def test_input_not_applied_does_not_search_previous_text(self):
        self.box.get_text.return_value = "陈"
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "搜索框.*龙"):
            self.backend._type_search("龙")
        self.device._must_broadcast.assert_not_called()

    def test_waits_past_stale_results_for_current_query(self):
        del self.backend._ready_rows
        def page(name):
            return (f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="卢"/>'
                    f'<node resource-id="{rd.FOOTER_ID}" text="补签0人"/>'
                    f'<node text="{name}"/></hierarchy>')
        self.backend._xml = Mock(side_effect=[page("陈甲"), page("陈甲"),
                                             page("露露卢"), page("露露卢")])
        self.backend._rows = lambda xml: [{"name": "露露卢" if "露露卢" in xml else "陈甲",
                                           "info": "班级", "cx": 100, "cy": 200}]
        with patch.object(rd.time, "sleep"):
            rows = self.backend._ready_rows(timeout=2, query="卢")
        self.assertEqual([r["name"] for r in rows], ["露露卢"])
        self.assertEqual(self.backend._xml.call_count, 4)

    def test_stale_empty_result_for_previous_query_is_not_accepted(self):
        del self.backend._ready_rows
        self.backend._xml = Mock(return_value=empty_page())
        with patch.object(rd.time, "monotonic", side_effect=[0, .1, 2]), \
             patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "超时"):
            self.backend._ready_rows(timeout=1, query="龙")

    def test_empty_result_for_current_query_completes(self):
        del self.backend._ready_rows
        self.backend._xml = Mock(return_value=empty_page().replace('text="陈"', 'text="龙"'))
        with patch.object(rd.time, "sleep"):
            self.assertEqual(self.backend._ready_rows(timeout=1, query="龙"), [])

    def test_clear_accepts_search_hint_instead_of_empty_text(self):
        self.box.get_text.return_value = "搜索"
        self.backend._xml = Mock(return_value=empty_page().replace('text="陈"', 'text="搜索" hint="搜索"'))
        with patch.object(rd.time, "sleep"):
            self.backend._clear_search()
        self.device.click.assert_called_once_with(700, 1050)
        self.device._must_broadcast.assert_not_called()

    def test_clear_rejects_remaining_previous_query(self):
        self.box.get_text.return_value = "龙"
        self.backend._xml = Mock(return_value=empty_page().replace('text="陈"', 'text="龙" hint="搜索"'))
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "空查询"):
            self.backend._clear_search()
        self.device._must_broadcast.assert_not_called()

    def test_empty_query_page_accepts_hint_text(self):
        del self.backend._ready_rows
        self.backend._xml = Mock(return_value=empty_page().replace('text="陈"', 'text="搜索" hint="搜索"'))
        with patch.object(rd.time, "sleep"):
            self.assertEqual(self.backend._ready_rows(timeout=1, query=""), [])

    def test_original_keyboard_unchanged_after_success(self):
        with patch.object(rd.time, "sleep"):
            self.backend._type_search("陈")
        self.device.shell.assert_not_called()
        self.device.set_input_ime.assert_not_called()

    def test_original_keyboard_unchanged_after_input_failure(self):
        self.box.set_text.side_effect = RuntimeError("输入失败")
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "填写搜索框"):
            self.backend._type_search("陈")
        self.device.shell.assert_not_called()
        self.device.set_input_ime.assert_not_called()

    def test_original_keyboard_unchanged_after_search_timeout(self):
        self.backend._ready_rows.side_effect = RuntimeError("搜索超时")
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "搜索超时"):
            self.backend._type_search("陈")
        self.device.shell.assert_not_called()

    def test_unknown_original_keyboard_prevents_switching(self):
        self.device.current_ime.return_value = ""
        with self.assertRaisesRegex(RuntimeError, "当前输入法"):
            self.backend._type_search("陈")
        self.device.set_input_ime.assert_not_called()

    def test_existing_automation_keyboard_is_not_replaced_with_guessed_keyboard(self):
        self.device.current_ime.return_value = "com.github.uiautomator/.AdbKeyboard"
        with patch.object(rd.time, "sleep"):
            self.backend._type_search("陈")
        self.device.shell.assert_not_called()

    def test_typed_search_word_is_not_mistaken_for_hint(self):
        self.box.get_text.return_value = "搜索"
        self.backend._xml = Mock(return_value=empty_page().replace('text="陈"', 'text="搜索" hint="姓名"'))
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "空查询"):
            self.backend._clear_search()
        self.device._must_broadcast.assert_not_called()

    def test_session_itself_does_not_modify_input_method(self):
        with self.backend.input_session():
            pass
        self.device.current_ime.assert_called_once()
        self.device.shell.assert_not_called()
        self.device.set_input_ime.assert_not_called()

    def test_search_never_uses_automation_keyboard_commands(self):
        with patch.object(rd.time, "sleep"):
            self.backend._type_search("陈")
        self.device.send_action.assert_not_called()
        self.device._must_broadcast.assert_not_called()
        self.device.send_keys.assert_not_called()

    def test_rejected_search_click_stops_without_changing_keyboard(self):
        self.device.click.side_effect = RuntimeError("手机端拒绝搜索动作")
        with patch.object(rd.time, "sleep"), self.assertRaisesRegex(RuntimeError, "手机端拒绝"):
            self.backend._type_search("陈")
        self.backend._ready_rows.assert_not_called()
        self.device.shell.assert_not_called()


if __name__ == "__main__":
    unittest.main()
