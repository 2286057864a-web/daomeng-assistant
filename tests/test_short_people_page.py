import unittest
from unittest.mock import Mock, patch

import makeup
from roster_device import DeviceBackend, PKG


def page(verb="录取", *, end=1000, scroll="false", extra="", loading=False, count=0):
    def node(rid, text="", box="[0,0][100,40]", children="", attrs=""):
        return f'<node resource-id="{PKG}:id/{rid}" text="{text}" bounds="{box}" {attrs}>{children}</node>'

    people = []
    for i, name in enumerate(("唐甲", "唐乙", "唐丙", "唐丁")):
        y = 100 + i * 100
        fields = (node("title", name, f"[10,{y+10}][60,{y+35}]") +
                  node("tv_school_college", "2025级·测试班", f"[10,{y+40}][60,{y+70}]") +
                  node("ll_imgcheck", box=f"[75,{y+15}][95,{y+75}]"))
        people.append(node("row", box=f"[0,{y}][100,{y+100}]", children=fields))
    listing = node("recycleview", box=f"[0,100][100,{end}]", children="".join(people)+extra,
                   attrs=f'scrollable="{scroll}"')
    return ('<hierarchy>' + node("text_searchcontent") + listing + node("tv_next", f"{verb}{count}人") +
            (node("loading_view") if loading else "") + '</hierarchy>')


class ShortPeoplePageTests(unittest.TestCase):
    @patch('makeup.time.sleep')
    def test_roster_and_grade_credit_do_not_swipe_short_results(self, _sleep):
        for task, verb in (("admit", "录取"), ("makeup", "补签"), ("credit", "颁发")):
            with self.subTest(task=task):
                phone = Mock()
                backend = DeviceBackend(phone)
                backend.task = task
                backend._xml = lambda: page(verb, count=phone.click.call_count)
                backend.count = lambda: phone.click.call_count
                backend._back_to_top()
                self.assertEqual(len(backend._collect(target_names={"唐缺"})), 4)
                if task == 'credit':
                    with patch('device_checks.wait_count', side_effect=lambda fn, **kw: fn()):
                        self.assertEqual(len(backend.select_credit_grade("2025")), 4)
                    self.assertEqual(phone.click.call_count, 4)
                phone.swipe.assert_not_called()

    @patch('makeup.time.sleep')
    @patch('guard.ensure_foreground')
    def test_grade_makeup_and_read_only_collection(self, _guard, _sleep):
        phone = Mock()
        phone.dump_hierarchy.side_effect = lambda: page('补签', count=phone.click.call_count)
        self.assertEqual(len(makeup.collect(phone)), 4)
        self.assertEqual(len(makeup.walk_select(phone, '2025')), 4)
        self.assertEqual(phone.click.call_count, 4)
        phone.swipe.assert_not_called()

    @patch('makeup.time.sleep')
    def test_poll_again_after_loading_and_after_geometry_changes(self, _sleep):
        backend = DeviceBackend(Mock())
        backend.task = 'admit'
        backend._xml = Mock(side_effect=[page(loading=True), page(scroll='true'), page(), page()])
        self.assertEqual(len(backend._collect()), 4)
        self.assertEqual(backend._xml.call_count, 4)
        backend.d.swipe.assert_not_called()

    def test_scrollable_clipped_full_or_incomplete_list_is_not_short(self):
        for kw in [dict(scroll='true'), dict(end=499), dict(end=505),
                   dict(extra='<node bounds="[0,500][100,600]"/>')]:
            with self.subTest(kw=kw):
                self.assertNotIsInstance(makeup.parse_snapshot(page(**kw))[0], makeup.SingleScreenRows)
