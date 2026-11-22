"""credit.py —— 学分颁发（把「学分发放助手」已验证的流程 + 校验搬进来）。

流程（来自参考软件 credit 模块）：
    我的活动 → 管理 → 选活动 → 学分管理
      → 逐个学分项目：读「积分 N」→ 点积分卡片 →「颁发」
      → 人员选择页：搜 11 位学号 → 勾选（底部人数必须 +1）
      →「颁发」→ 确认弹窗（button1）→ toast「成功 N 人」
      → 返回前核对「已颁发 M 人」增量 == 本组人数

默认只演练（不点最终「颁发」），加 --real 才真的提交；每次提交前必须 --yes。
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

import guard
import scan

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"

TOAST_RE = re.compile(r"成功\s*(\d+)\s*人")
SCORE_RE = re.compile(r"积分\s*([0-9]+(?:\.[0-9]+)?)")


# ---------- 名单 ----------

def read_roster(path: str):
    """读 xlsx/csv 名单：姓名 + 学号（+ 可选 分值）。同号保留最高分。"""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        ws = load_workbook(p, read_only=True, data_only=True).active
        rows = [[("" if c is None else str(c)).strip() for c in r] for r in ws.iter_rows(values_only=True)]
    else:
        import csv

        with open(p, encoding="utf-8-sig", newline="") as fh:
            rows = [[c.strip() for c in r] for r in csv.reader(fh)]
    if not rows:
        return []
    header = rows[0]
    idx = {}
    for i, h in enumerate(header):
        if "学号" in h:
            idx["sid"] = i
        elif "姓名" in h:
            idx["name"] = i
        elif "分" in h:
            idx["score"] = i
    if "sid" not in idx:
        raise SystemExit("[FAIL] 名单缺少「学号」列")

    best = {}
    for r in rows[1:]:
        sid = re.sub(r"\D", "", r[idx["sid"]] if idx["sid"] < len(r) else "")
        if len(sid) != 11:
            continue  # 参考软件：不是 11 位学号一律跳过
        name = r[idx["name"]] if "name" in idx and idx["name"] < len(r) else ""
        score = r[idx["score"]] if "score" in idx and idx["score"] < len(r) else ""
        key = sid
        if key not in best or _num(score) > _num(best[key]["score"]):
            best[key] = {"sid": sid, "name": name, "score": score}
    return list(best.values())


def _num(s):
    m = re.search(r"\d+(?:\.\d+)?", str(s or ""))
    return float(m.group()) if m else 0.0


# ---------- 页面 ----------

def enter_credit(d, keyword: str):
    """列表 → 该活动「管理活动」→「学分管理」。"""
    scan.open_activity(d, keyword)

    scan.click_text(d, "学分管理")
    time.sleep(1.5)
    print("[OK] 已进入「学分管理」")


def projects(d):
    """按卡片容器绑定名称、积分和选择状态，兼容旧版单项目页面。"""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(d.dump_hierarchy())
    nodes = list(root.iter("node"))
    parents = {child: parent for parent in root.iter() for child in parent}
    scores = {n: Decimal(m.group(1)) for n in nodes
              if (m := SCORE_RE.fullmatch((n.get("text") or "").strip()))}
    grants = [n for n in nodes if re.sub(r"\s+", "", n.get("text") or "") == "颁发"
              and scan.center(n.get("bounds"))]

    out = []
    for score_node, score in scores.items():
        score_pos = scan.center(score_node.get("bounds"))
        if not score_pos:
            continue
        card = parents.get(score_node)
        while card is not None:
            children = list(card.iter("node"))
            if sum(n in scores for n in children) != 1:
                break  # 不借用同排另一张卡片的名称、积分或选中状态。
            names = [n for n in children if (n.get("text") or "").strip()
                     and (pos := scan.center(n.get("bounds"))) and pos[1] < score_pos[1]
                     and n.get("text").strip() not in ("查看", "颁发")]
            own_button = next((n for n in grants if n in children), None)
            if names and (own_button is not None or
                          card.get("clickable") == "true" and scan.center(card.get("bounds"))):
                title = next((n for n in names if n.get("resource-id", "").endswith("tv_activity_name")), names[0])
                checks = [n for n in children if n.get("checkable") == "true"]
                selected = any(n.get("checked") == "true" for n in checks) if checks else None
                if not checks:
                    labels = {n.get("content-desc", "") for n in children}
                    if labels & {"未选中", "未选择"}:
                        selected = False
                    if labels & {"已选中", "已选择", "选中"} or any(n.get("selected") == "true" for n in children):
                        selected = True
                button = own_button
                if button is None and len(grants) == 1:
                    button = grants[0]
                out.append(dict(name=title.get("text").strip(), score=score,
                                card=scan.center(card.get("bounds")) if own_button is None else None,
                                selected=selected,
                                marker=next((n.get("bounds") for n in children
                                             if n.get("resource-id", "").endswith(":id/img_check")), None),
                                btn=scan.center(button.get("bounds")) if button is not None else None,
                                enabled=button is not None and button.get("enabled", "true") == "true"))
                break
            card = parents.get(card)
    # 旧版单项目 XML 可能把名称、积分、按钮平铺，只有唯一项目时才可配对。
    if not out and len(scores) == 1 and len(grants) == 1:
        names = [n for n in nodes if n.get("resource-id", "").endswith("tv_activity_name")
                 and (n.get("text") or "").strip()]
        if len(names) == 1:
            out.append(dict(name=names[0].get("text").strip(), score=next(iter(scores.values())),
                            btn=scan.center(grants[0].get("bounds"))))
    unknown = [p for p in out if p.get("card") and p.get("selected") is None and p.get("marker")]
    if unknown:
        try:
            image = d.screenshot()
            for project in unknown:
                box = tuple(int(x) for x in re.findall(r"\d+", project["marker"]))
                if len(box) != 4 or not (0 <= box[0] < box[2] <= image.width
                                        and 0 <= box[1] < box[3] <= image.height):
                    continue
                crop = image.crop(box).convert("RGB")
                colors = crop.getcolors(crop.width * crop.height)
                total = crop.width * crop.height
                purple = sum(n for n, (r,g,b) in colors if b >= r and r >= g+30 and b >= g+60) / total
                gray = sum(n for n, (r,g,b) in colors
                           if max(r,g,b)-min(r,g,b) < 20 and 80 <= r <= 220) / total
                if purple >= .10:
                    project["selected"] = True
                elif purple <= .01 and gray >= .10:
                    project["selected"] = False
        except Exception:
            pass  # 无法辨认颜色时保持未知，禁止继续进入颁发。
    return out


def open_project(d, target):
    """项目选择只进入人员页；共用按钮前必须确认仅目标卡片被选中。"""
    if not target.get("btn"):
        raise RuntimeError("该项目没有「颁发」按钮")
    if not target.get("card"):
        d.click(*target["btn"])
        return

    def refresh():
        items = projects(d)
        hits = [p for p in items if p["name"] == target["name"] and p["score"] == target["score"]]
        if len(hits) != 1 or any(p.get("selected") is None for p in items):
            raise RuntimeError("无法确认积分项目的选中状态，已停止，请导出诊断包。")
        return items, hits[0]

    def toggle(item, selected):
        d.click(*item["card"])
        for _ in range(12):
            time.sleep(.25)
            items, _ = refresh()
            hits = [p for p in items if p["card"] == item["card"]]
            if len(hits) == 1 and hits[0]["selected"] is selected:
                return
        raise RuntimeError("积分项目选中状态未按预期变化，已停止。")

    items, current = refresh()
    for other in items:
        if other is not current and other["selected"]:
            toggle(other, False)
    items, current = refresh()
    if not current["selected"]:
        toggle(current, True)
    items, current = refresh()
    if ([p for p in items if p["selected"]] != [current]
            or not current.get("enabled") or not current.get("btn")):
        raise RuntimeError("未确认只选中目标积分项目或「颁发」按钮不可用，已停止。")
    print(f"[核对积分项目] {current['name']}，积分 {current['score']}")
    d.click(*current["btn"])


def select_student(d, sid: str, expect_delta=1):
    """人员选择页：搜学号 → 勾选 → 校验人数 +1。"""
    guard.check_target(text="颁发")
    box = d(resourceId="com.jingcai.apps.qualitydev:id/text_searchcontent")
    if not box.wait(5):
        raise guard.GuardError("找不到搜索框")
    box.click()
    time.sleep(0.4)
    box.set_text(sid)
    time.sleep(1.4)

    before = count(d)
    pos = first_person_row(d)
    if not pos:
        raise guard.GuardError(f"搜学号 {sid} 后没有人员行")
    d.click(pos[0], pos[1])
    time.sleep(0.9)
    after = count(d)
    if after - before != expect_delta:
        raise guard.GuardError(f"勾选后人数没有 +{expect_delta}（{before} → {after}）")
    return after


def first_person_row(d):
    """搜完学号后，第一条可点击的人员行中心（跳过顶部搜索框区域）。"""
    import xml.etree.ElementTree as ET

    for node in ET.fromstring(d.dump_hierarchy()).iter("node"):
        if node.get("clickable") != "true":
            continue
        c = scan.center(node.get("bounds"))
        if c and c[1] > 380:
            return c
    return None


def count(d):
    """底部按钮里的数字，例如「颁发 3 人」「录取 0 人」。"""
    m = re.search(r"(?:颁发|录取|补签)\s*(\d+)\s*人", d.dump_hierarchy())
    return int(m.group(1)) if m else 0


def grant_one(d, project, students, real=False, yes=False):
    print(f"[..] 项目「{project['name']}」积分 {project['score']}，{len(students)} 人")
    for s in students:
        print(f"     学号 {s['sid']}  姓名 {s['name']}  分 {s['score']}")

    if not real:
        print("    [演练] 不点「颁发」")
        return {"ok": len(students), "real": False}

    if not yes:
        raise guard.GuardError("正式模式必须显式加 --yes")

    if not project["btn"]:
        raise guard.GuardError("这个项目上没有「颁发」按钮")
    guard.begin_submit("credit")
    d.click(*project["btn"])
    time.sleep(1.5)

    for s in students:
        select_student(d, s["sid"])

    ok_btn = d(text="颁发")
    if not ok_btn.wait(3):
        raise guard.GuardError("人员页找不到「颁发」按钮")
    ok_btn.click()
    time.sleep(1.4)

    confirm = d(resourceId="android:id/button1")
    if confirm.wait(4):
        confirm.click()
        time.sleep(1.6)

    xml = d.dump_hierarchy()
    m = TOAST_RE.search(xml)
    n = int(m.group(1)) if m else 0
    print(f"    [提交] toast：成功 {n} 人")
    guard.end_submit("credit")
    return {"ok": n, "real": True}


def main(argv=None):
    ap = argparse.ArgumentParser(description="学分颁发（整合参考软件流程）")
    ap.add_argument("--roster", required=False, help="名单 xlsx/csv（姓名/学号/分值）")
    ap.add_argument("--open", default="", help="活动名关键词；不给就只列出项目")
    ap.add_argument("--project", default="", help="只处理名字包含该词的学分项目")
    ap.add_argument("--real", action="store_true", help="真的点颁发（默认演练）")
    ap.add_argument("--yes", action="store_true", help="确认正式提交")
    args = ap.parse_args(argv)

    if not scan.uiautomator2_ready():
        print("[FAIL] 没有检测到设备")
        return 2

    import uiautomator2 as u2

    d = u2.connect()
    try:
        if args.open:
            enter_credit(d, args.open)
        items = projects(d)
        if not items:
            print("[FAIL] 没读到学分项目，可能不在「学分管理」页")
            return 1
        print(f"[OK] 读到 {len(items)} 个学分项目：")
        for i, p in enumerate(items, 1):
            print(f"     {i}. {p['name']}（积分 {p['score']}）{'有颁发按钮' if p['btn'] else '无按钮'}")

        if not args.roster:
            return 0
        students = read_roster(args.roster)
        print(f"[OK] 名单 {len(students)} 人（同号保留最高分）")
        target = [p for p in items if args.project and args.project in p["name"]] or items[:1]
        result = grant_one(d, target[0], students, real=args.real, yes=args.yes)
        print(f"[OK] 结果：{result}")
        return 0
    except guard.GuardError as exc:
        print(f"[FAIL] {exc}")
        guard.diagnostics(d, "credit")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        guard.diagnostics(d, "credit")
        return 1


if __name__ == "__main__":
    sys.exit(main())
