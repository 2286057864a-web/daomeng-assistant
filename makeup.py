"""makeup.py —— 补签（按年级 / 按名单），思路来自参考软件的「按年级补签」。

用法（先只读，再勾选，最后才提交）：
    python makeup.py --grade 2024 --dry          # 只读：列出会勾谁，不点任何东西
    python makeup.py --grade 2024 --select       # 真勾选，但不提交（可再跑 --clear 取消）
    python makeup.py --grade 2024 --submit --yes # 勾选 + 提交（不可逆，谨慎）
    python makeup.py --clear                     # 取消全部勾选
    python makeup.py --open "10月30日上午"        # 从列表自动进入该活动的「未签到」

前提：默认假定已经停在「未签到」列表；加 --open 才自动导航。
"""

from __future__ import annotations

import argparse
import re
import sys
import time

import guard
import scan

GRADE_RE = re.compile(r"^(20\d{2})级")
FOOTER_RE = re.compile(r"补签\s*(\d+)\s*人")


def parse_snapshot(xml):
    """同一人员容器内绑定姓名、年级、勾选框；残缺行不借用邻行信息。"""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    parents = {child: parent for parent in root.iter() for child in parent}

    def by_id(parent, suffix):
        return [n for n in parent.iter("node") if n.get("resource-id", "").endswith(":" + "id/" + suffix)]

    def bounds(node):
        values = [int(x) for x in re.findall(r"-?\d+", node.get("bounds", ""))]
        return values if len(values) == 4 else [0, 0, 0, 0]

    footers = by_id(root, "tv_next")
    footer = footers[0].get("text", "") if len(footers) == 1 else ""
    rows = []
    for listing in by_id(root, "recycleview"):
        left, top, right, bottom = bounds(listing)
        for name in by_id(listing, "title"):
            if not name.get("text", "").strip():
                continue
            container = parents.get(name)
            while container is not None and container is not listing:
                names = by_id(container, "title")
                infos = by_id(container, "tv_school_college")
                checks = by_id(container, "ll_imgcheck")
                if len(names) == len(infos) == len(checks) == 1:
                    fields = [name, infos[0], checks[0]]
                    if any(n.get("visible-to-user", "true") == "false" for n in fields):
                        break
                    boxes = [bounds(n) for n in fields]
                    # 姓名/年级必须完整可见，勾选框中心必须在列表内；禁止点底部提交。
                    if any(x1 >= x2 or y1 >= y2 for x1, y1, x2, y2 in boxes):
                        break
                    if any(y1 < top or y2 > bottom for _, y1, _, y2 in boxes[:2]):
                        break
                    x1, y1, x2, y2 = boxes[2]
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    if not (left < cx < right and top < cy < bottom):
                        break
                    info = infos[0].get("text", "").strip()
                    grade = GRADE_RE.search(info)
                    rows.append({"name": name.get("text").strip(), "info": info,
                                 "grade": grade.group(1) if grade else "",
                                 "cx": cx, "cy": cy})
                    break
                if len(names) > 1:
                    break
                container = parents.get(container)
    return sorted(rows, key=lambda row: row["cy"]), footer


def snap(d):
    return parse_snapshot(d.dump_hierarchy())


def _ready_snapshot(d, timeout=30):
    """等待人员列表、计数及行位置连续稳定；加载状态不冒充空名单。"""
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        guard.ensure_foreground(d)
        xml = d.dump_hierarchy()
        rows, footer = parse_snapshot(xml)
        loading = 'loading_view' in xml or 'android.widget.ProgressBar' in xml
        mark = (tuple((r["name"], r["info"], r["cx"], r["cy"]) for r in rows), footer)
        if not loading and count_of(footer) is not None:
            if mark == previous:
                return rows, footer
            previous = mark
        else:
            previous = None
        time.sleep(0.2)
    raise guard.GuardError("等待未签到列表及补签人数就绪超时，已停止；没有重复勾选或提交。")


def _scroll_people(d, toward_top=False):
    """按真实列表边界下滑，保留重叠行，不触碰底部按钮。"""
    import xml.etree.ElementTree as ET
    nodes = [n for n in ET.fromstring(d.dump_hierarchy()).iter("node")
             if n.get("resource-id", "").endswith(":id/recycleview")]
    if len(nodes) != 1:
        raise guard.GuardError("没有唯一人员列表，已停止滚动。")
    x1, y1, x2, y2 = [int(x) for x in re.findall(r"\d+", nodes[0].get("bounds", ""))]
    x = (x1 + x2) // 2
    start, end = int(y1 + (y2-y1)*.75), int(y1 + (y2-y1)*.40)
    if toward_top:
        start, end = end, start
    d.swipe(x, start, x, end, .45)
    time.sleep(.4)


def _nodes(xml):
    import xml.etree.ElementTree as ET

    out = []
    for n in ET.fromstring(xml).iter("node"):
        b = [int(x) for x in re.findall(r"\d+", n.get("bounds") or "")]
        if len(b) != 4:
            continue
        out.append({
            "text": (n.get("text") or "").strip(),
            "rid": n.get("resource-id") or "",
            "cx": (b[0] + b[2]) // 2,
            "cy": (b[1] + b[3]) // 2,
        })
    return out


def wait_rows(d, timeout=12.0):
    """页面可能在加载：等到能读到人员行为止。"""
    deadline = time.time() + timeout
    last = ([], "")
    while time.time() < deadline:
        try:
            last = snap(d)
        except Exception:
            time.sleep(0.5)
            continue
        if last[0]:
            return last
        time.sleep(0.6)
    return last


def count_of(footer):
    m = FOOTER_RE.search(footer or "")
    return int(m.group(1)) if m else None


def reset_top(d):
    """直接选择未签到，在该列表内回顶，不切到已签到。"""
    guard.ensure_foreground(d)
    tab = d(text="未签到")
    if not tab.wait(6):
        raise guard.GuardError("找不到「未签到」页签，已停止。")
    tab.click()
    previous, unchanged = None, 0
    for _ in range(160):
        rows, footer = _ready_snapshot(d)
        mark = tuple((r["name"], r["info"], r["cy"]) for r in rows)
        unchanged = unchanged + 1 if mark == previous else 0
        if unchanged >= 3:
            return rows, footer
        previous = mark
        _scroll_people(d, toward_top=True)
    raise guard.GuardError("未确认未签到名单已回到顶部，已停止。")


def collect(d, max_scroll=160):
    """只读收集：连续三次下滑没有新人员才结束，不作为勾选前置步骤。"""
    merged, order = {}, []
    streak = 0
    for i in range(max_scroll):
        rows, footer = _ready_snapshot(d)
        fresh = 0
        for r in rows:
            key = (r["name"], r["info"])
            if key not in merged:
                merged[key] = r
                order.append(key)
                fresh += 1
        print(f"  第 {i + 1} 屏：+{fresh} 人（累计 {len(order)}），{footer}")
        if i > 0 and fresh == 0:
            streak += 1
            if streak >= 3:
                print("  [OK] 连续三次下滑没有新人员，已到列表末尾")
                break
            time.sleep(1.2)  # 可能还在懒加载，等一下再滑
        else:
            streak = 0
        _scroll_people(d)
    else:
        raise guard.GuardError("达到扫描上限但未确认列表结束，已停止，不能视为全部读取完成。")
    return [merged[k] for k in order]


def grade_summary(rows):
    from collections import Counter

    c = Counter(r["grade"] or "未知" for r in rows)
    for g in sorted(c):
        print(f"  {g}级：{c[g]} 人")
    return c


def main(argv=None):
    ap = argparse.ArgumentParser(description="补签（按年级）")
    ap.add_argument("--grade", help="只勾这个年级，例如 2024")
    ap.add_argument("--open", help="先从管理列表进入该活动的「未签到」（模糊匹配活动名）")
    ap.add_argument("--dry", action="store_true", help="只读，不点任何控件")
    ap.add_argument("--select", action="store_true", help="真勾选，但不提交")
    ap.add_argument("--clear", action="store_true", help="取消全部勾选")
    ap.add_argument("--submit", action="store_true", help="提交补签（需要 --yes）")
    ap.add_argument("--yes", action="store_true", help="确认提交（不可逆）")
    ap.add_argument("--limit", type=int, default=0, help="只勾前 N 人（试跑用）")
    args = ap.parse_args(argv)

    import uiautomator2 as u2

    d = u2.connect()
    guard.ensure_foreground(d)

    if args.open:
        if not open_unsigned(d, args.open):
            return 1

    rows, footer = wait_rows(d)
    if not rows:
        print("[FAIL] 当前页没有人员行；确认已停在「未签到」列表（或加 --open）")
        guard.diagnostics(d, "makeup-empty")
        return 2

    print("[..] 回到列表顶部")
    reset_top(d)
    time.sleep(0.6)

    print(f"\n当前：{footer}  本屏 {len(rows)} 人")
    all_rows = collect(d)
    print(f"\n共读到 {len(all_rows)} 人：")
    grade_summary(all_rows)

    if args.dry:
        want = args.grade
        picked = [r for r in all_rows if not want or r["grade"] == want]
        print(f"\n会勾选 {len(picked)} 人：")
        for r in picked:
            print(f"  {r['name']}（{r['grade']}级）{r['info']}")
        print("[dry] 没有动手机。")
        return 0

    if args.clear:
        return clear_all(d, all_rows)

    picked = [r for r in all_rows if not args.grade or r["grade"] == args.grade]
    if not picked:
        print(f"[FAIL] 没有 {args.grade} 级的未签到人员")
        return 2

    if args.select or args.submit:
        picked = walk_select(d, args.grade, args.limit or None)
        if picked is None:
            return 1
        if args.submit:
            if not args.yes:
                print("[STOP] 已勾选但未提交；确认要提交请加 --yes")
                return 0
            return submit(d, picked)
    return 0


def walk_select(d, want=None, limit=None, *, observe=None):
    """边检查边勾选；连续三次下滑没有新人员才结束，每人只点一次。"""
    if want is not None and not re.fullmatch(r"20\d{2}", str(want)):
        raise guard.GuardError("年级格式应为四位年份。")
    _, footer = _ready_snapshot(d)
    if count_of(footer) != 0:
        raise guard.GuardError("当前已有勾选，请先核对或取消，避免混入其他人员。")
    reset_top(d)
    done, seen, unchanged = {}, set(), 0
    count = 0
    print("[..] 起始：已勾 0 人")
    for page in range(160):
        rows, footer = _ready_snapshot(d)
        if count_of(footer) != count:
            raise guard.GuardError("补签人数与本轮选择不一致，已停止。")
        keys = [(r["name"], r["info"]) for r in rows]
        if len(keys) != len(set(keys)):
            raise guard.GuardError("同屏存在无法区分的同名同班人员，已停止。")
        fresh = set(keys) - seen
        seen.update(keys)
        unchanged = unchanged + 1 if page > 0 and not fresh else 0
        if unchanged >= 3:
            print("  [OK] 连续三次下滑没有新人员，已到列表末尾")
            break
        for original in rows:
            key = (original["name"], original["info"])
            if observe and key not in done:
                observe(original, "跳过" if want and original["grade"] != want else "未处理",
                        "非本次目标年级" if want and original["grade"] != want else "尚未勾选")
            if key in done or (want and original["grade"] != want):
                continue
            fresh, footer = _ready_snapshot(d)
            matches = [r for r in fresh if (r["name"], r["info"]) == key]
            if len(matches) != 1 or count_of(footer) != count:
                raise guard.GuardError("人员位置或勾选人数变化，已停止。")
            row = matches[0]
            if observe:
                observe(row, "点击待核对", "即将点击，需等待勾选人数增加1；尚未提交补签。")
            d.click(row["cx"], row["cy"])
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                _, footer = snap(d)
                now = count_of(footer)
                if now == count + 1:
                    break
                if now is not None and now not in (count, count + 1):
                    raise guard.GuardError(f"勾选人数异常（{count} → {now}），已停止。")
                time.sleep(.2)
            else:
                raise guard.GuardError(f"勾选 {row['name']} 后人数没有 +1，已停止；没有重复点击。")
            count += 1
            done[key] = row
            if observe:
                observe(row, "已勾选未提交", "勾选人数增加1，尚未提交补签。")
            print(f"  [勾选 {count}] {row['name']}（{row['grade']}级）→ {footer}")
            if limit and count >= limit:
                print(f"[OK] 已达到试跑上限 {limit} 人，未提交")
                return list(done.values())
        print(f"  第 {page+1} 屏：已检查 {len(seen)} 人，累计勾选 {count} 人")
        _scroll_people(d)
    else:
        raise guard.GuardError("达到扫描上限但未确认列表结束，已停止，不能视为全部勾选完成。")
    _, footer = _ready_snapshot(d)
    if count_of(footer) != count:
        raise guard.GuardError("结束时补签人数与本轮选择不一致，已停止。")
    if not count:
        print(f"[OK] 已扫描到列表末尾，没有{str(want) + '级' if want else ''}待补签人员；未提交")
    else:
        print(f"[OK] 勾选完成：共 {count} 人，底部显示 {footer}；未提交")
    return list(done.values())


def clear_all(d, rows=None):
    """全选一次再取消一次：不管之前勾了谁，最后都归零。"""
    print(f"[..] 当前：{snap(d)[1]}")
    el = d(resourceId="com.jingcai.apps.qualitydev:id/tv_step")
    if not el.wait(3):
        print("[FAIL] 没找到「全选」")
        return 1
    el.click()
    time.sleep(1.2)
    print(f"[..] 全选后：{snap(d)[1]}")
    el.click()
    time.sleep(1.2)
    left = count_of(snap(d)[1])
    print(f"[OK] 取消后：{snap(d)[1]}")
    return 0 if left == 0 else 1


def submit(d, rows):
    footer = snap(d)[1]
    n = count_of(footer)
    print(f"\n[..] 准备提交：{footer}")
    ans = input(f"确认对 {n} 人补签？输入 yes 继续：").strip()
    if ans.lower() != "yes":
        print("[STOP] 已取消，未提交")
        return 0
    guard.begin_submit("makeup", {"count": n, "names": [r["name"] for r in rows]})
    el = d(resourceId="com.jingcai.apps.qualitydev:id/tv_next")
    if not el.wait(3):
        print("[FAIL] 找不到底部的「补签 N 人」")
        return 1
    el.click()
    time.sleep(1.5)
    xml = d.dump_hierarchy()
    print("[..] 弹窗：", [t for t in _texts(xml)][:8])
    if "补签" not in xml and "确认" not in xml:
        print("[FAIL] 没出现预期确认弹窗，已停止")
        return 1
    if not _click_confirm(d):
        print("[FAIL] 找不到确认按钮")
        return 1
    time.sleep(2.5)
    print("[..] 提交后：", snap(d)[1])
    guard.end_submit()
    return 0


def _texts(xml):
    return [(n.get("text") or "").strip() for n in __import__("xml.etree.ElementTree", fromlist=["ET"]).fromstring(xml).iter("node") if (n.get("text") or "").strip()]


def _click_confirm(d):
    for rid in ("android:id/button1",):
        el = d(resourceId=rid)
        if el.wait(2):
            el.click()
            return True
    el = d(text="确认")
    if el.wait(2):
        el.click()
        return True
    return False


def activity_button(xml, keyword):
    """只绑定同一卡片里的活动标题和管理按钮，禁止借用相邻卡片按钮。"""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml)
    parents = {child: parent for parent in root.iter() for child in parent}
    titles = [n for n in root.iter("node")
              if n.get("resource-id", "").endswith(":id/tv_activity_name")]
    hits = [n for n in titles if n.get("text", "").strip() == keyword]
    if not hits:
        hits = [n for n in titles if keyword in n.get("text", "")]
    if len(hits) > 1:
        raise guard.GuardError("多个活动匹配，请使用完整活动名称。")
    if not hits:
        return None
    title = hits[0]
    parent = parents.get(title)
    while parent is not None:
        names = [n for n in parent.iter("node")
                 if n.get("resource-id", "").endswith(":id/tv_activity_name")]
        if len(names) != 1:
            break
        buttons = [n for n in parent.iter("node")
                   if n.get("text", "").strip() == "管理活动"
                   and n.get("resource-id", "").endswith(":id/tv_tag_bb")]
        if len(buttons) == 1:
            button = buttons[0]
            box = [int(x) for x in re.findall(r"-?\d+", button.get("bounds", ""))]
            if len(box) != 4 or box[0] >= box[2] or box[1] >= box[3]:
                return None
            if (button.get("visible-to-user", "true") == "false"
                    or button.get("enabled", "true") != "true"):
                return None
            cx, cy = (box[0]+box[2])//2, (box[1]+box[3])//2
            for overlay in root.iter("node"):
                if not (overlay.get("text", "").strip() == "发布活动"
                        or overlay.get("resource-id", "").endswith((":id/btn_createactivity", ":id/btn_mangeractivity"))):
                    continue
                cover = [int(x) for x in re.findall(r"-?\d+", overlay.get("bounds", ""))]
                if len(cover) == 4 and cover[0] <= cx <= cover[2] and cover[1] <= cy <= cover[3]:
                    return None
            listing = parents.get(parent)
            while listing is not None:
                if listing.get("resource-id", "").endswith(":id/gridview_activitys"):
                    viewport = [int(x) for x in re.findall(r"-?\d+", listing.get("bounds", ""))]
                    if len(viewport) != 4 or not (viewport[0] <= box[0] < box[2] <= viewport[2]
                                                  and viewport[1] <= box[1] < box[3] <= viewport[3]):
                        return None
                    break
                listing = parents.get(listing)
            return {"name": title.get("text", "").strip(),
                    "cx": cx, "cy": cy}
        parent = parents.get(parent)
    return None


def verify_activity(xml, expected):
    titles = [n["text"] for n in _nodes(xml) if n["rid"].endswith(":id/tv_activity_name")]
    if titles != [expected]:
        raise guard.GuardError(f"活动核对失败：目标「{expected}」，当前「{' / '.join(titles) or '未读到'}」，已停止。")


def open_unsigned(d, keyword):
    """按同一卡片进入活动，再核对签到管理页标题，最后打开未签到。"""
    keyword = keyword.strip()
    if not keyword:
        raise guard.GuardError("请先选择活动。")
    title = scan.open_activity(d, keyword)
    guard.ensure_foreground(d)
    el = d(text="签到管理")
    if not el.wait(6):
        raise guard.GuardError("进入活动后没找到「签到管理」。")
    el.click()
    time.sleep(1.5)
    listing = d(resourceId=guard.PKG + ":id/tv_clear", text="签到列表")
    if not listing.wait(10):
        raise guard.GuardError("签到管理页未加载，没找到「签到列表」。")
    verify_activity(d.dump_hierarchy(), title)
    print(f"[核对签到管理] {title}")
    listing.click()
    tab = d(text="未签到")
    if not tab.wait(10):
        raise guard.GuardError("没找到「未签到」页签，已停止。")
    tab.click()
    _ready_snapshot(d)
    print("[OK] 已进入目标活动的「未签到」列表")
    return True


if __name__ == "__main__":
    sys.exit(main())
