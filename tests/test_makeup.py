"""离线回归：不连接手机、不提交补签。"""
import itertools
import unittest
from unittest.mock import patch

import guard
import makeup


def node(rid, text="", bounds="[0,0][100,100]", children=""):
    return (f'<node resource-id="{guard.PKG}:id/{rid}" text="{text}" '
            f'bounds="{bounds}">{children}</node>')


def person(name, grade, y=100):
    return node("row", children=(
        node("title", name, f"[0,{y}][80,{y+30}]") +
        node("tv_school_college", grade + "级·测试班", f"[0,{y+30}][80,{y+60}]") +
        node("ll_imgcheck", bounds=f"[80,{y}][100,{y+60}]")))


class MakeupTests(unittest.TestCase):
    def test_activity_button_stays_in_own_card(self):
        # 相邻活动的按钮离目标标题更近，旧版按距离会误点它。
        wrong = node("card", children=node("tv_activity_name", "10月31日上午", "[0,50][100,100]") +
                     node("tv_tag_bb", "管理活动", "[0,190][100,210]"))
        target = node("card", children=node("tv_activity_name", "10月30日下午", "[0,220][100,240]") +
                      node("tv_tag_bb", "管理活动", "[0,400][100,440]"))
        hit = makeup.activity_button("<hierarchy>" + wrong + target + "</hierarchy>", "10月30日下午")
        self.assertEqual((hit["name"], hit["cy"]), ("10月30日下午", 420))

    def test_missing_button_does_not_borrow_neighbor(self):
        xml = "<hierarchy>" + node("card", children=node("tv_activity_name", "目标"))
        xml += node("card", children=node("tv_activity_name", "其他") + node("tv_tag_bb", "管理活动"))
        self.assertIsNone(makeup.activity_button(xml + "</hierarchy>", "目标"))

    def test_ambiguous_activity_stops(self):
        xml = "<hierarchy>" + node("tv_activity_name", "活动上午") + node("tv_activity_name", "活动下午") + "</hierarchy>"
        with self.assertRaises(guard.GuardError):
            makeup.activity_button(xml, "活动")

    def test_clipped_activity_button_is_not_clicked(self):
        card = node("card", children=node("tv_activity_name", "目标") +
                    node("tv_tag_bb", "管理活动", "[0,280][100,330]"))
        xml = "<hierarchy>" + node("gridview_activitys", bounds="[0,50][100,300]", children=card) + "</hierarchy>"
        self.assertIsNone(makeup.activity_button(xml, "目标"))

    def test_wrong_management_title_stops(self):
        with self.assertRaises(guard.GuardError):
            makeup.verify_activity("<hierarchy>" + node("tv_activity_name", "其他活动") + "</hierarchy>", "目标活动")

    def test_person_fields_bound_to_same_row(self):
        # 残缺行没有年级，不能借用下一行的 2023 级信息。
        clipped = node("row", children=node("title", "残缺行", "[0,60][80,90]"))
        xml = "<hierarchy>" + node("recycleview", bounds="[0,50][100,300]",
            children=clipped + person("目标", "2023")) + node("tv_next", "补签0人") + "</hierarchy>"
        rows, footer = makeup.parse_snapshot(xml)
        self.assertEqual([(r["name"], r["grade"], r["cx"]) for r in rows], [("目标", "2023", 90)])
        self.assertEqual(footer, "补签0人")

    def test_clipped_person_waits_for_next_screen(self):
        xml = "<hierarchy>" + node("recycleview", bounds="[0,50][100,140]", children=person("目标", "2023")) + "</hierarchy>"
        self.assertEqual(makeup.parse_snapshot(xml)[0], [])

    def run_walk(self, pages, initial=0, increment=True, limit=None):
        state = {"page": 0, "count": initial, "clicks": 0}

        class Device:
            def click(self, x, y):
                state["clicks"] += 1
                if increment:
                    state["count"] += 1

            def swipe(self, *args):
                scroll(self)

        def snapshot(d):
            return pages[state["page"]], f"补签{state['count']}人"

        def scroll(d):
            state["page"] = min(state["page"] + 1, len(pages)-1)

        with patch.object(makeup, "_ready_snapshot", snapshot), patch.object(makeup, "snap", snapshot), \
             patch.object(makeup, "reset_top"), patch.object(makeup, "_scroll_people", scroll), \
             patch.object(guard, "ensure_foreground", return_value=True), \
             patch.object(makeup.time, "sleep"), patch.object(makeup.time, "monotonic", side_effect=itertools.count()):
            try:
                return makeup.walk_select(Device(), "2023", limit), state
            finally:
                self.last_state = state

    @staticmethod
    def row(name, grade):
        return {"name": name, "info": grade + "级·测试班", "grade": grade, "cx": 90, "cy": 120}

    def test_two_nonmatching_pages_do_not_end_scan(self):
        picked, state = self.run_walk([[self.row("甲", "2024")], [self.row("乙", "2025")],
                                       [self.row("丙", "2023")], [self.row("丙", "2023"), self.row("丁", "2023")]])
        self.assertEqual([r["name"] for r in picked], ["丙", "丁"])
        self.assertEqual(state["clicks"], 2)

    def test_existing_selection_rejected(self):
        with self.assertRaises(guard.GuardError):
            self.run_walk([[self.row("甲", "2023")]], initial=1)
        self.assertEqual(self.last_state["clicks"], 0)

    def test_failed_click_never_retried(self):
        with self.assertRaises(guard.GuardError):
            self.run_walk([[self.row("甲", "2023")]], increment=False)
        self.assertEqual(self.last_state["clicks"], 1)

    def test_missing_footer_is_not_zero(self):
        self.assertIsNone(makeup.count_of(""))

    def test_no_matching_people_does_not_click(self):
        picked, state = self.run_walk([[self.row("甲", "2024")], [self.row("乙", "2026")]])
        self.assertEqual(picked, [])
        self.assertEqual(state["clicks"], 0)


if __name__ == "__main__":
    unittest.main()
