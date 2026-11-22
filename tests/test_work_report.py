"""工作结果导出：真实 Excel、真实名单执行层和补签遍历，不连接手机。"""
import contextlib
import io
import itertools
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gui
import makeup
import roster_workflow
import work_report
from verify_roster import Candidate, FakeBackend, read, workbook


class WorkReportTests(unittest.TestCase):
    def test_gui_roster_job_publishes_result_even_if_no_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = workbook(folder / "输入.xlsx", [["姓名", "学号"], ["张甲", "20230000001"]])
            app = SimpleNamespace(running=False, ent_score=SimpleNamespace(get=lambda: "0.5"),
                roster=source, admit_roster=None, makeup_roster=None, selected=lambda: dict(name="测试", state="已结束"),
                _work_result=Mock(), run=lambda job, **kw: job(), _check_stop=lambda: None, _stop_event=gui.threading.Event(), _post=lambda fn, *args: fn(*args),
                connect=Mock(side_effect=RuntimeError("设备未连接")), _show_work_result=Mock())
            with patch.object(gui, "ROOT", folder), patch.object(gui.messagebox, "showerror"), \
                 contextlib.redirect_stdout(io.StringIO()):
                gui.App._run_roster_task(app, "credit", False)
            app._show_work_result.assert_called_once()
            report = work_report.read_report(app._show_work_result.call_args.args[0])
            self.assertEqual(report["state"], "本次工作已停止")
            self.assertIn("设备未连接", report["note"])
            self.assertEqual(report["rows"][0][4], "未处理")

    def test_grade_submission_updates_matching_work_and_does_not_guess_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = work_report.WorkReport(tmp, "补签", "测试", "按年级")
            report.observe(dict(name="甲", info="班级"), "已勾选未提交", "未提交")
            report.finish("勾选完成，尚未提交")
            different = work_report.makeup_submission(tmp, report.path, "其他活动", 1, "")
            self.assertNotEqual(Path(different), report.path)
            self.assertEqual(work_report.read_report(different)["rows"], [])
            self.assertEqual(work_report.read_report(report.path)["rows"][0][4], "已勾选未提交")
            app = SimpleNamespace(running=False, selected=lambda: dict(name="测试", state="已结束"),
                _grade_work_path=report.path, _work_result=Mock(), run=lambda job, **kw: job(), _check_stop=lambda: None, _stop_event=gui.threading.Event(),
                _post=lambda fn, *args: fn(*args), connect=lambda: "offline", _show_work_result=Mock())
            with patch.object(gui, "ROOT", Path(tmp)), patch.object(gui.messagebox, "askyesno", return_value=True), \
                 patch.object(gui.device_checks, "submit_selected_makeup", return_value=(1, "record.json")), \
                 contextlib.redirect_stdout(io.StringIO()):
                gui.App._submit_makeup_checked(app)
            app._show_work_result.assert_called_once_with(report.path)
            self.assertEqual(work_report.read_report(report.path)["state"], "本次工作完成")
            self.assertEqual(work_report.read_report(report.path)["rows"][0][4], "已提交未核查")

    def test_grade_capture_and_submission_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            report = work_report.WorkReport(folder, "补签", "测试活动", "按年级", condition="2023级")
            target = dict(name="测试甲", info="2023级·测试班", grade="2023", cx=10, cy=20)
            other = dict(name="测试乙", info="2024级·测试班", grade="2024", cx=10, cy=40)
            state = dict(count=0)
            def snapshot(d):
                return [target, other], f"补签{state['count']}人"
            device = SimpleNamespace(click=lambda *args: state.update(count=state["count"] + 1))
            with patch.object(makeup, "_ready_snapshot", snapshot), patch.object(makeup, "snap", snapshot), \
                 patch.object(makeup, "reset_top"), patch.object(makeup, "_scroll_people"), \
                 patch.object(makeup.time, "monotonic", side_effect=itertools.count()), \
                 contextlib.redirect_stdout(io.StringIO()):
                picked = makeup.walk_select(device, "2023", observe=report.observe)
            report.finish("勾选完成，尚未提交")
            self.assertEqual(len(picked), 1)
            output = work_report.export_report(report.path, folder / "工作.xlsx")
            rows = read(output)["人员明细"]
            self.assertEqual([(r[1], r[4]) for r in rows[1:]], [("测试甲", "已勾选未提交"), ("测试乙", "跳过")])
            with self.assertRaisesRegex(ValueError, "不一致"):
                work_report.update_makeup(report.path, "测试活动", 2)
            work_report.update_makeup(report.path, "测试活动", 1)
            work_report.export_report(report.path, output)
            self.assertEqual(read(output)["人员明细"][1][4], "已提交未核查")
            self.assertEqual([r[1] for r in read(output)["待核对人员"][1:]], ["测试乙"])

    def test_grade_click_failure_preserves_unconfirmed_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = work_report.WorkReport(tmp, "补签", "测试", "按年级")
            person = dict(name="测试甲", info="测试班", grade="2023", cx=1, cy=2)
            with patch.object(makeup, "_ready_snapshot", return_value=([person], "补签0人")), \
                 patch.object(makeup, "snap", return_value=([person], "补签0人")), \
                 patch.object(makeup, "reset_top"), patch.object(makeup.time, "sleep"), \
                 patch.object(makeup.time, "monotonic", side_effect=itertools.count()), \
                 contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Exception):
                makeup.walk_select(Mock(), "2023", observe=report.observe)
            report.finish("本次工作已停止", "勾选人数未增加")
            result = read(work_report.export_report(report.path, Path(tmp) / "失败.xlsx"))
            self.assertEqual(result["人员明细"][1][4], "点击待核对")
            self.assertEqual(len(result["待核对人员"]), 2)

    def test_all_roster_tasks_export_real_feedback(self):
        for task in ("makeup", "admit", "credit"):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                source = workbook(folder / "输入.xlsx", [["姓名", "学号"],
                    ["李甲", "20230000001"], ["张乙", "20230000002"], ["无效", "abc"]])
                report = work_report.WorkReport(folder, task, "测试", "正式", inputs=[source])
                backend = FakeBackend([Candidate("a", "李甲", "20230000001")])
                with patch.object(roster_workflow, "wait_count", side_effect=lambda fn, **kw: fn()), \
                     contextlib.redirect_stdout(io.StringIO()):
                    result = roster_workflow.run_task(source, task, "测试", folder,
                        score="0.5", real=True, confirmed=True, backend_factory=lambda: backend)
                report.read_feedback(result)
                report.finish("本次工作完成")
                sheets = read(work_report.export_report(report.path, folder / "输出.xlsx"))
                self.assertEqual(list(sheets), ["任务汇总", "人员明细", "待核对人员"])
                self.assertEqual(len(sheets["人员明细"]), 4)
                self.assertEqual(sheets["人员明细"][1][4], "已补签" if task == "makeup" else "已提交未核查")
                self.assertEqual(sheets["人员明细"][2][4], "未找到")
                self.assertEqual(sheets["人员明细"][3][4], "跳过")
                for protected in (source, result.feedback_path, result.plan_path):
                    original = Path(protected).read_bytes()
                    with self.assertRaisesRegex(ValueError, "覆盖"):
                        work_report.export_report(report.path, protected)
                    self.assertEqual(original, Path(protected).read_bytes())

    def test_empty_report_has_summary_and_no_invented_people(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = work_report.WorkReport(tmp, "补签", "测试", "按年级", condition="2023级")
            report.finish("本次工作完成", "没有目标年级待补签人员；未提交。")
            result = read(work_report.export_report(report.path, Path(tmp) / "空结果.xlsx"))
            self.assertIn(("记录人数", 0), [tuple(row[:2]) for row in result["任务汇总"]])
            self.assertEqual(result["任务汇总"][-1][0], "姓名")
            self.assertEqual(result["任务汇总"][-2][0], "异常及未执行人员（0 条）")
            self.assertEqual(len(result["人员明细"]), 1)

    def test_external_text_is_not_excel_formula_and_output_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = work_report.WorkReport(tmp, "补签", "=1+1", "按年级")
            report.observe(dict(name="=2+2", info="测试班"), "跳过", "非目标年级")
            report.finish("本次工作完成")
            output = Path(tmp) / "结果.xlsx"
            work_report.export_report(report.path, output)
            original = output.read_bytes()
            with patch("roster_plan.os.replace", side_effect=PermissionError("被占用")), self.assertRaises(PermissionError):
                work_report.export_report(report.path, output)
            self.assertEqual(output.read_bytes(), original)
            self.assertFalse(list(Path(tmp).glob("*.tmp-*.xlsx")))
            from openpyxl import load_workbook
            wb = load_workbook(output)
            self.assertEqual(wb["人员明细"]["B2"].data_type, "s")
            self.assertEqual(wb["人员明细"].freeze_panes, "A2")
            self.assertEqual(wb["人员明细"].auto_filter.ref, "A1:G2")
            wb.close()

    def test_export_cancel_and_running_do_not_write(self):
        app = SimpleNamespace(running=True, _work_report_path=Path("unused.json"))
        with patch.object(gui.messagebox, "showinfo"), patch.object(gui.filedialog, "asksaveasfilename") as dialog:
            gui.App._export_work(app)
            dialog.assert_not_called()
        app.running = False
        with patch.object(gui.filedialog, "asksaveasfilename", return_value=""), \
             patch.object(work_report, "export_report") as export:
            gui.App._export_work(app)
            export.assert_not_called()

    def test_export_failure_does_not_show_success(self):
        app = SimpleNamespace(running=False, _work_report_path=Path("unused.json"), run=lambda job, **kw: job(), _check_stop=lambda: None, _stop_event=gui.threading.Event(),
                              _post=lambda fn, *args: fn(*args))
        with patch.object(gui.filedialog, "asksaveasfilename", return_value="工作.xlsx"), \
             patch.object(work_report, "export_report", side_effect=PermissionError("占用")), \
             patch.object(gui.messagebox, "showerror") as error, patch.object(gui.messagebox, "showinfo") as success:
            gui.App._export_work(app)
            error.assert_called_once()
            success.assert_not_called()


if __name__ == "__main__":
    unittest.main()
