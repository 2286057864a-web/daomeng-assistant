"""只读等待与旧补签入口的单次提交；无设备连接代码。"""
import re
import time
import xml.etree.ElementTree as ET

from submission_log import SubmissionLog, check_previous


class PageNotReady(RuntimeError):
    """适配器仅在页面加载/底栏尚未出现时抛出；断连、错页不能用此异常。"""


def wait_count(read, timeout=30, interval=.2, expected=None):
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        try:
            value = read()
        except PageNotReady:
            value = None
        if value is not None and (type(value) is not int or value < 0):
            raise RuntimeError("人数读取不是有效非负整数，已停止。")
        if value is not None and value == previous and (expected is None or value == expected):
            return value
        previous = value
        time.sleep(interval)
    target = "底部计数或明确空列表" if expected is None else f"人数变为 {expected}"
    raise RuntimeError(f"等待人员页{target}超时（{timeout} 秒上限）；未重复点击。")


def _nodes(xml):
    return list(ET.fromstring(xml).iter("node"))


def _footer(nodes):
    hits = [n for n in nodes if n.get("resource-id", "").endswith(":id/tv_next")
            and re.fullmatch(r"补签\s*\d+\s*人", n.get("text", ""))]
    return hits[0] if len(hits) == 1 else None


def _center(node):
    values = [int(n) for n in re.findall(r"-?\d+", node.get("bounds", ""))]
    if len(values) != 4 or values[0] < 0 or values[1] < 0 or values[2] <= values[0] or values[3] <= values[1]:
        raise RuntimeError("按钮位置无效，已停止。")
    if node.get("enabled", "true") != "true":
        raise RuntimeError("按钮尚未启用，已停止。")
    return (values[0] + values[2]) // 2, (values[1] + values[3]) // 2


def submit_selected_makeup(device, title, out_dir, *, timeout=30, interval=.2):
    """先保存意图，再点一次补签；支持直接完成或经稳定的补签确认框完成。

    不把导航或计数变化宣称为已签到复核。按钮不重复点；任何不确定结果保留记录。
    """
    import guard

    check_previous(out_dir, "makeup", title, None, None)
    last_nodes = []

    def read_count():
        nonlocal last_nodes
        guard.ensure_foreground(device)
        last_nodes = _nodes(device.dump_hierarchy())
        footer = _footer(last_nodes)
        if footer is None:
            raise PageNotReady("补签底栏尚未就绪")
        return int(re.search(r"\d+", footer.get("text")).group())

    count = wait_count(read_count, timeout=timeout, interval=interval)
    if count == 0:
        raise RuntimeError("当前没有勾选任何人，未提交。")
    footer = _footer(last_nodes)
    # 点击前再次取实际节点与人数，拒绝过期坐标。
    guard.ensure_foreground(device)
    fresh = _nodes(device.dump_hierarchy())
    current = _footer(fresh)
    if current is None or (current.get("text"), current.get("bounds")) != (footer.get("text"), footer.get("bounds")):
        raise RuntimeError("补签按钮位置或人数发生变化，未提交。")
    position = _center(current)
    record = SubmissionLog(out_dir, "makeup", title, None, None)
    record.data["count"] = count
    record.save()
    try:
        device.click(*position)
        deadline = time.monotonic() + timeout
        previous, stable, confirmed = None, 0, False
        while time.monotonic() < deadline:
            guard.ensure_foreground(device)
            nodes = _nodes(device.dump_hierarchy())
            dialogs = [n for n in nodes if n.get("resource-id") == "android:id/button1"]
            if dialogs:
                messages = [n.get("text", "") for n in nodes if n.get("resource-id") == "android:id/message"]
                if len(dialogs) != 1 or len(messages) != 1 or "补签" not in messages[0] or dialogs[0].get("text") != "确定":
                    raise RuntimeError("无法确认弹窗属于本次补签，未点击确定。")
                counts = re.findall(r"(?:已)?选中(?:的)?\s*(\d+)\s*人|补签\s*(\d+)\s*人", messages[0])
                if not counts or any(int(a or b) != count for a, b in counts):
                    raise RuntimeError("补签确认框选中人数无法核对，未点击确定。")
                mark = (messages[0], dialogs[0].get("bounds"))
                stable = stable + 1 if mark == previous else 1
                previous = mark
                if stable >= 3 and not confirmed:
                    device.click(*_center(dialogs[0]))
                    confirmed = True
                    previous, stable = None, 0
            else:
                footer = _footer(nodes)
                zero = footer is not None and re.fullmatch(r"补签\s*0\s*人", footer.get("text", ""))
                signed_tab = any(n.get("text") == "已签到" and
                                 (n.get("selected") == "true" or n.get("checked") == "true") for n in nodes)
                if zero or signed_tab:
                    stable = stable + 1 if previous == "completed" else 1
                    previous = "completed"
                    if stable >= 2:
                        record.save("已提交未核查", "补签动作已结束，未逐人核查已签到名单。")
                        return count, record.path
                elif confirmed:
                    # 已确认且弹窗已关闭，动作已发出；不要求额外成功 toast。
                    record.save("已提交未核查", "确认框已关闭，未逐人核查已签到名单。")
                    return count, record.path
                else:
                    previous, stable = None, 0
            time.sleep(interval)
        raise RuntimeError("补签点击后未能确认页面状态，未重试点击。")
    except Exception as exc:
        note = f"提交结果待核对：可能已经补签，禁止直接重试。{exc}"
        record.save("提交结果待核对", note)
        raise RuntimeError(f"{note}\n提交记录：{record.path}") from exc
