"""积分卡片布局回归；全部使用本地 XML 和假设备。"""
from decimal import Decimal
import unittest
from unittest.mock import Mock, patch

import credit
import roster_device


def card(x, score, selected=False, checkable=True):
    return (f'<node clickable="true" bounds="[{x},260][{x+370},535]" '
            f'selected="{str(selected).lower()}">'
            f'<node text="社会实践与社会工作" bounds="[{x+25},300][{x+320},340]"/>'
            f'<node text="积分 {score}" bounds="[{x+25},390][{x+200},425]"/>'
            f'<node text="名额 500" bounds="[{x+25},470][{x+170},505]"/>'
            f'<node text="已颁发 0" bounds="[{x+200},470][{x+350},505]"/>'
            f'<node checkable="{str(checkable).lower()}" checked="{str(selected).lower()}" '
            f'bounds="[{x+300},300][{x+340},340]"/></node>')


def page(scores=("3.0", "2.0"), selected=(), enabled=False):
    return ('<hierarchy><node bounds="[0,0][864,1920]">' +
            ''.join(card(38+i*415, score, i in selected) for i, score in enumerate(scores)) +
            f'<node text="查看" clickable="true" bounds="[0,1750][432,1840]"/>'
            f'<node text="颁发" enabled="{str(enabled).lower()}" '
            'clickable="true" bounds="[432,1750][864,1840]"/></node></hierarchy>')


class CardDevice:
    def __init__(self, selected=(), stuck=False, enable=True, scores=("3.0", "2.0")):
        self.selected = set(selected)
        self.stuck, self.enable, self.scores = stuck, enable, scores
        self.clicks = []

    def dump_hierarchy(self):
        return page(self.scores, self.selected, bool(self.selected) and self.enable)

    def click(self, x, y):
        self.clicks.append((x, y))
        if y < 600 and not self.stuck:
            i = 0 if x < 432 else 1
            self.selected.symmetric_difference_update({i})


class CreditProjectTests(unittest.TestCase):
    def open(self, device, score):
        backend = roster_device.DeviceBackend(device)
        backend._expect_search_box = Mock()
        with patch.object(credit, "enter_credit"), patch.object(credit.time, "sleep"):
            backend.open(task="credit", title="测试活动", score=score)
        backend._expect_search_box.assert_called_once()

    def test_side_by_side_scores_belong_to_their_own_card(self):
        items = credit.projects(CardDevice())
        self.assertEqual([p["score"] for p in items], [Decimal("3.0"), Decimal("2.0")])
        self.assertEqual([p["name"] for p in items], ["社会实践与社会工作"]*2)

    def test_opens_left_or_right_card_before_shared_grant_button(self):
        for score, x in (("3", 223), ("2", 638)):
            with self.subTest(score=score):
                d = CardDevice()
                self.open(d, score)
                self.assertEqual(d.clicks, [(x, 397), (648, 1795)])

    def test_clears_other_selected_card_before_granting(self):
        d = CardDevice(selected=(1,))
        self.open(d, "3")
        self.assertEqual(d.selected, {0})
        self.assertEqual(d.clicks, [(638, 397), (223, 397), (648, 1795)])

    def test_already_selected_target_is_not_toggled_off(self):
        d = CardDevice(selected=(0,))
        self.open(d, "3")
        self.assertEqual(d.clicks, [(648, 1795)])

    def test_wrong_score_or_duplicate_score_stops_before_click(self):
        for scores, wanted in ((("3", "2"), "1"), (("3", "3.0"), "3")):
            with self.subTest(scores=scores):
                d = CardDevice(scores=scores)
                with self.assertRaises(RuntimeError):
                    self.open(d, wanted)
                self.assertEqual(d.clicks, [])

    def test_failed_toggle_or_disabled_grant_does_not_proceed(self):
        for d in (CardDevice(stuck=True), CardDevice(enable=False)):
            with self.subTest(device=d), self.assertRaises(RuntimeError):
                self.open(d, "3")
            self.assertFalse(any(y > 600 for x, y in d.clicks))

    def test_legacy_single_project_layout_still_opens(self):
        d = Mock()
        d.dump_hierarchy.return_value = ('<hierarchy>'
            '<node resource-id="app:id/tv_activity_name" text="旧项目" bounds="[0,100][200,140]"/>'
            '<node resource-id="app:id/tv_score" text="积分 0.50" bounds="[0,150][200,190]"/>'
            '<node text="颁发" bounds="[200,150][300,190]"/></hierarchy>')
        self.open(d, "0.5")
        d.click.assert_called_once_with(250, 170)

    def test_missing_selection_state_stops_before_clicking_shared_button(self):
        d = Mock()
        d.dump_hierarchy.return_value = page().replace('checkable="true"', 'checkable="false"')
        with self.assertRaisesRegex(RuntimeError, "选中状态"):
            self.open(d, "3")
        d.click.assert_not_called()

    def test_per_card_buttons_keep_their_own_score(self):
        d = Mock()
        xml = '<hierarchy>'
        for x, score in ((38, "3.0"), (453, "2.0")):
            xml += card(x, score)[:-7] + f'<node text="颁发" bounds="[{x},510][{x+100},535]"/></node>'
        d.dump_hierarchy.return_value = xml + '</hierarchy>'
        self.open(d, "2")
        d.click.assert_called_once_with(503, 522)

    def test_delayed_card_state_is_waited_for_without_double_click(self):
        class DelayedDevice(CardDevice):
            remaining = 0
            pending = None
            def click(self, x, y):
                self.clicks.append((x, y))
                if y < 600:
                    self.pending, self.remaining = 0 if x < 432 else 1, 3
            def dump_hierarchy(self):
                if self.remaining:
                    self.remaining -= 1
                    if not self.remaining:
                        self.selected.symmetric_difference_update({self.pending})
                return super().dump_hierarchy()
        d = DelayedDevice()
        self.open(d, "3")
        self.assertEqual(d.clicks, [(223, 397), (648, 1795)])


if __name__ == "__main__":
    unittest.main()
