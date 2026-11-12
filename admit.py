"""admit.py —— 报名中活动的「录取」。

    python admit.py --check          只验证计数：勾 1 人 → 看「录取 N 人」变化 → 取消勾选，不提交

后续（未实现，等确认弹窗再说）：
    python admit.py --names 张三,李四   # 按姓名勾选并提交录取
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import xml.etree.ElementTree as ET

import guard
import scan

PKG = guard.PKG
TITLE = "长征"
MANAGE = "管理活动"
PEOPLE = "人员管理"


def bounds_of(node):
    nums = [int(n) for n in re.findall(r"\d+", node.get("bounds") or "")]
    return nums if len(nums) == 4 else None


def texts(d):
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        b = bounds_of(node)
        if not b:
            continue
        yield {
            "text": (node.get("text") or "").strip(),
            "id": node.get("resource-id") or "",
            "clickable": node.get("clickable") == "true",
            "x": (b[0] + b[2]) // 2, "y": (b[1] + b[3]) // 2,
        }


def find(d, pred):
    return next((n for n in texts(d) if pred(n)), None)


def goto_list_top(d, key=TITLE):
    """回到列表顶部：只在没看到标题时上滑，避免过度下拉触发刷新。"""
    for _ in range(8):
        xml = d.dump_hierarchy()
        if key in xml:
            return
        d.swipe(720, 1300, 720, 2500, 0.25)
        time.sleep(0.5)
    raise guard.GuardError(f"列表里一直看不到「{TITLE}」")


def open_people(d, key=TITLE):
    scan.open_activity(d, key)

    guard.ensure_foreground(d)
    people = find(d, lambda n: n["text"] == PEOPLE)
    if not people:
        raise guard.GuardError("E2 里没有「人员管理」")
    guard.check_target(text=PEOPLE)
    d.click(people["x"], people["y"])
    time.sleep(2.0)
    guard.ensure_foreground(d)
    print(f"[OK] 已进入「{PEOPLE}」页")


def count_text(d):
    n = find(d, lambda n: n["id"].endswith("tv_next"))
    return n["text"] if n else ""


def count(d):
    m = re.search(r"(\d+)", count_text(d))
    return int(m.group(1)) if m else -1


def rows(d):
    """报名人员的勾选行（ll_imgcheck 的 y 坐标，按屏幕顺序）。"""
    out = []
    xml = d.dump_hierarchy()
    for node in ET.fromstring(xml).iter("node"):
        if not (node.get("resource-id") or "").endswith("ll_imgcheck"):
            continue
        b = bounds_of(node)
        if b:
            out.append({"x": (b[0] + b[2]) // 2, "y": (b[1] + b[3]) // 2})
    return out


def toggle(d, row):
    d.click(row["x"], row["y"])
    time.sleep(0.8)


def check_mode(d):
    print(f"[..] 当前按钮：{count_text(d)!r}")
    if not count_text(d).startswith("录取"):
        raise guard.GuardError("没有找到底部「录取 N 人」按钮")
    rows_ = rows(d)
    print(f"[..] 本屏可勾选 {len(rows_)} 行")
    if not rows_:
        raise guard.GuardError("本屏没有可勾选的人员")

    before = count(d)
    toggle(d, rows_[0])
    after = count(d)
    print(f"[..] 勾第 1 人：{before} → {after}（{count_text(d)}）")
    if after != before + 1:
        raise guard.GuardError("勾选后计数没有 +1，停止")

    toggle(d, rows_[0])
    back = count(d)
    print(f"[..] 取消勾选：{after} → {back}（{count_text(d)}）")
    if back != before:
        raise guard.GuardError("取消勾选后计数没有回到原值")
    print("[OK] 计数校验通过（未提交任何录取）")


DIALOG_TEXT = "确定录取"


def dismiss_dialog_if_any(d):
    """如果已经停在确认弹窗上，用返回键关掉（绝不点弹窗里的按钮）。"""
    if DIALOG_TEXT in d.dump_hierarchy():
        d.press("back")
        time.sleep(1.0)
        return True
    return False


def dialog_mode(d):
    """勾 1 人 → 点「录取 1 人」→ 抓弹窗结构 → 用返回键取消 → 取消勾选。绝不点「确定」。"""
    dismiss_dialog_if_any(d)
    rows_ = rows(d)
    if not rows_:
        raise guard.GuardError("本屏没有可勾选的人员")
    toggle(d, rows_[0])
    if count(d) != 1:
        raise guard.GuardError("勾 1 人后计数不是 1")
    print(f"[..] 已勾 1 人：{count_text(d)}")

    btn = find(d, lambda n: n["id"].endswith("tv_next"))
    d.click(btn["x"], btn["y"])
    time.sleep(1.5)

    xml = d.dump_hierarchy()
    if DIALOG_TEXT not in xml:
        guard.diagnostics(d, "admit-nodialog")
        raise guard.GuardError("点了「录取 1 人」但没有出现确认弹窗，已停止")
    print("[..] 弹窗出现了，结构如下：")
    for node in ET.fromstring(xml).iter("node"):
        cls = node.get("class") or ""
        if "Button" not in cls and "TextView" not in cls:
            continue
        t = (node.get("text") or "").strip()
        rid = (node.get("resource-id") or "").split("/")[-1]
        if t or rid:
            print(f"     {cls.split('.')[-1]:<10} text={t!r:<10} id={rid!r}")
    guard.diagnostics(d, "admit-dialog")

    d.press("back")  # 只按返回，不点任何按钮
    time.sleep(1.2)
    if DIALOG_TEXT in d.dump_hierarchy():
        # 这个弹窗返回键关不掉，只能点「取消」(button2)，绝不碰「确定」(button1)
        cancel = d(resourceId="android:id/button2")
        if not cancel.wait(3):
            raise guard.GuardError("返回键没关掉弹窗，也找不到「取消」按钮")
        cancel.click()
        time.sleep(1.2)
    print("[OK] 弹窗已用返回键关闭，没有提交")

    toggle(d, rows_[0])
    print(f"[..] 已取消勾选：{count_text(d)}")


def submit_mode(d, how_many=1, yes=False):
    """真的录取：勾 N 人 → 录取 N 人 → 确认弹窗。没 --yes 就自动取消勾选退出。"""
    dismiss_dialog_if_any(d)
    rows_ = rows(d)
    if len(rows_) < how_many:
        raise guard.GuardError(f"本屏只有 {len(rows_)} 行，不够录取 {how_many} 人")

    before = count(d)
    for r in rows_[:how_many]:
        toggle(d, r)
        if count(d) != before + 1:
            raise guard.GuardError("勾选后计数没有 +1，停止")
        before = count(d)
    print(f"[..] 已勾 {how_many} 人：{count_text(d)}")

    if not yes:
        for r in rows_[:how_many]:
            toggle(d, r)
        print("[OK] 没加 --yes，已取消勾选，什么都没提交")
        return

    btn = find(d, lambda n: n["id"].endswith("tv_next"))
    d.click(btn["x"], btn["y"])
    time.sleep(1.5)
    if DIALOG_TEXT not in d.dump_hierarchy():
        guard.diagnostics(d, "admit-nodialog")
        raise guard.GuardError("没弹出确认框，已停止（可能已经提交，请人工核对）")

    guard.begin_submit("admit")
    ok = d(resourceId="android:id/button1")
    if not ok.wait(3):
        guard.diagnostics(d, "admit-nobutton")
        raise guard.GuardError("确认框里找不到「确定」按钮")
    ok.click()
    time.sleep(2.0)
    guard.end_submit("admit")
    print("[OK] 已点「确定」。请核对：该活动是否显示录取人数已更新。")


def main(argv=None):
    parser = argparse.ArgumentParser(description="录取（勾选 / 校验 / 提交）")
    parser.add_argument("--check", action="store_true", help="只验证勾选计数，不提交")
    parser.add_argument("--dialog", action="store_true", help="打开确认弹窗后立刻取消，看结构")
    parser.add_argument("--submit", action="store_true", help="真的录取")
    parser.add_argument("--count", type=int, default=1, help="录取几人（默认 1）")
    parser.add_argument("--yes", action="store_true", help="确认提交")
    args = parser.parse_args(argv)

    import uiautomator2 as u2

    if not scan.uiautomator2_ready():
        print("[FAIL] 没有检测到设备")
        return 2

    d = u2.connect()
    try:
        open_people(d)
        if args.check:
            check_mode(d)
        elif args.dialog:
            dialog_mode(d)
        elif args.submit:
            submit_mode(d, args.count, args.yes)
        else:
            print("[..] 请指定 --check / --dialog / --submit")
    except guard.GuardError as exc:
        print(f"[FAIL] {exc}")
        guard.diagnostics(d, "admit")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        guard.diagnostics(d, "admit")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())