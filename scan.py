"""scan.py —— 只读扫描「界面 D」：列出我发布的活动（名称 + 状态），不动任何按钮。

用法：
    python scan.py                 # 自动导航：打开到梦空间 → 我的 → 我的活动 → 管理
    python scan.py --nav none      # 不从桌面导航，假定已经停在管理 tab
    python scan.py --max-scroll 20 # 最多滚 20 屏（默认 30）

输出：终端表格 + out/scan.json
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

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"
PKG = guard.PKG

STATES = ["待部落审核", "待审核", "报名中", "进行中", "已结束", "已完结"]


def canon_state(text: str) -> str:
    for s in STATES:
        if s in text:
            return "待审核" if s.startswith("待") else s
    return text.strip()


def center(bounds: str):
    nums = [int(n) for n in re.findall(r"\d+", bounds or "")]
    if len(nums) != 4:
        return None
    return (nums[0] + nums[2]) // 2, (nums[1] + nums[3]) // 2


def cards(xml: str):
    """状态和地点只取同一活动卡片，裁剪行不借用相邻活动的状态。"""
    root = ET.fromstring(xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    names = [n for n in root.iter("node")
             if n.get("resource-id", "").endswith(":id/tv_activity_name") and n.get("text", "").strip()]
    rows = []
    for name in names:
        point = center(name.get("bounds"))
        if point is None:
            continue
        row = {"name": name.get("text").strip(), "y": point[1], "state": "", "where": ""}
        parent = parents.get(name)
        while parent is not None:
            if sum(n.get("resource-id", "").endswith(":id/tv_activity_name") for n in parent.iter("node")) != 1:
                break
            for n in parent.iter("node"):
                rid, text = n.get("resource-id", ""), n.get("text", "").strip()
                if rid.endswith(":id/tv_activity_status") and text:
                    row["state"] = canon_state(text)
                elif rid.endswith(":id/tv_activity_address") and text:
                    row["where"] = text
            parent = parents.get(parent)
        rows.append(row)
    return sorted(rows, key=lambda row: row["y"])


def click_text(d, text, timeout=8):
    guard.ensure_foreground(d)
    guard.check_target(text=text)
    el = d(text=text)
    if not el.wait(timeout):
        raise guard.GuardError(f"找不到「{text}」")
    el.click()
    time.sleep(0.8)


def at_list(d, xml=None):
    """判断是不是已经停在「管理」列表（界面 D）。"""
    try:
        if xml is None:
            xml = d.dump_hierarchy()
    except Exception:
        return False
    nodes = list(ET.fromstring(xml).iter("node"))
    return any(n.get("resource-id", "").endswith(":id/text_searchcontent") for n in nodes) and any(
        n.get("resource-id", "").endswith(":id/radio_activity")
        and (n.get("checked") == "true" or n.get("selected") == "true") for n in nodes)


def wake(d):
    """熄屏/锁屏时先亮屏并解除锁屏（无密码设备）。"""
    try:
        state = d.shell("dumpsys power | grep -m1 mWakefulness") or ""
        state = str(getattr(state, "output", state))
    except Exception:
        state = ""
    if "Dozing" in state or "Asleep" in state:
        d.screen_on()
        time.sleep(1.2)
    try:
        d.shell("wm dismiss-keyguard")
        time.sleep(0.8)
    except Exception:
        pass


def navigate(d):
    """根据当前页面导航，只有应用不在前台时才启动；首页禁止盲目返回。"""
    wake(d)
    if guard.foreground(d) != PKG:
        d.app_start(PKG)
        time.sleep(1.5)
    guard.ensure_foreground(d, tries=20)
    for _ in range(10):
        xml = d.dump_hierarchy()
        nodes = list(ET.fromstring(xml).iter("node"))
        dialogs = [n for n in nodes if n.get("resource-id") == "android:id/button1"]
        if dialogs:
            messages = [n.get("text", "") for n in nodes if n.get("resource-id") == "android:id/message"]
            cancel = [n for n in nodes if n.get("resource-id") == "android:id/button2"
                      and n.get("text") == "取消" and n.get("enabled", "true") == "true"]
            if (len(dialogs) != 1 or dialogs[0].get("text") != "确定" or len(cancel) != 1
                    or len(messages) != 1 or not re.search(r"补签|录取|颁发|学分", messages[0])):
                raise guard.GuardError("手机有未处理弹窗，请在手机处理后重新开始任务，无需重启助手。")
            # 下一项任务只取消遗留确认框，不能替上一项补点确定。
            d(resourceId="android:id/button2").click()
            print("[..] 已取消上一项遗留的确认弹窗，正在返回活动列表。")
            time.sleep(.8)
            guard.ensure_foreground(d)
            continue
        if at_list(d, xml):
            print("[OK] 已到「管理」列表")
            return
        texts = {n.get("text", "") for n in nodes}
        if any(n.get("resource-id", "").endswith(":id/radio_activity") for n in nodes):
            click_text(d, "管理")
        elif "我的活动" in texts:
            click_text(d, "我的活动")
        elif "我的" in texts:
            click_text(d, "我的")
        elif any(n.get("resource-id", "").endswith(":id/home_tab_mine") for n in nodes):
            d(resourceId=PKG + ":id/home_tab_mine").click()
            time.sleep(.8)
        elif any(n.get("resource-id", "").endswith(":id/homtabs") for n in nodes):
            raise guard.GuardError("已在应用首页，但未找到「我的」入口，已停止导航。")
        elif any(n.get("resource-id", "").endswith((":id/back", ":id/iv_back", ":id/img_back")) for n in nodes):
            back = next(n for n in nodes if n.get("resource-id", "").endswith((":id/back", ":id/iv_back", ":id/img_back")))
            d(resourceId=back.get("resource-id")).click()
            time.sleep(.8)
        elif any(n.get("resource-id", "").endswith((":id/recycleview", ":id/tv_activity_name")) for n in nodes):
            d.press("back")
            time.sleep(.8)
        else:
            raise guard.GuardError("当前页面没有可识别的导航入口，已停止；请回到到梦空间首页或管理列表。")
        guard.ensure_foreground(d)
    raise guard.GuardError("导航次数已达上限，未能进入「管理」列表。")


def wait_loaded(d, timeout=15.0):
    """等列表真正加载完：有卡片且不再显示加载圈。不往上滑（会触发下拉刷新）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            xml = d.dump_hierarchy()
        except Exception:
            time.sleep(0.4)
            continue
        if "ProgressBar" not in xml and cards(xml):
            return xml
        time.sleep(0.4)
    print("  [..] 等待活动列表加载超时")
    return False


def _scroll_activities(d, xml=None, pause=1.3):
    if xml is None:
        xml = d.dump_hierarchy()
    nodes = [n for n in ET.fromstring(xml).iter("node")
             if n.get("resource-id", "").endswith(":id/gridview_activitys")]
    if len(nodes) != 1:
        raise guard.GuardError("没有唯一活动列表，已停止滚动。")
    box = [int(x) for x in re.findall(r"-?\d+", nodes[0].get("bounds", ""))]
    if len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        raise guard.GuardError("活动列表边界无效。")
    x1, y1, x2, y2 = box
    start, end = int(y1+(y2-y1)*.75), int(y1+(y2-y1)*.40)
    guard.ensure_foreground(d)
    d.swipe((x1+x2)//2, start, (x1+x2)//2, end, .45)
    time.sleep(pause)


def _search_activities(d, query):
    """使用列表自带搜索重新加载结果，不反向滑动触发刷新。"""
    guard.ensure_foreground(d)
    box = d(resourceId=PKG + ":id/text_searchcontent")
    button = d(resourceId=PKG + ":id/textSearchBtn")
    if not box.wait(6) or not button.wait(6):
        raise guard.GuardError("活动列表缺少搜索入口，已停止。")
    box.set_text(query)
    button.click()
    time.sleep(1.3)
    xml = wait_loaded(d)
    if not xml:
        raise guard.GuardError("活动搜索结果未加载完成，请检查网络或搜索条件。")
    return xml


def reset_list_top(d):
    """点列表自身返回键后重进管理；不退出应用、不反向滑动刷新。"""
    if not at_list(d):
        raise guard.GuardError("当前不在活动管理列表，不能重新进入列表。")
    back = d(resourceId=PKG + ":id/back")
    if not back.wait(5):
        raise guard.GuardError("活动列表缺少返回入口，已停止。")
    guard.ensure_foreground(d)
    back.click()
    time.sleep(.8)
    guard.ensure_foreground(d)
    navigate(d)
    return _search_activities(d, "")


def open_activity(d, keyword):
    """按完整标题与同一卡片管理按钮进入活动，供三种任务共用。"""
    from makeup import activity_button

    if not keyword or not keyword.strip():
        raise guard.GuardError("请先选择活动。")
    navigate(d)
    _search_activities(d, keyword.strip())
    for _ in range(8):
        if not wait_loaded(d):
            raise guard.GuardError("活动列表尚未加载完成。")
        button = activity_button(d.dump_hierarchy(), keyword.strip())
        if button:
            guard.ensure_foreground(d)
            d.click(button["cx"], button["cy"])
            time.sleep(1.5)
            print(f"[核对活动] {button['name']}")
            return button["name"]
        _scroll_activities(d)
    raise guard.GuardError(f"列表里没找到「{keyword}」及其管理按钮。")


def scan(d, max_scroll=30, pause=0.4):
    """逐屏向下读卡片，按活动名合并；见到第一个「已完结」就停。"""
    merged, order = {}, []
    first_page = reset_list_top(d)
    _scan_pass(d, merged, order, max_scroll, pause, first_page)
    # 只保留到第一个「已完结」为止（同一屏里它后面的卡片也一并截掉）
    cut = next((i for i, key in enumerate(order) if merged[key]["state"] == "已完结"), len(order))
    order = order[: cut + 1]
    return [merged[k] for k in order if merged[k]["state"] in ("报名中", "已结束")]


def _scan_pass(d, merged, order, max_scroll, pause, xml):
    streak = 0
    for i in range(max_scroll + 1):
        guard.ensure_foreground(d)
        fresh = 0
        page = cards(xml)
        if i and all(row["name"] in merged and all(
                not row[field] or row[field] == merged[row["name"]][field] for field in ("state", "where"))
                for row in page):
            # 翻页后可能暂时仍显示上一屏，稍候复读，再判断是否到底。
            time.sleep(1.3)
            xml = wait_loaded(d)
            if not xml:
                raise guard.GuardError("活动列表未加载完成，请重新扫描。")
            page = cards(xml)
        for c in page:
            key = c["name"]
            if key not in merged:
                merged[key] = dict(c)
                order.append(key)
                fresh += 1
            else:
                got = merged[key]
                got["state"] = got["state"] or c["state"]
                got["where"] = got["where"] or c["where"]
        streak = 0 if fresh else streak + 1
        print(f"  第 {i + 1} 屏：+{fresh} 条（累计 {len(order)}）")
        # 列表按 待审核 → 报名中 → 进行中 → 已结束 → 已完结 排序，
        # 见到第一个「已完结」就说明后面全是已完结，不用再往下读了。
        if any(item["state"] == "已完结" for item in merged.values()):
            print("  已读到第一个「已完结」，后面的都是已完结，停止扫描")
            break
        if streak >= 2:
            break
        if i == max_scroll:
            print(f"  已达到 {max_scroll} 次翻页上限，活动列表可能未扫描完整。")
            break
        _scroll_activities(d, xml, pause)
        xml = wait_loaded(d)
        if not xml:
            raise guard.GuardError("活动列表未加载完成，请重新扫描。")


def render(rows):
    width = max([len(r["name"]) for r in rows] + [8])
    print("\n序号  活动名称" + " " * max(1, width - 4) + "  状态      处理？")
    print("-" * (width + 30))
    for idx, r in enumerate(rows, 1):
        state = r["state"] or "未读到"
        if state in ("报名中", "已结束"):
            handle = "处理"
        elif state in ("待审核", "进行中", "已完结"):
            handle = "跳过"
        else:
            handle = "?"
        print(f"{idx:>4}  {r['name']:<{width}}  {state:<6}  {handle}")
    print()


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读扫描我发布的活动")
    parser.add_argument("--nav", default="auto", choices=["auto", "none"])
    parser.add_argument("--max-scroll", type=int, default=30)
    parser.add_argument("--out", default=str(OUT / "scan.json"))
    args = parser.parse_args(argv)

    import uiautomator2 as u2

    if not uiautomator2_ready():
        print("[FAIL] 没有检测到设备（adb devices 为空）")
        return 2

    d = u2.connect()
    try:
        if args.nav != "none":
            navigate(d)
        rows = scan(d, max_scroll=args.max_scroll)
    except guard.GuardError as exc:
        print(f"[FAIL] {exc}")
        guard.diagnostics(d, "scan")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        guard.diagnostics(d, "scan")
        return 1

    render(rows)
    OUT.mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    todo = [r for r in rows if r["state"] in ("报名中", "已结束")]
    print(f"[OK] 共 {len(rows)} 条，其中需要处理 {len(todo)} 条 → {args.out}")
    if todo:
        for i, r in enumerate(todo, 1):
            print(f"   {i}. {r['name']}（{r['state']}）")
    return 0


def uiautomator2_ready():
    try:
        import adbutils

        return bool(adbutils.adb.list())
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
