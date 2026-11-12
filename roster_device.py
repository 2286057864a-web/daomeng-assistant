"""真实设备上的名单任务后端。

把 roster_workflow 需要的 8 个动作接到到梦空间页面：

    open(task, title, score)  导航到对应页（补签→未签到 / 录取→报名 / 发分→人员选择）
    begin_group(query)        清空搜索框，确认当前没有勾选
    count()                   读底部「补签/颁发/录取 N 人」
    search(query)             搜学号或姓氏，返回所有结果行
    select(key)               按行标识点一行，计数由调用方校验
    clear_selection()         全选两次把勾选清零
    submit(students, score)   点提交 → 确认弹窗 → 复核
    end_group()               清搜索、清勾选

安全：计数每次由 roster_workflow 用 wait_count 校验；本文件不吞异常，
任何不确定都抛错并停止，不重试提交。
"""

from __future__ import annotations

import functools
import re
import time
import xml.etree.ElementTree as ET

import errcatch
import guard
from device_checks import PageNotReady

PKG = "com.jingcai.apps.qualitydev"
SEARCH_ID = PKG + ":id/text_searchcontent"
FOOTER_ID = PKG + ":id/tv_next"
SELECT_ALL_ID = PKG + ":id/tv_step"

TABS = {"makeup": ("未签到", "已签到"), "admit": ("报名", "录取")}
SUBMIT_VERB = {"makeup": "补签", "admit": "录取", "credit": "颁发"}


def _nodes(xml: str):
    out = []
    for n in ET.fromstring(xml).iter("node"):
        b = [int(x) for x in re.findall(r"-?\d+", n.get("bounds") or "")]
        out.append({
            "text": (n.get("text") or "").strip(),
            "rid": n.get("resource-id") or "",
            "enabled": n.get("enabled", "true"),
            "box": tuple(b) if len(b) == 4 else None,
        })
    for n in out:
        if n["box"]:
            x1, y1, x2, y2 = n["box"]
            n["cx"], n["cy"] = (x1 + x2) // 2, (y1 + y2) // 2
        else:
            n["cx"] = n["cy"] = None
    return out


STALL_SECONDS = 60.0


def guarded(name: str):
    """单个设备动作超过 STALL_SECONDS 秒没做完，就自动留一份现场（只记录，不打断）。"""

    def deco(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            with errcatch.stall(STALL_SECONDS, name, getattr(self, "d", None)):
                return fn(self, *args, **kwargs)

        return wrapper

    return deco


class DeviceBackend:
    def __init__(self, device):
        self.d = device
        self.task = None
        self.title = ""
        self.query = ""

    # ---------- 基础读取 ----------
    def _xml(self) -> str:
        guard.ensure_foreground(self.d)
        return self.d.dump_hierarchy()

    def _rows(self, xml: str | None = None):
        """同一人员容器绑定姓名、班级及勾选框，残缺行留到下一屏。"""
        from makeup import parse_snapshot

        return parse_snapshot(xml if xml is not None else self._xml())[0]

    def _ready_rows(self, timeout=30):
        """等待加载完成和行位置稳定，加载中的空屏不计作没有新人员。"""
        deadline = time.monotonic() + timeout
        previous = None
        while time.monotonic() < deadline:
            xml = self._xml()
            rows = self._rows(xml)
            mark = tuple((r["name"], r["info"], r["cx"], r["cy"]) for r in rows)
            loading = "loading_view" in xml or "android.widget.ProgressBar" in xml
            if not loading and SEARCH_ID in xml and self._footer(xml) is not None:
                if mark == previous:
                    return rows
                previous = mark
            else:
                previous = None
            time.sleep(.2)
        raise RuntimeError("等待搜索名单加载完成超时，未确认结果结束，已停止。")

    def _footer(self, xml: str | None = None):
        for n in _nodes(xml or self._xml()):
            if n["rid"].endswith(":id/tv_next") and n["text"]:
                return n
        return None

    # ---------- 契约 ----------
    @guarded("打开名单页")
    def open(self, *, task, title, score=None):
        self.task, self.title = task, title
        d = self.d
        if task == "makeup":
            import makeup

            if not makeup.open_unsigned(d, title):
                raise RuntimeError(f"没能进入「{title}」的未签到名单")
        elif task == "admit":
            import admit

            admit.open_people(d, title)
            tab = d(text="报名")
            if tab.wait(5):
                tab.click()
                time.sleep(1.5)
        elif task == "credit":
            import credit

            credit.enter_credit(d, title)
            projs = credit.projects(d)
            if not projs:
                raise RuntimeError("学分管理页没有可颁发的项目")
            from roster_plan import parse_score

            requested = parse_score(score)
            matches = [p for p in projs if p["score"] is not None and parse_score(p["score"]) == requested]
            if not matches:
                raise RuntimeError(f"找不到分值为 {score} 的项目（页面只有：{[p['score'] for p in projs]}）")
            if len(matches) > 1:
                raise RuntimeError(f"存在多个分值为 {score} 的项目，无法确定颁发项目，已停止。")
            target = matches[0]
            btn = target.get("btn")
            if not btn:
                raise RuntimeError("该项目没有「颁发」按钮")
            d.click(btn[0], btn[1])
            time.sleep(1.8)
        else:
            raise RuntimeError(f"未知任务：{task}")
        self._expect_search_box()

    @guarded("开始分组")
    def begin_group(self, query):
        self.query = ""
        self._clear_search()
        n = self.count()
        if n != 0:
            raise RuntimeError(f"本组开始前已有 {n} 人被勾选，已停止")

    def count(self) -> int:
        """只查底部那一个控件，比整页 dump 快一个数量级。"""
        try:
            info = self.d(resourceId=FOOTER_ID).info
        except Exception as exc:  # noqa: BLE001
            raise PageNotReady(f"底部计数按钮还没出现（{type(exc).__name__}）") from exc
        text = (info.get("text") or "").strip()
        m = re.search(r"(\d+)", text)
        if not m:
            raise PageNotReady(f"底部按钮文字异常：{text!r}")
        return int(m.group(1))

    @guarded("搜索名单")
    def search(self, query):
        from roster_workflow import Candidate

        self.query = query
        self._type_search(query)
        rows = self._collect()
        self._back_to_top()
        self._type_search(query)
        sid = query if re.fullmatch(r"\d{11}", query) else ""
        return [Candidate(key=f"{r['name']}\x1f{r['info']}", name=r["name"], sid=sid) for r in rows]

    @guarded("勾选一人")
    def select(self, key):
        for _ in range(2):
            for rows in self._walk_rows():
                hits = [r for r in rows if f"{r['name']}\x1f{r['info']}" == key]
                if len(hits) > 1:
                    raise RuntimeError("搜索结果中存在无法区分的同名同班人员，未勾选。")
                if hits:
                    from device_checks import wait_count

                    before = wait_count(self.count)
                    # 点击前重新确认当前行；只点击一次，等真实人数 +1。
                    fresh = [r for r in self._ready_rows() if f"{r['name']}\x1f{r['info']}" == key]
                    if len(fresh) != 1:
                        raise RuntimeError("人员行发生变化，未勾选。")
                    self.d.click(fresh[0]["cx"], fresh[0]["cy"])
                    wait_count(self.count, expected=before + 1)
                    return
            self._back_to_top()
            self._type_search(self.query)
        raise RuntimeError(f"搜索结果里找不到这一行：{key.split(chr(31))[0]}")

    @guarded("清空勾选")
    def clear_selection(self):
        if self.count() == 0:
            return
        el = self.d(resourceId=SELECT_ALL_ID)
        if not el.wait(3):
            raise RuntimeError("找不到「全选」，无法清空勾选")
        el.click()
        time.sleep(1.2)
        el.click()
        time.sleep(1.2)
        n = self.count()
        if n != 0:
            raise RuntimeError(f"清空勾选后仍有 {n} 人，已停止")

    @guarded("提交")
    def submit(self, *, students, score=None):
        from roster_workflow import Receipt

        before = self.count()
        if before == 0:
            raise RuntimeError("当前没有勾选任何人，未提交")
        verb = SUBMIT_VERB.get(self.task or "", "")
        btn = self._footer()
        if not btn or verb not in (btn["text"] or ""):
            raise RuntimeError(f"底部按钮不是「{verb} N 人」：{btn['text'] if btn else '无'}")
        guard.begin_submit(self.task or "roster", {"count": before, "students": len(students)})
        self.d.click(btn["cx"], btn["cy"])
        time.sleep(1.6)
        self._confirm_dialog()
        guard.end_submit()

        if self.task == "credit":
            return Receipt(before=before, after=before, submitted=True)

        confirmed = self._verify_in_tab(students)
        if len(confirmed) != len(students):
            missing = [s["name"] for s in students if all(s["name"] != c[0] for c in confirmed)]
            raise RuntimeError(f"提交后只复核到 {len(confirmed)}/{len(students)} 人，未找到：{'、'.join(missing[:5])}")
        return Receipt(identities=tuple(confirmed), before=before,
                       after=before + len(confirmed), submitted=True)

    def end_group(self):
        self._clear_search()
        try:
            self.clear_selection()
        except RuntimeError:
            pass

    # ---------- 内部 ----------
    def _expect_search_box(self, timeout=8):
        if not self.d(resourceId=SEARCH_ID).wait(timeout):
            raise RuntimeError("当前页没有搜索框，可能没进到人员列表")

    def _type_search(self, query):
        box = self.d(resourceId=SEARCH_ID)
        if not box.wait(5):
            raise RuntimeError("找不到搜索框")
        try:
            box.set_text(query)
        except Exception:
            box.click()
            time.sleep(0.4)
            self.d.send_keys(query, clear=True)
        time.sleep(1.3)

    def _clear_search(self):
        box = self.d(resourceId=SEARCH_ID)
        if not box.wait(3):
            return
        try:
            box.set_text("")
        except Exception:
            box.click()
            time.sleep(0.3)
            self.d.send_keys("", clear=True)
        time.sleep(1.0)

    def _walk_rows(self, limit=160):
        """仅遍历当前查询结果；三次下滑没有新人员才返回，不能静默截断。"""
        seen, streak = set(), 0
        for page in range(limit):
            rows = self._ready_rows()
            keys = [(r["name"], r["info"]) for r in rows]
            if len(keys) != len(set(keys)):
                raise RuntimeError("搜索结果中存在无法区分的同名同班人员，已停止。")
            fresh = set(keys) - seen
            seen.update(keys)
            streak = streak + 1 if page > 0 and not fresh else 0
            if streak >= 3:
                return
            yield rows
            self._swipe_down()
        raise RuntimeError("达到搜索扫描上限但未确认结果结束，已停止，不能视为全部名单处理完成。")

    def _collect(self, limit=160):
        seen, order = set(), []
        for rows in self._walk_rows(limit):
            for row in rows:
                key = (row["name"], row["info"])
                if key not in seen:
                    seen.add(key)
                    order.append(row)
        return order

    def _swipe_down(self):
        from makeup import _scroll_people

        _scroll_people(self.d)

    def _back_to_top(self):
        """沿当前查询结果回到顶部，不切换到已签到或录取页签。"""
        from makeup import _scroll_people

        previous, unchanged = None, 0
        for _ in range(160):
            mark = tuple((r["name"], r["info"], r["cy"]) for r in self._ready_rows())
            unchanged = unchanged + 1 if mark == previous else 0
            if unchanged >= 3:
                return
            previous = mark
            _scroll_people(self.d, toward_top=True)
        raise RuntimeError("未确认搜索结果已回到顶部，已停止。")

    def _confirm_dialog(self):
        """只点确认弹窗，不重试：找不到就报错交人工。"""
        for _ in range(12):
            nodes = _nodes(self._xml())
            btns = [n for n in nodes if n["rid"] == "android:id/button1"]
            msgs = [n for n in nodes if n["rid"] == "android:id/message"]
            if len(btns) == 1 and len(msgs) == 1:
                if btns[0]["text"] != "确定":
                    raise RuntimeError(f"确认按钮不是「确定」：{btns[0]['text']}")
                self.d.click(btns[0]["cx"], btns[0]["cy"])
                time.sleep(1.0)
                return
            if len(btns) > 1 or len(msgs) > 1:
                raise RuntimeError("出现多个弹窗按钮，无法确定点击哪个，已停止")
            time.sleep(0.5)
        raise RuntimeError("提交后没有出现确认弹窗，已停止（可能未提交，请人工核对）")

    def _verify_in_tab(self, students):
        """切到「已签到 / 录取」页，逐个搜索姓名或学号，确认是否真的进去了。"""
        pair = TABS.get(self.task or "")
        if not pair:
            return []
        el = self.d(text=pair[1])
        if not el.wait(6):
            raise RuntimeError(f"找不到「{pair[1]}」页签，无法复核")
        el.click()
        time.sleep(1.6)
        self._clear_search()
        confirmed = []
        for s in students:
            query = s.get("sid") or s.get("name")
            if not query:
                continue
            self._type_search(query)
            rows = self._collect()
            hit = next((r for r in rows if r["name"] == s["name"]), None)
            if hit:
                confirmed.append((s["name"], s.get("sid", "")))
        self._clear_search()
        back = self.d(text=pair[0])
        if back.wait(4):
            back.click()
            time.sleep(1.3)
        return confirmed


def create_backend(device):
    """roster_workflow.BACKEND_FACTORY 的实现：接收已连接设备，返回后端。"""
    return DeviceBackend(device)
