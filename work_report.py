"""工作结果留档与 Excel 导出；只处理本地记录，不连接手机。"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from roster_plan import _append, _save
from submission_log import atomic_json

HEADER = ["序号", "姓名", "学号", "班级／分组", "结果", "说明", "时间"]
EXCEPTION_COLORS = {
    "未找到": "FFF2CC", "重名跳过": "E6D9F5", "跳过": "E2E8F0",
    "失败": "F8CECC", "未处理": "FCE4D6", "已勾选未提交": "DDEBF7",
    "演练未提交": "E2F0F5", "点击待核对": "FBE2C5", "提交结果待核对": "F4C6D7",
}


class WorkReport:
    def __init__(self, out_dir, task, title, mode, *, condition="", inputs=()):
        now = datetime.now()
        self.path = Path(out_dir) / "工作记录" / (now.strftime("%Y%m%d-%H%M%S-%f") + ".json")
        self.data = dict(task=task, title=title, mode=mode, condition=condition,
                         started=now.isoformat(timespec="seconds"), ended="", state="进行中",
                         note="", rows=[], inputs=[str(Path(p).resolve()) for p in inputs])
        self.save()

    def save(self):
        atomic_json(self.path, self.data)

    def observe(self, person, status, note):
        row = [0, person["name"], "", person["info"], status, note,
               datetime.now().isoformat(timespec="seconds")]
        key = (row[1], row[3])
        for index, old in enumerate(self.data["rows"]):
            if (old[1], old[3]) == key:
                row[0] = old[0]
                self.data["rows"][index] = row
                return
        row[0] = len(self.data["rows"]) + 1
        self.data["rows"].append(row)

    def read_feedback(self, result):
        for path in (result.feedback_path, result.plan_path):
            self.data["inputs"].append(str(Path(path).resolve()))
        wb = load_workbook(result.feedback_path, read_only=True, data_only=True)
        try:
            rows = list(wb.worksheets[0].values)[1:]
            self.data["rows"] = [list(row) for row in rows]
            for row in self.data["rows"]:
                if not row[4]:
                    row[4] = "演练未提交"
        finally:
            wb.close()
        wb = load_workbook(result.plan_path, read_only=True, data_only=True)
        try:
            for _, name, sid, reason in list(wb["处理日志"].values)[1:]:
                self.data["rows"].append([len(self.data["rows"]) + 1, name, sid, "名单校验",
                                          "跳过", reason, self.data["started"]])
        finally:
            wb.close()

    def finish(self, state, note=""):
        self.data.update(state=state, note=note, ended=datetime.now().isoformat(timespec="seconds"))
        self.save()


def read_report(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def update_makeup(path, title, count, *, error=""):
    """仅更新已留档且人数相符的勾选结果，不据人数猜测人员身份。"""
    data = read_report(path)
    rows = [r for r in data["rows"] if r[4] == "已勾选未提交"]
    if data["title"] != title or len(rows) != count:
        raise ValueError("本次补签与工作名单不一致，不能将该名单标为已提交。")
    status = "提交结果待核对" if error else "已提交未核查"
    for row in rows:
        row[4:7] = [status, error or "补签动作已提交，尚未逐人核查已签到名单。",
                    datetime.now().isoformat(timespec="seconds")]
    data.update(state="本次工作已停止" if error else "本次工作完成",
                note=error or "补签已提交，结果尚未逐人核查。",
                ended=datetime.now().isoformat(timespec="seconds"))
    atomic_json(path, data)


def makeup_submission(out_dir, path, title, count, error):
    if path:
        data = read_report(path)
        selected = sum(r[4] == "已勾选未提交" for r in data["rows"])
        if data["title"] == title and selected and (error or selected == count):
            update_makeup(path, title, selected, error=error)
            return path
    report = WorkReport(out_dir, "补签", title, "提交补签")
    report.data["submitted_count"] = count
    report.finish("本次工作已停止" if error else "本次工作完成",
                  error or "已提交未核查；缺少可对应的逐人勾选记录，不推测人员名单。")
    return report.path


def export_report(path, destination):
    data = read_report(path)
    output = Path(destination).resolve()
    if output.suffix.lower() != ".xlsx":
        raise ValueError("请选择 .xlsx 格式保存。")
    if output in [Path(p).resolve() for p in data["inputs"]]:
        raise ValueError("不能覆盖输入名单或原始反馈表，请换一个文件名。")
    wb = Workbook()
    summary = wb.active
    summary.title = "任务汇总"
    _append(summary, ["项目", "内容"])
    for label, key in (("活动", "title"), ("工作类型", "task"), ("执行方式", "mode"),
                       ("年级／分值", "condition"), ("开始时间", "started"), ("结束时间", "ended"),
                       ("工作状态", "state"), ("说明", "note")):
        _append(summary, [label, data[key]])
    _append(summary, ["记录人数", len(data["rows"])])
    if data.get("submitted_count") is not None:
        _append(summary, ["已提交未核查人数", data["submitted_count"]])
    for status, count in Counter(r[4] for r in data["rows"]).items():
        _append(summary, [status, count])
    exceptions = [row for row in data["rows"] if row[4] in EXCEPTION_COLORS]
    _append(summary, [])
    _append(summary, [f"异常及未执行人员（{len(exceptions)} 条）"])
    summary.cell(summary.max_row, 1).font = Font(bold=True, color="203864")
    _append(summary, ["姓名", "具体原因", "工作状态", "学号", "班级／分组", "原序号"])
    header_row = summary.max_row
    for cell in summary[header_row]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="365675")
    for seq, name, sid, group, status, reason, _ in exceptions:
        _append(summary, [name, reason, status, sid, group, seq])
        for cell in summary[summary.max_row]:
            cell.fill = PatternFill("solid", fgColor=EXCEPTION_COLORS[status])
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    summary.auto_filter.ref = f"A{header_row}:F{summary.max_row}"
    detail = wb.create_sheet("人员明细")
    pending = wb.create_sheet("待核对人员")
    for ws in (detail, pending):
        _append(ws, HEADER)
    for row in data["rows"]:
        _append(detail, row)
    for row in exceptions:
        _append(pending, row)
    return _save(wb, output)
