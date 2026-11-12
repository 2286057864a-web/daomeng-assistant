"""按名单录取：读「报名」tab，勾选名单里的人，校验人数，提交。

    python admit_roster.py --dry          # 只勾选+校验+取消，不提交
    python admit_roster.py --yes          # 勾选后提交（会弹确认框，自动点「确定」）
    python admit_roster.py --report out/录取结果.xlsx   # 顺便写一份结果表

安全：勾选每个人都要「录取 N 人」+1；数量对不上就停；提交前做一次总人数核对。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import guard
import uiautomator2 as u2
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parent
PKG = guard.PKG
ROSTER = str(ROOT / "data" / "代表团名单.xlsx")


def parse_rows(xml):
    """返回 [{name, info, y}]：以 layout_userhead 为一行，点击用行的中心。"""
    rows, cur = [], None
    for node in ET.fromstring(xml).iter("node"):
        rid = node.get("resource-id", "")
        text = (node.get("text") or "").strip()
        b = [int(n) for n in re.findall(r"\d+", node.get("bounds") or "")]
        if len(b) != 4:
            continue
        if rid.endswith("layout_userhead"):
            cur = {"name": "", "info": "", "y": (b[1] + b[3]) // 2}
            rows.append(cur)
        elif cur is not None and rid.endswith(":id/title") and text:
            cur["name"] = text
        elif cur is not None and rid.endswith("tv_school_college") and text:
            cur["info"] = text
    return [r for r in rows if r["name"]]


def bottom_text(d):
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        rid = (node.get("resource-id") or "").split(":id/")[-1]
        if rid == "tv_next":
            return (node.get("text") or "").strip()
    return ""


def count(d):
    m = re.search(r"(\d+)", bottom_text(d))
    return int(m.group(1)) if m else -1


def select_tab(d, text):
    el = d(text=text)
    if not el.wait(3):
        raise guard.GuardError(f"找不到 tab「{text}」")
    el.click()
    time.sleep(1.6)


def collect(d, max_scroll=45):
    seen, order, streak = set(), [], 0
    for _ in range(max_scroll):
        fresh = 0
        for r in parse_rows(d.dump_hierarchy()):
            if r["name"] not in seen:
                seen.add(r["name"])
                order.append(r)
                fresh += 1
        streak = 0 if fresh else streak + 1
        if streak >= 2:
            break
        d.swipe(720, 2300, 720, 1150, 0.35)
        time.sleep(0.65)
    return order


def load_roster(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(c or "").strip() for c in rows[0]]
    idx = {h: i for i, h in enumerate(head)}

    def col(*names):
        for n in names:
            if n in idx:
                return idx[n]
        return None

    i_name = col("姓名")
    i_team = col("代表团", "所属代表团")
    i_role = col("身份")
    i_seq = col("序号")
    out = []
    for r in rows[1:]:
        if not r or i_name is None or i_name >= len(r):
            continue
        name = str(r[i_name] or "").strip()
        team = str(r[i_team] or "").strip() if i_team is not None else ""
        role = str(r[i_role] or "").strip() if i_role is not None else ""
        seq = r[i_seq] if i_seq is not None else len(out) + 1
        if not name or not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name):
            continue
        if i_team is not None and team and not team.startswith("第"):
            continue
        out.append({"team": team, "name": name, "role": role, "seq": seq})
    return out


def to_top(d):
    """切到另一个 tab 再切回来：列表回到顶部。"""
    select_tab(d, "录取")
    select_tab(d, "报名")


def select_matched(d, want_names):
    """从顶部往下滚，遇到名单里的人就点；返回已选名字列表。"""
    picked = []
    seen = set()
    prev = count(d)
    streak = 0
    for i in range(45):
        fresh = 0
        for r in parse_rows(d.dump_hierarchy()):
            name = r["name"]
            if name not in seen:
                seen.add(name)
                fresh += 1
            if name in want_names and name not in picked:
                for x in (1000, 720):
                    d.click(x, r["y"])
                    time.sleep(0.7)
                    now = count(d)
                    if now == prev + 1:
                        break
                else:
                    raise guard.GuardError(
                        f"勾「{name}」没让计数 +1（{prev} → {now}），已停止")
                picked.append(name)
                prev = now
                print(f"   + {name}（{bottom_text(d)}）")
        streak = 0 if fresh else streak + 1
        if streak >= 2:
            break
        d.swipe(720, 2300, 720, 1150, 0.35)
        time.sleep(0.65)
    return picked


def unselect(d, names_cb):
    """再点一遍已选的人，把勾选清掉。"""
    for _ in range(45):
        for r in parse_rows(d.dump_hierarchy()):
            if r["name"] in names_cb:
                d.click(1000, r["y"])
                time.sleep(0.4)
                names_cb.discard(r["name"])
        if not names_cb:
            return
        d.swipe(720, 2300, 720, 1150, 0.35)
        time.sleep(0.6)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", default=ROSTER)
    ap.add_argument("--title", default="长征", help="活动名称关键词")
    ap.add_argument("--open", action="store_true", help="先导航进这条活动的「人员管理」")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--match-only", action="store_true", help="只报匹配人数，不勾选")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--report", default=str(ROOT / "out" / "录取结果.xlsx"))
    args = ap.parse_args(argv)

    d = u2.connect()
    guard.ensure_foreground(d, tries=20)
    if args.open:
        import admit

        admit.open_people(d, args.title)
        time.sleep(1.0)
    roster = load_roster(args.roster)
    print(f"[..] 名单 {len(roster)} 人")

    select_tab(d, "录取")
    admitted = {r["name"] for r in collect(d)}
    print(f"[..] 录取 tab 已有 {len(admitted)} 人")

    select_tab(d, "报名")
    signed = {r["name"] for r in collect(d)}
    print(f"[..] 报名 tab 共 {len(signed)} 人")

    want = {"name": x["name"] for x in roster}
    matched = [x for x in roster if x["name"] in signed and x["name"] not in admitted]
    print(f"[..] 名单里待录取（在报名、未录取）：{len(matched)} 人")
    for x in matched:
        print(f"   · {x['name']}")
    if args.match_only:
        print(f"[OK] match-only：{len(matched)} 人可录取，未勾选、未提交")
        return 0

    to_top(d)
    picked = select_matched(d, {x["name"] for x in matched})
    print(f"[..] 实勾 {len(picked)} 人，底部：{bottom_text(d)}")
    if len(picked) != len(matched):
        unselect(d, set(picked))
        raise guard.GuardError(f"勾选数 {len(picked)} != 待录取数 {len(matched)}，已全部取消")

    if args.dry or not args.yes:
        unselect(d, set(picked))
        print(f"[OK] dry-run：{len(picked)} 人可录取，已取消勾选，未提交（底部：{bottom_text(d)}）")
        return 0

    guard.begin_submit("admit_roster")
    btn = d(resourceId=PKG + ":id/tv_next")
    if not btn.wait(2):
        raise guard.GuardError("找不到「录取 N 人」按钮")
    btn.click()
    time.sleep(1.5)
    if "确定录取" not in d.dump_hierarchy():
        guard.diagnostics(d, "admit-roster-nodialog")
        raise guard.GuardError("没弹确认框，已停止（请人工核对）")
    ok = d(resourceId="android:id/button1")
    ok.wait(3)
    ok.click()
    guard.end_submit()
    time.sleep(2.5)
    print("[OK] 已提交")

    # 复核：切到录取 tab，确认这些人都进来了
    select_tab(d, "录取")
    now = {r["name"] for r in collect(d)}
    ok_names = [n for n in picked if n in now]
    print(f"[..] 提交后录取 tab 共 {len(now)} 人，本次 {len(picked)} 人中复核到 {len(ok_names)} 人")

    # 结果表
    wb = Workbook()
    ws = wb.active
    ws.title = "录取结果"
    ws.append(["序号", "代表团", "姓名", "身份", "结果", "备注"])
    done = set(ok_names)
    for x in roster:
        if x["name"] in admitted:
            res, note = "此前已录取", ""
        elif x["name"] in done:
            res, note = "本次已录取", ""
        elif x["name"] in picked and x["name"] not in done:
            res, note = "已勾选但复核未找到", "需人工核对"
        elif x["name"] in signed:
            res, note = "未录取", "在报名名单但未勾选"
        else:
            res, note = "未报名", "报名列表里没有此人"
        ws.append([x["seq"], x["team"], x["name"], x["role"], res, note])
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"[OK] 结果表：{out}")
    (ROOT / "out" / "admit-roster.json").write_text(
        json.dumps({"admitted": sorted(done), "picked": picked}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except guard.GuardError as exc:
        print(f"[FAIL] {exc}")
        sys.exit(1)
