"""名单任务的设备无关执行层；设备操作由合并方提供 Backend。

Backend 契约见 设备接口说明.md。此模块不点击手机、不调用旧版的不安全搜索。
"""
from dataclasses import dataclass
from contextlib import nullcontext
from device_checks import wait_count, TaskStopped
import re
from submission_log import SubmissionLog

from roster_plan import (ROSTER_DUPLICATE, feedback_row, output_path, parse_score, prepare_roster,
                         search_kind, write_feedback, write_plan)


TASKS = {"credit": ("发分", "已发分"), "makeup": ("补签", "已补签"),
         "admit": ("录取", "已录取")}
# DeepSeekHarness 合并时赋值为 create_backend(device)；未接入时明确阻止设备操作。
BACKEND_FACTORY = None


@dataclass(frozen=True)
class Candidate:
    key: str
    name: str
    sid: str


@dataclass(frozen=True)
class Receipt:
    identities: tuple = ()
    before: int | None = None
    after: int | None = None
    toast_count: int | None = None
    submitted: bool = False


@dataclass(frozen=True)
class RunResult:
    plan_path: object
    feedback_path: object
    error: str
    confirmed: int
    submitted: int = 0


def match_person(person, candidates, query):
    """姓氏查询按已读取结果中的完整姓名匹配；预处理表指定的学号查询仍核对姓名。"""
    mode = search_kind(query)
    same_name = [c for c in candidates if c.name == person.name]
    if mode == "sid":
        same_sid = [c for c in candidates if c.sid == person.sid]
        if not same_sid:
            if same_name:
                return None, "失败", "姓名存在但学号缺失或不一致，无法确认身份"
            return None, "未找到", "搜索结果没有该学号"
        if len(same_sid) != 1 or same_sid[0].name != person.name:
            return None, "失败", "学号不唯一或姓名不一致，未勾选"
        return same_sid[0], "", ""
    if len(same_name) > 1:
        return None, "失败", "姓氏搜索出现同名多人，跳过，未勾选"
    if not same_name:
        return None, "未找到", "搜索结果没有该姓名"
    return same_name[0], "", ""


def require_backend():
    if BACKEND_FACTORY is None:
        raise RuntimeError("名单处理已完成；新版设备接口尚未合并，未勾选、未提交。请由 DeepSeekHarness 接入设备接口。")
    return BACKEND_FACTORY


def run_task(path, task, title, out_dir, *, score=None, real=False,
             confirmed=False, backend_factory=None, check_stop=lambda: None):
    """自动生成分组表并执行；正式任务必须先获得 GUI 的显式确认。

    backend_factory 为无参数工厂，连接设备也必须延迟到完成预处理之后。
    演练反馈结果留空，说明列明确未提交，绝不写“已发分/已补签/已录取”。
    """
    if task not in TASKS:
        raise ValueError("未知名单任务")
    if real and not confirmed:
        raise ValueError("正式模式必须显式确认")
    score_text = format(parse_score(score), "f") if task == "credit" else None
    if score_text and "." in score_text:
        score_text = score_text.rstrip("0").rstrip(".")
    skip_duplicates = task in {"credit", "admit"}
    plan = prepare_roster(path, allow_name_only=True, skip_duplicate_names=skip_duplicates)
    duplicate_names = {name for _, name, _, reason in plan.logs if reason == ROSTER_DUPLICATE}
    plan_path = write_plan(plan, output_path(out_dir, "名单预处理"))
    print(f"名单预处理：{plan_path}")
    for row, name, sid, reason in plan.logs:
        print(f"跳过第 {row} 行 {name} {sid}：{reason}")
    error, confirmed_count, submitted_count = "", 0, 0
    people = plan.people
    feedback = {p.identity: feedback_row(p, "未处理", "尚未执行") for p in people}
    mode = "-演练" if not real else ""
    feedback_path = output_path(out_dir, f"反馈-{TASKS[task][0]}{mode}")
    pending, record, active = [], None, []

    def save_feedback():
        return write_feedback([feedback[p.identity] for p in people], feedback_path, dry=not real)

    def log_duplicates():
        if duplicate_names:
            print(f"[重名跳过] 共 {len(duplicate_names)} 个姓名：{'、'.join(sorted(duplicate_names))}。均未勾选、未提交。")

    save_feedback()
    if not people and duplicate_names:
        print("名单处理完成：没有可执行的人员，已提交 0 人。")
        log_duplicates()
        print(f"反馈表：{feedback_path}")
        return RunResult(plan_path, feedback_path, "", 0, 0)
    try:
        if not people:
            raise ValueError("名单没有有效人员，请查看处理日志。")
        if backend_factory is None:
            raise RuntimeError("新版设备接口尚未合并，未勾选、未提交。")
        backend = backend_factory()
        backend.check_stop = check_stop
        check_stop()
        backend.open(task=task, title=title, score=score_text)
        with getattr(backend, "input_session", nullcontext)():
            # 补签优先逐人按学号；只有无学号的人员走姓氏搜索。
            batches = ([(p.sid, [p]) for p in people if p.sid] +
                       [(query, [p for p in group if not p.sid]) for query, group in plan.batches()
                        if any(not p.sid for p in group)]) if task == "makeup" else list(plan.batches())
            for query, group in batches:
                check_stop()
                active = group
                search_kind(query)
                print(f"[处理名单] {'、'.join(p.name for p in group)}；查询：{query}")
                backend.begin_group(query)
                if wait_count(backend.count) != 0:
                    raise RuntimeError("本组开始前已有勾选，已停止，避免提交名单外人员。")
                mode = search_kind(query)
                backend.search_max_grade = (max(p.sid[:4] for p in group)
                    if task == "credit" and mode == "surname"
                    and all(re.fullmatch(r"20\d{9}", p.sid) for p in group) else None)
                # 勾选前确认目标完整姓名在搜索结果中唯一，包括后续屏幕。
                backend.unique_names = {p.name for p in group} if skip_duplicates else set()
                target_names = set() if skip_duplicates or mode != "surname" else {p.name for p in group if p.sid}
                candidates = list(backend.search(query, target_names=target_names))
                print(f"[搜索结果] “{query}”已读取 {len(candidates)} 人，正在核对名单")
                if any((c.sid != query if mode == "sid" else query not in c.name) for c in candidates):
                    raise RuntimeError("搜索结果未按查询过滤，已停止，未勾选本组。")
                if wait_count(backend.count) != 0:
                    raise RuntimeError("搜索后已有勾选，已停止，未勾选本组。")
                keys = [(c.key, c.name) for c in candidates]
                if len({c.key for c in candidates}) != (len(set(keys)) if skip_duplicates else len(keys)):
                    raise RuntimeError("搜索结果存在重复行标识，无法可靠定位。")
                picked = []
                selected_keys = set()
                for person in group:
                    check_stop()
                    hit, result, note = match_person(person, candidates, query)
                    if hit is None:
                        if skip_duplicates and sum(c.name == person.name for c in candidates) > 1:
                            result = "重名跳过"
                            duplicate_names.add(person.name)
                        feedback[person.identity] = feedback_row(person, result, note)
                        print(f"[{result}] {person.name}：{note}；未勾选、未提交")
                        continue
                    if hit.key in selected_keys:
                        raise RuntimeError("多个名单身份指向同一人员行，已停止。")
                    before = len(picked)
                    after = backend.select(hit.key, expected_before=before)
                    if after != before + 1:
                        raise RuntimeError(f"勾选 {person.name} 后人数没有 +1（{before} → {after}），已停止，请人工核对当前选择。")
                    picked.append(person)
                    selected_keys.add(hit.key)
                    feedback[person.identity] = feedback_row(person, "已勾选未提交", "人数增加1，尚未提交本组")
                    print(f"[已勾选] {person.name}：人数 {before} → {after}")
                check_stop()
                if wait_count(backend.count) != len(picked):
                    raise RuntimeError("本组总人数不一致，已停止，未提交本组。")
                if not real:
                    backend.clear_selection()
                    if wait_count(backend.count, expected=0) != 0:
                        raise RuntimeError("演练清除勾选失败，已停止，请人工核对。")
                    for person in picked:
                        feedback[person.identity] = feedback_row(
                            person, "", "演练勾选及 +1 校验通过，已取消勾选，未提交", dry=True)
                        print(f"[演练完成] {person.name}：已验证勾选并取消，未提交")
                elif picked:
                    students = [{"name": p.name, "sid": p.sid, "score": score_text} for p in picked]
                    identities = [p.identity for p in picked]
                    record = SubmissionLog(out_dir, task, title, score_text, identities)
                    record.save()  # 写入失败时禁止进入 submit；进程退出也保留待核对意图。
                    pending = list(picked)
                    for person in pending:
                        feedback[person.identity] = feedback_row(person, "提交结果待核对", f"已保存提交意图，可能即将或已经提交；记录：{record.path}")
                    save_feedback()
                    try:
                        check_stop()
                    except TaskStopped:
                        record.save("未提交", "用户在提交前停止，手机勾选保留。")
                        for person in pending:
                            feedback[person.identity] = feedback_row(person, "已勾选未提交", "用户在提交前停止，手机勾选保留。")
                        pending = []
                        raise
                    print(f"[正在提交] {'、'.join(p.name for p in picked)}，共 {len(picked)} 人")
                    # 提交及其核对作为一个操作完成，停止请求在保存结果后响应。
                    backend.check_stop = lambda: None
                    try:
                        receipt = backend.submit(students=students, score=score_text)
                    finally:
                        backend.check_stop = check_stop
                    if receipt.submitted is not True:
                        raise RuntimeError("设备未确认提交动作已完成。")
                    if task == "admit":
                        status, note = "已提交未核查", "已在报名页确认提交，未逐人核查录取结果。"
                    elif task == "credit":
                        status, note = "已提交未核查", "设备确认提交动作完成；未核查 toast、已颁发人数或逐人到账。"
                    else:
                        if (len(receipt.identities) != len(identities) or
                                set(receipt.identities) != set(identities) or
                                type(receipt.before) is not int or type(receipt.after) is not int or
                                receipt.after - receipt.before != len(picked)):
                            raise RuntimeError("提交后人员身份或人数复核不一致。")
                        status, note = TASKS[task][1], "提交后身份及人数复核通过"
                    record.save(status, note)
                    for person in picked:
                        feedback[person.identity] = feedback_row(person, status, note)
                        print(f"[{status}] {person.name}：{note}")
                    submitted_count += len(picked)
                    if task == "makeup":
                        confirmed_count += len(picked)
                    pending = []
                    save_feedback()
                check_stop()
                backend.end_group()
                active = []
                save_feedback()
    except Exception as exc:
        error = str(exc)
        if pending:
            error = f"提交结果待核对：可能已经提交，请人工核对，禁止直接重试。{error}"
            for person in pending:
                feedback[person.identity] = feedback_row(person, "提交结果待核对", error)
            try:
                record.save("提交结果待核对", error)
            except OSError as log_error:
                error += f"（更新记录失败，原提交意图仍保留：{log_error}）"
        else:
            for person in active:
                if feedback[person.identity][4] == "未处理":
                    feedback[person.identity] = feedback_row(person, "未处理" if isinstance(exc, TaskStopped) else "失败", f"未提交本组：{error}")
        print(f"任务停止：{error}")
    for person in people:
        if feedback[person.identity][4] == "未处理":
            feedback[person.identity] = feedback_row(person, "未处理", f"任务停止，尚未提交：{error}")
    feedback_path = save_feedback()
    if not error:
        missing = sum(row[4] == "未找到" for row in feedback.values())
        skipped = sum(row[4] == "失败" for row in feedback.values())
        print(f"名单处理完成：共 {len(people)} 人，未找到 {missing} 人，身份未确认 {skipped} 人，"
              f"已提交 {submitted_count} 人" + ("（演练未提交）" if not real else ""))
    log_duplicates()
    print(f"反馈表：{feedback_path}")
    return RunResult(plan_path, feedback_path, error, confirmed_count, submitted_count)
