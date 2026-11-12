"""历史 bug 回归：纯假设备、临时文件，不连接手机、不修改在用源码。"""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import xml.etree.ElementTree as ET

import device_checks as dc
import roster_workflow as flow
from submission_log import check_previous
from verify_roster import Candidate, FakeBackend, read, workbook


def quick_count(read, **kwargs):
    return dc.wait_count(read, timeout=.04, interval=.001, **kwargs)


def run(path, out, backend, *, task="credit", score="0.5"):
    with redirect_stdout(StringIO()), patch.object(flow, "wait_count", side_effect=quick_count):
        return flow.run_task(path, task, "活动甲", out, score=score, real=True,
                             confirmed=True, backend_factory=lambda: backend)


def main():
    with tempfile.TemporaryDirectory(prefix="roster-fixes-") as temporary:
        root = Path(temporary)
        path = workbook(root / "名单.xlsx", [["姓名", "学号"], ["张甲", "20230000001"]])
        person = Candidate("a", "张甲", "20230000001")

        class NoToast(FakeBackend):
            def submit(self, **kwargs):
                super().submit(**kwargs)
                return flow.Receipt(submitted=True)

        backend = NoToast([person])
        result = run(path, root / "direct", backend)
        assert not result.error and result.submitted == 1 and result.confirmed == 0
        assert read(result.feedback_path)["反馈"][1][4] == "已提交未核查"
        assert len(read(result.feedback_path)["待核对"]) == 1
        retry = run(path, root / "direct", backend, score="0.50")
        assert "阻止再次提交" in retry.error and len(backend.submissions) == 1
        print("[OK] 缺 toast/人数增量不阻断正式发分，明确未核查；等价分值重复提交被拦截")

        class DelayedFooter(NoToast):
            def count(self):
                self.calls = getattr(self, "calls", 0) + 1
                if self.calls <= 3:
                    raise dc.PageNotReady("loading_view")
                return super().count()

        result = run(path, root / "loading", DelayedFooter([person]))
        assert not result.error and result.submitted == 1
        values = iter([0, 0, 0, 1, 1])
        assert dc.wait_count(lambda: next(values), timeout=.1, interval=.001, expected=1) == 1
        with patch.object(dc.time, "sleep"):
            try:
                dc.wait_count(lambda: (_ for _ in ()).throw(RuntimeError("连接断开")), timeout=.1)
            except RuntimeError as exc:
                assert str(exc) == "连接断开"
            else:
                raise AssertionError("非加载错误不应被忽略")
        print("[OK] 加载完成后才读人数；勾选后等待真实 +1；断连错误立即停止")

        class Unfiltered(NoToast):
            def search(self, query):
                return self.candidates

        unfiltered = Unfiltered([person, Candidate("b", "李乙", "20230000002")])
        result = run(path, root / "unfiltered", unfiltered)
        assert "未按查询过滤" in result.error and not unfiltered.clicks and not unfiltered.submissions
        print("[OK] 搜索未生效时不勾选、不提交")

        class TimeoutAfterSubmit(NoToast):
            def submit(self, **kwargs):
                super().submit(**kwargs)
                raise TimeoutError("等待返回学分页超时")

        backend = TimeoutAfterSubmit([person])
        result = run(path, root / "timeout", backend)
        assert "可能已经提交" in result.error
        row = read(result.feedback_path)["反馈"][1]
        assert row[4] == "提交结果待核对" and "直接重试" in row[5]
        assert len(read(result.feedback_path)["待核对"]) == 2
        assert json.loads(next((root / "timeout/提交记录").glob("*.json")).read_text(encoding="utf-8"))["state"] == "提交结果待核对"
        retry = run(path, root / "timeout", backend)
        assert "阻止再次提交" in retry.error and len(backend.submissions) == 1
        print("[OK] 提交后导航异常保留待核对状态，无重复 submit；下次运行仍拦截")

        class Crash(BaseException):
            pass

        class CrashAfterIntent(NoToast):
            def submit(self, **kwargs):
                records = list((root / "crash/提交记录").glob("*.json"))
                assert len(records) == 1
                assert json.loads(records[0].read_text(encoding="utf-8"))["state"] == "提交结果待核对"
                feedback = next((root / "crash").glob("反馈-*.xlsx"))
                assert read(feedback)["反馈"][1][4] == "提交结果待核对"
                raise Crash()

        backend = CrashAfterIntent([person])
        try:
            run(path, root / "crash", backend)
        except Crash:
            pass
        else:
            raise AssertionError("退出模拟未触发")
        retry = run(path, root / "crash", backend)
        assert "阻止再次提交" in retry.error
        failure = NoToast([person])
        with patch("submission_log.atomic_json", side_effect=PermissionError("写盘失败")):
            result = run(path, root / "disk", failure)
        assert "写盘失败" in result.error and not failure.submissions
        print("[OK] submit 前 JSON 与逐人反馈已经落盘；异常退出后可恢复；写盘失败禁止提交")

        class WrongReceipt(FakeBackend):
            def submit(self, **kwargs):
                super().submit(**kwargs)
                return flow.Receipt((), 0, 1, submitted=True)

        result = run(path, root / "admit", WrongReceipt([person]), task="admit")
        assert "提交结果待核对" in result.error
        print("[OK] 录取/名单补签继续复核；复核不一致也不等同于未提交失败")

        class FailReturning(NoToast):
            def end_group(self):
                raise RuntimeError("返回页面失败")

        result = run(path, root / "returned", FailReturning([person]))
        assert read(result.feedback_path)["反馈"][1][4] == "已提交未核查"
        assert result.submitted == 1 and result.error
        print("[OK] 已确认发出提交动作后，导航失败不覆盖已提交状态")

        def xml(footer="补签 1 人", dialog=None, bounds="[50,50][150,100]", signed=False):
            root_node = ET.Element("hierarchy")
            ET.SubElement(root_node, "node", {"resource-id": "app:id/tv_next", "text": footer,
                                              "bounds": "[0,200][200,250]", "enabled": "true"})
            if signed:
                ET.SubElement(root_node, "node", {"text": "已签到", "selected": "true"})
            if dialog:
                ET.SubElement(root_node, "node", {"resource-id": "android:id/message", "text": dialog})
                ET.SubElement(root_node, "node", {"resource-id": "android:id/button1", "text": "确定", "bounds": bounds})
            return ET.tostring(root_node, encoding="unicode")

        class MakeupDevice:
            def __init__(self, dialogs=False, moving=False, unknown=False, stuck=False):
                self.clicks = []
                self.frames = 0
                self.dialogs, self.moving, self.unknown, self.stuck = dialogs, moving, unknown, stuck

            def dump_hierarchy(self):
                if not self.clicks or self.stuck:
                    return xml()
                if len(self.clicks) == 1 and self.dialogs:
                    self.frames += 1
                    bounds = "[50,10][150,30]" if self.moving and self.frames <= 2 else "[50,50][150,100]"
                    return xml(dialog="确定删除？" if self.unknown else "该列表共 5 人，已选中 1 人，确定补签？", bounds=bounds)
                return xml(footer="补签 0 人", signed=True)

            def click(self, *point):
                self.clicks.append(point)

        with patch.dict("sys.modules", {"guard": SimpleNamespace(ensure_foreground=lambda d: None)}):
            direct = MakeupDevice()
            count, record = dc.submit_selected_makeup(direct, "活动甲", root / "makeup-direct", timeout=.1, interval=.001)
            assert count == 1 and len(direct.clicks) == 1
            assert json.loads(record.read_text(encoding="utf-8"))["state"] == "已提交未核查"
            popup = MakeupDevice(dialogs=True, moving=True)
            dc.submit_selected_makeup(popup, "活动甲", root / "makeup-popup", timeout=.1, interval=.001)
            assert popup.clicks == [(100, 225), (100, 75)]
            assert popup.frames >= 5
            for device, folder in ((MakeupDevice(dialogs=True, unknown=True), "unknown"),
                                   (MakeupDevice(stuck=True), "stuck")):
                try:
                    dc.submit_selected_makeup(device, "活动甲", root / folder, timeout=.015, interval=.001)
                except RuntimeError as exc:
                    assert "可能已经补签" in str(exc)
                else:
                    raise AssertionError("不确定界面不应报成功")
                assert len(device.clicks) == 1
        print("[OK] 补签直接完成不找弹窗；弹窗只核对选中人数，位置稳定才点；未知或卡住界面不重试")

    print("历史风险回归全部通过；未操作真实手机。")


if __name__ == "__main__":
    main()
