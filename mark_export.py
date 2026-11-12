"""以「代表团名单」为母表，标出哪些人出现在平台导出表里。纯本地比对。"""

import argparse
import re
from collections import Counter
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = Path(__file__).resolve().parent
DEFAULT_WANT = str(ROOT / "data" / "代表团名单.xlsx")


def load_want(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb["名单总表"] if "名单总表" in wb.sheetnames else wb.worksheets[0]
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 or not r or len(r) < 3:
            continue
        team, name, role = str(r[1] or "").strip(), str(r[2] or "").strip(), str(r[3] or "")
        if team.startswith("第") and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name):
            out.append({"seq": r[0], "team": team, "name": name, "role": role})
    return out


def load_got(path):
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
    out = []
    for r in rows[1:]:
        if not r or i_name is None or i_name >= len(r):
            continue
        name = str(r[i_name] or "").strip()
        if not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", name):
            continue
        get = lambda k: (str(r[idx[k]] or "").strip() if k in idx and idx[k] < len(r) else "")
        out.append({
            "name": name,
            "sid": get("学号"),
            "cls": get("班级"),
            "grade": get("年级"),
            "sign": get("报名状态"),
            "admit": get("录取状态"),
            "activity": get("活动名称"),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--want", default=DEFAULT_WANT, help="母表（代表团名单）")
    ap.add_argument("--got", required=True, help="平台导出表（录取列表）")
    ap.add_argument("--out", default=str(ROOT / "out" / "代表团名单-标记结果.xlsx"))
    args = ap.parse_args()

    want = load_want(args.want)
    got = load_got(args.got)
    by_name = {}
    for g in got:
        by_name.setdefault(g["name"], []).append(g)

    wb = Workbook()
    ws = wb.active
    ws.title = "标记结果"
    ws.append(["序号", "代表团", "姓名", "身份", "出现在导出表", "学号", "班级", "录取状态"])
    hit = 0
    for w in want:
        gs = by_name.get(w["name"], [])
        if gs:
            hit += 1
            g = gs[0]
            row = [w["seq"], w["team"], w["name"], w["role"], "是", g["sid"], g["cls"], g["admit"]]
        else:
            row = [w["seq"], w["team"], w["name"], w["role"], "否", "", "", ""]
        ws.append(row)

    green = PatternFill("solid", fgColor="D9F2D9")
    red = PatternFill("solid", fgColor="FDE2E2")
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        f = green if row[4].value == "是" else red
        for c in row:
            c.fill = f
            c.alignment = Alignment(vertical="center", horizontal="center" if c.column != 3 else "left")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDE6F5")
    for col, wd in zip("ABCDEFGH", (7, 14, 11, 8, 14, 16, 22, 10)):
        ws.column_dimensions[col].width = wd
    ws.freeze_panes = "A2"

    want_names = {w["name"] for w in want}
    extra = [g for g in got if g["name"] not in want_names]
    ws2 = wb.create_sheet("导出表里但母表没有")
    ws2.append(["姓名", "学号", "班级", "报名状态", "录取状态"])
    for g in extra:
        ws2.append([g["name"], g["sid"], g["cls"], g["sign"], g["admit"]])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for col, wd in zip("ABCDE", (10, 16, 22, 12, 12)):
        ws2.column_dimensions[col].width = wd

    ws3 = wb.create_sheet("汇总")
    ws3.append(["项目", "人数"])
    ws3.append(["母表总人数", len(want)])
    ws3.append(["出现在导出表", hit])
    ws3.append(["母表里没有出现", len(want) - hit])
    ws3.append(["导出表总行数", len(got)])
    ws3.append(["导出表里但母表没有", len(extra)])
    ws3.append(["导出表录取状态分布", "、".join(f"{k}:{v}" for k, v in Counter(g['admit'] for g in got).items())])
    for c in ws3[1]:
        c.font = Font(bold=True)
    ws3.column_dimensions["A"].width = 30
    ws3.column_dimensions["B"].width = 36

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    print(f"母表 {len(want)} 人，导出表 {len(got)} 行，命中 {hit} 人")
    print("导出状态分布:", dict(Counter(g["admit"] for g in got)))
    print("导出里但母表没有:", len(extra), [g["name"] for g in extra])
    print("写入", out)


if __name__ == "__main__":
    main()
