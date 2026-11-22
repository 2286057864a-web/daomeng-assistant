"""真实设备上的名单任务后端。

把 roster_workflow 需要的 8 个动作接到到梦空间页面：

    open(task, title, score)  导航到对应页（补签→未签到 / 录取→报名 / 发分→人员选择）
    begin_group(query)        确认当前没有勾选，随后直接输入本组查询
    count()                   读底部「补签/颁发/录取 N 人」
    search(query, target_names=()) 按预处理表搜索；姓氏查询可在找到目标姓名后返回匹配勾选
    select(key, expected_before=...)  核对人数和目标行，点击一次后返回已确认的人数
    clear_selection()         全选两次把勾选清零
    submit(students, score)   点提交 → 确认弹窗 → 复核
    end_group()               清搜索、清勾选

安全：勾选和提交前的计数使用 wait_count 校验；本文件不吞异常，
任何不确定都抛错并停止，不重试提交。
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
import re
import time
import xml.etree.ElementTree as ET

import errcatch
import guard
from device_checks import PageNotReady
from makeup import SingleScreenRows

PKG = "com.jingcai.apps.qualitydev"
SEARCH_ID = PKG + ":id/text_searchcontent"
FOOTER_ID = PKG + ":id/tv_next"
SELECT_ALL_ID = PKG + ":id/tv_step"

SUBMIT_VERB = {"makeup": "补签", "admit": "录取", "credit": "颁发"}


def _nodes(xml: str):
    out = []
    for n in ET.fromstring(xml).iter("node"):
        b = [int(x) for x in re.findall(r"-?\d+", n.get("bounds") or "")]
        out.append({
            "text": (n.get("text") or "").strip(),
            "hint": n.get("hint") or "",
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


def _search_matches(node, query):
    return node["rid"] == SEARCH_ID and (node["text"] == query or
            (not query and bool(node.get("hint")) and node["text"] == node["hint"]))


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
        self.credit_score = None
        self._input_session_active = False
        self._original_ime = ""
        self.check_stop = lambda: None
        self.search_max_grade = None
        self.unique_names = set()

    # ---------- 基础读取 ----------
    def _xml(self) -> str:
        guard.ensure_foreground(self.d)
        return self.d.dump_hierarchy()

    def _rows(self, xml: str | None = None):
        """同一人员容器绑定姓名、班级及勾选框，残缺行留到下一屏。"""
        from makeup import parse_snapshot

        return parse_snapshot(xml if xml is not None else self._xml())[0]

    def _ready_rows(self, timeout=30, query=None):
        """等待加载完成和行位置稳定，加载中的空屏不计作没有新人员。"""
        deadline = time.monotonic() + timeout
        previous = None
        hide_sent, keyboard_since = False, None
        keyboard_blocked = False
        search_issue = ""
        while time.monotonic() < deadline:
            xml = self._xml()
            rows = self._rows(xml)
            mark = (tuple((r["name"], r["info"], r["cx"], r["cy"]) for r in rows), isinstance(rows, SingleScreenRows))
            loading = "loading_view" in xml or "android.widget.ProgressBar" in xml
            empty = not rows and self._empty_results(xml)
            ready = not loading and SEARCH_ID in xml and (self._footer(xml) is not None or empty)
            if query is not None:
                typed = any(_search_matches(n, query) for n in _nodes(xml))
                if not typed:
                    values = [n["text"] for n in _nodes(xml) if n["rid"] == SEARCH_ID]
                    issue = f"搜索框暂读为 {values!r}" if values else "暂未读到人员搜索框"
                    if issue != search_issue:
                        print(f"[等待搜索页面] {issue}；等待“{query or '全部人员'}”，不点击、不按返回")
                    search_issue = issue
                    previous, keyboard_since, keyboard_blocked = None, None, False
                    time.sleep(.2)
                    continue
                search_issue = ""
                filtered = not query or re.fullmatch(r"\d{11}", query) or all(query in r["name"] for r in rows)
                package = self._original_ime.split('/')[0]
                keyboard_visible = bool(package) and any(
                    n.get('package') == package and n.get('visible-to-user', 'true') == 'true'
                    for n in ET.fromstring(xml).iter('node'))
                keyboard_blocked = typed and keyboard_visible
                if keyboard_blocked:
                    # XML 会残留键盘节点；以系统服务的当前状态核对，不能只信输入法视图状态。
                    try:
                        state = self.d.shell(['dumpsys', 'input_method'], timeout=3)
                        shown = re.findall(r'\bmInputShown=(true|false)\b', state.output)
                        if state.exit_code != 0 or len(shown) != 1:
                            raise ValueError('unknown input state')
                    except Exception as exc:
                        raise RuntimeError("无法确认系统键盘状态，未按返回键，已停止。") from exc
                    keyboard_blocked = shown[0] == 'true'
                if keyboard_blocked:
                    now = time.monotonic()
                    if keyboard_since is None:
                        keyboard_since = now
                    # 先等搜索键自行收起键盘；每次搜索最多一次 Back，之后只等页面恢复。
                    if not hide_sent and now - keyboard_since >= .6:
                        self.d.press('back')
                        hide_sent = True
                        print("[收起键盘] 系统确认仍展开，返回键仅发送一次；等待人员页恢复")
                    previous = None
                    time.sleep(.2)
                    continue
                keyboard_since = None
                ready = ready and typed and filtered and (bool(rows) or empty)
            if ready:
                if mark == previous:
                    return rows
                previous = mark
            else:
                previous = None
            time.sleep(.2)
        if keyboard_blocked:
            raise RuntimeError(f"搜索“{query or '全部人员'}”后键盘仍未收起，无法核对人数，已停止；未勾选、未提交本组。")
        if search_issue:
            raise RuntimeError(f"等待搜索页面恢复超时：{search_issue}；未确认“{query or '全部人员'}”的结果，未勾选、未提交本组。")
        if query is not None:
            raise RuntimeError(f"搜索“{query or '全部人员'}”结果加载超时，未确认搜索生效，已停止。")
        raise RuntimeError("等待搜索名单加载完成超时，未确认结果结束，已停止。")

    def _footer(self, xml: str | None = None):
        nodes = _nodes(xml or self._xml())
        if self.task == "credit":
            for rid in (SELECT_ALL_ID, FOOTER_ID):
                hits = [n for n in nodes if n["rid"] == rid
                        and re.fullmatch(r"颁发\s*\d+\s*人", n["text"])]
                if len(hits) == 1:
                    return hits[0]
            return None
        for n in nodes:
            if n["rid"].endswith(":id/tv_next") and n["text"]:
                return n
        return None

    def _empty_results(self, xml):
        """只认人员搜索页明确的空结果，加载中或未知页面不能当作零人。"""
        if "loading_view" in xml or "android.widget.ProgressBar" in xml:
            return False
        nodes = _nodes(xml)
        ids = {n["rid"] for n in nodes}
        return (SEARCH_ID in ids and PKG + ":id/empty_view" in ids
                and any(n["rid"] == PKG + ":id/tv_empty_"
                        and n["text"] == "这里暂无数据，逛逛其它吧！" for n in nodes)
                and not self._rows(xml))

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

            self.credit_score = score
            credit.enter_credit(d, title)
            return self._enter_credit_people()
        else:
            raise RuntimeError(f"未知任务：{task}")
        self._expect_search_box()

    def _enter_credit_people(self):
        import credit
        from roster_plan import parse_score

        projs = credit.projects(self.d)
        if not projs:
            raise RuntimeError("学分管理页没有可颁发的项目")
        requested = parse_score(self.credit_score)
        matches = [p for p in projs if p["score"] is not None and parse_score(p["score"]) == requested]
        if not matches:
            raise RuntimeError(f"找不到分值为 {self.credit_score} 的项目（页面只有：{[p['score'] for p in projs]}）")
        if len(matches) > 1:
            raise RuntimeError(f"存在多个分值为 {self.credit_score} 的项目，无法确定颁发项目，已停止。")
        credit.open_project(self.d, matches[0])
        time.sleep(1.8)
        self._expect_search_box()

    @guarded("开始分组")
    def begin_group(self, query):
        from device_checks import wait_count

        self.query = ""
        # search 会用 set_text 完整替换旧查询，不必先提交空搜索加载全部人员。
        n = wait_count(self.count)
        if n != 0:
            raise RuntimeError(f"本组开始前已有 {n} 人被勾选，已停止")

    def count(self) -> int:
        """只查底部那一个控件，比整页 dump 快一个数量级。"""
        if self.task == "credit":
            for rid in (SELECT_ALL_ID, FOOTER_ID):
                footer = self.d(resourceId=rid)
                if footer.exists:
                    text = (footer.info.get("text") or "").strip()
                    match = re.fullmatch(r"颁发\s*(\d+)\s*人", text)
                    if match:
                        return int(match.group(1))
            if self._empty_results(self._xml()):
                return 0
            raise PageNotReady("发分人员页尚未出现「颁发 N 人」计数按钮")
        try:
            footer = self.d(resourceId=FOOTER_ID)
            if not footer.exists:
                if self._empty_results(self._xml()):
                    return 0
                raise PageNotReady("底部计数按钮还没出现")
            info = footer.info
        except Exception as exc:  # noqa: BLE001
            raise PageNotReady(f"底部计数按钮还没出现（{type(exc).__name__}）") from exc
        text = (info.get("text") or "").strip()
        m = re.search(r"(\d+)", text)
        if not m:
            raise PageNotReady(f"底部按钮文字异常：{text!r}")
        return int(m.group(1))

    @guarded("搜索名单")
    def search(self, query, *, target_names=()):
        from roster_workflow import Candidate

        self.query = query
        first_rows = self._type_search(query)
        sid = query if re.fullmatch(r"\d{11}", query) else ""
        # 姓氏查找可按本组目标结束；学号查询仍完整检查结果。
        rows = self._collect(first_rows=first_rows, target_names=() if sid else target_names)
        # 保持当前位置，select 优先处理当前屏幕；只有目标不在当前屏幕时才回找。
        return [Candidate(key=f"{r['name']}\x1f{r['info']}", name=r["name"], sid=sid) for r in rows]

    @guarded("勾选一人")
    def select(self, key, *, expected_before=None):
        grade = re.match(r"(20\d{2})级", key.partition("\x1f")[2]) if self.task == "credit" else None
        for _ in range(2):
            pages = self._walk_rows(max_grade=grade.group(1)) if grade else self._walk_rows()
            for rows in pages:
                hits = [r for r in rows if f"{r['name']}\x1f{r['info']}" == key]
                if len(hits) > 1:
                    raise RuntimeError("搜索结果中存在无法区分的同名同班人员，未勾选。")
                if hits:
                    from device_checks import wait_count

                    before = wait_count(self.count)
                    if expected_before is not None and before != expected_before:
                        raise RuntimeError("勾选计数发生意外变化，已停止。")
                    # 点击前重新确认当前行；只点击一次，等真实人数 +1。
                    fresh = [r for r in self._ready_rows() if f"{r['name']}\x1f{r['info']}" == key]
                    if len(fresh) != 1:
                        raise RuntimeError("人员行发生变化，未勾选。")
                    self.check_stop()
                    self.d.click(fresh[0]["cx"], fresh[0]["cy"])
                    return wait_count(self.count, expected=before + 1)
            if _ == 0:
                self._back_to_top()
        raise RuntimeError(f"搜索结果里找不到这一行：{key.split(chr(31))[0]}")

    @guarded("按年级勾选发分人员")
    def select_credit_grade(self, want=None, *, observe=None):
        """复用人员行解析和遍历，边读年级边勾选，最后由用户在手机提交。"""
        from device_checks import wait_count

        if self.task != "credit" or (want is not None and not re.fullmatch(r"20\d{2}", str(want))):
            raise ValueError("请在发分人员页选择全部或四位年级。")
        if not any(_search_matches(n, "") for n in _nodes(self._xml())):
            raise RuntimeError("人员页仍有搜索条件，请清除后重新开始按年级勾选。")
        if wait_count(self.count) != 0:
            raise RuntimeError("当前已有勾选，请先核对或取消，避免混入其他人员。")
        self._back_to_top()
        picked, seen = [], set()
        for rows in self._walk_rows(max_grade=want):
            if wait_count(self.count) != len(picked):
                raise RuntimeError("发分人数与本轮选择不一致，已停止。")
            for row in rows:
                self.check_stop()
                key = (row["name"], row["info"])
                if key in seen:
                    continue
                seen.add(key)
                if want and row["grade"] != want:
                    if observe:
                        observe(row, "跳过", "非本次目标年级" if row["grade"] else "未识别到年级")
                    continue
                if observe:
                    observe(row, "点击待核对", "即将勾选，尚未提交发分。")
                self.select(f"{row['name']}\x1f{row['info']}")
                picked.append(row)
                if observe:
                    observe(row, "已勾选未提交", "勾选人数增加1，请自行在手机上完成提交。")
                print(f"[按年级勾选 {len(picked)}] {row['name']}（{row['grade'] or '未知'}级）")
            print(f"[年级扫描] 已检查 {len(seen)} 人，已勾选 {len(picked)} 人")
        if wait_count(self.count) != len(picked):
            raise RuntimeError("结束时发分人数与本轮选择不一致，已停止。")
        self.check_stop()
        print(f"[OK] 年级检查结束，共勾选 {len(picked)} 人；未提交发分。")
        return picked

    @guarded("清空勾选")
    def clear_selection(self):
        if self.count() == 0:
            return
        if self.task == "credit":
            import credit

            # 此页 tv_step 是颁发，不是全选。返回积分卡片再打开，取消未提交的勾选。
            self.d.press("back")
            for _ in range(20):
                time.sleep(.25)
                if credit.projects(self.d):
                    break
            else:
                raise RuntimeError("未确认返回积分项目页，已停止；请人工检查勾选状态。")
            self._enter_credit_people()
            if self.count() != 0:
                raise RuntimeError("重新进入发分人员页后仍有勾选，已停止。")
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

        if self.task == "admit":
            return Receipt(submitted=True)
        if self.task == "credit":
            return Receipt(before=before, after=before, submitted=True)

        confirmed = self._verify_makeup(students)
        if len(confirmed) != len(students):
            missing = [s["name"] for s in students if all(s["name"] != c[0] for c in confirmed)]
            raise RuntimeError(f"提交后只复核到 {len(confirmed)}/{len(students)} 人，未找到：{'、'.join(missing[:5])}")
        return Receipt(identities=tuple(confirmed), before=before,
                       after=before + len(confirmed), submitted=True)

    def end_group(self):
        if self.task == "credit":
            if not self.d(resourceId=SEARCH_ID).exists:
                self._enter_credit_people()
            self.clear_selection()
            return
        try:
            self.clear_selection()
        except RuntimeError:
            pass

    # ---------- 内部 ----------
    def _expect_search_box(self, timeout=8):
        if not self.d(resourceId=SEARCH_ID).wait(timeout):
            raise RuntimeError("当前页没有搜索框，可能没进到人员列表")

    @contextmanager
    def input_session(self):
        """记录当前输入法供按键定位使用，全程不更改输入法。"""
        if self._input_session_active:
            yield
            return
        original_ime = self.d.current_ime()
        if not original_ime or "/" not in original_ime:
            raise RuntimeError("无法读取当前输入法，未执行搜索。")
        self._original_ime = original_ime
        try:
            self._input_session_active = True
            yield
        finally:
            self._input_session_active = False

    def _native_search_key(self):
        """先读取按键控件，读不到时只在实际键盘区域识别搜索文字。"""
        from keyboard_search import keyboard_region, find_search_key

        package = self._original_ime.split("/")[0]
        xml = self._xml()
        positions = set()
        for node in ET.fromstring(xml).iter("node"):
            if (node.get("package") != package or node.get("clickable") != "true"
                    or node.get("enabled", "true") != "true"
                    or node.get("visible-to-user", "true") != "true"):
                continue
            labels = {(node.get(key) or "").strip().casefold() for key in ("text", "content-desc")}
            if not labels.intersection({"搜索", "search"}):
                continue
            b = [int(x) for x in re.findall(r"-?\d+", node.get("bounds", ""))]
            if len(b) == 4 and 0 <= b[0] < b[2] and 0 <= b[1] < b[3]:
                positions.add(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
        if len(positions) == 1:
            return next(iter(positions))
        if positions:
            raise RuntimeError("当前键盘存在多个搜索键，无法确定点击位置，已停止。")
        region = keyboard_region(xml, package)
        if region is None:
            raise RuntimeError("无法确认当前键盘的按键区域，未点击、未切换输入法。")
        key = find_search_key(self.d.screenshot(), region)
        fresh = self._xml()
        if keyboard_region(fresh, package) != region:
            raise RuntimeError("识别期间键盘位置改变，未点击搜索。")
        before = [(n['text'], n['hint']) for n in _nodes(xml) if n['rid'] == SEARCH_ID]
        after = [(n['text'], n['hint']) for n in _nodes(fresh) if n['rid'] == SEARCH_ID]
        if len(before) != 1 or before != after:
            raise RuntimeError("识别期间搜索页面或查询内容改变，未点击搜索。")
        return key

    def _type_search(self, query):
        self.check_stop()
        box = self.d(resourceId=SEARCH_ID)
        if not box.wait(5):
            raise RuntimeError("找不到搜索框")
        with self.input_session():
            box.click()
            time.sleep(0.3)
            try:
                box.set_text(query)
            except Exception as exc:
                raise RuntimeError("无法直接填写搜索框，已停止；输入法保持不变。") from exc
            actual = box.get_text()
            empty_hint = actual != query and not query and any(
                _search_matches(n, query) for n in _nodes(self._xml()))
            if actual != query and not empty_hint:
                raise RuntimeError(f"搜索框未正确填入“{query or '空查询'}”，未执行搜索。")
            # 重新聚焦，显示当前输入法的搜索键。
            box.click()
            time.sleep(0.3)
            if query:
                print(f"[搜索名单] {query}")
            key = self._native_search_key()
            self.d.click(*key)
            time.sleep(.3)
            rows = self._ready_rows(timeout=12, query=query)
            if query:
                print(f"[OK] 搜索“{query}”结果已加载")
            return rows

    def _clear_search(self):
        box = self.d(resourceId=SEARCH_ID)
        if not box.wait(3):
            return
        self._type_search("")

    def _walk_rows(self, limit=160, *, max_grade=None, first_rows=None):
        """仅遍历当前查询结果；三次下滑没有新人员才返回，不能静默截断。"""
        seen, streak = set(), 0
        last_grade, boundary_enabled = "", bool(max_grade)
        for page in range(limit):
            self.check_stop()
            rows = first_rows if page == 0 and first_rows is not None else self._ready_rows()
            if not rows:
                return
            if boundary_enabled and any(not re.fullmatch(r"20\d{2}", r.get("grade", "")) for r in rows):
                rows = self._ready_rows()  # 年级暂不可读先重读，不把它当作越界。
                if not rows:
                    boundary_enabled = False
            keys = [(r["name"], r["info"]) for r in rows]
            if not self.unique_names and len(keys) != len(set(keys)):
                raise RuntimeError("搜索结果中存在无法区分的同名同班人员，已停止。")
            fresh = set(keys) - seen
            if boundary_enabled:
                grades = [r.get("grade", "") for r in rows]
                new_grades = [r.get("grade", "") for r in rows if (r["name"], r["info"]) in fresh]
                if (any(not re.fullmatch(r"20\d{2}", g) for g in grades)
                        or grades != sorted(grades)
                        or (new_grades and last_grade and new_grades[0] < last_grade)):
                    boundary_enabled = False
                    print("[年级边界] 年级不明或出现倒序，本次恢复完整扫描")
                elif new_grades:
                    last_grade = new_grades[-1]
            seen.update(keys)
            streak = streak + 1 if page > 0 and not fresh else 0
            if streak >= 3:
                if self.unique_names:
                    yield rows  # 最后一屏也须检查同名重复行。
                return
            yield rows
            if isinstance(rows, SingleScreenRows):
                return
            self.check_stop()
            # 目标已出现后须检查剩余姓名，即使另一位同名者在后续年级。
            if any(name in self.unique_names for name, _ in keys):
                boundary_enabled = False
            if boundary_enabled and last_grade > max_grade:
                print(f"[年级边界] 已读到 {last_grade} 级，超过本组 {max_grade} 级，结束本次查找")
                return
            self._swipe_down()
        raise RuntimeError("达到搜索扫描上限但未确认结果结束，已停止，不能视为全部名单处理完成。")

    def _collect(self, limit=160, *, target_names=(), first_rows=None):
        seen, order = set(), []
        previous_page, ambiguous = set(), set()
        remaining = set(target_names)
        max_grade = self.search_max_grade if self.task == "credit" else None
        for rows in self._walk_rows(limit, max_grade=max_grade, first_rows=first_rows):
            page = set()
            for row in rows:
                key = (row["name"], row["info"])
                if key not in seen:
                    seen.add(key)
                    order.append(row)
                elif row["name"] in self.unique_names and key not in ambiguous and (key in page or key not in previous_page):
                    # 同屏两行相同，或离开视野后再次出现。相邻屏重叠本身不算重名。
                    order.append(row)
                    ambiguous.add(key)
                page.add(key)
                remaining.discard(row["name"])
            previous_page = page
            if target_names and not remaining:
                print(f"[名单姓名已出现] {'、'.join(sorted(target_names))}；按完整姓名匹配并勾选")
                break
        return order

    def _swipe_down(self):
        from makeup import _scroll_people

        _scroll_people(self.d)

    def _back_to_top(self):
        """沿当前查询结果回到顶部，不切换到已签到或录取页签。"""
        from makeup import _scroll_people

        previous, unchanged = None, 0
        for _ in range(160):
            self.check_stop()
            rows = self._ready_rows()
            if not rows:
                return
            mark = tuple((r["name"], r["info"], r["cy"]) for r in rows)
            unchanged = unchanged + 1 if mark == previous else 0
            if isinstance(rows, SingleScreenRows) or unchanged >= 3:
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

    def _verify_makeup(self, students):
        """切到「已签到」页，逐人复核补签结果。"""
        el = self.d(text="已签到")
        if not el.wait(6):
            raise RuntimeError("找不到「已签到」页签，无法复核")
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
            matches = [r for r in rows if r["name"] == s["name"]]
            if len(matches) == 1:
                confirmed.append((s["name"], s.get("sid", "")))
        self._clear_search()
        back = self.d(text="未签到")
        if back.wait(4):
            back.click()
            time.sleep(1.3)
        return confirmed


def create_backend(device):
    """roster_workflow.BACKEND_FACTORY 的实现：接收已连接设备，返回后端。"""
    return DeviceBackend(device)
