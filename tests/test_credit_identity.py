"""覆盖真实设备搜索与名单执行层之间的身份、项目分值契约；不连接手机。"""
import contextlib
from decimal import Decimal
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import credit
import roster_device
import roster_workflow
from verify_roster import Candidate, FakeBackend, read, workbook


class DeviceSearchBackend(FakeBackend):
    def search(self, query):
        rows = super().search(query)
        backend = roster_device.DeviceBackend(None)
        backend._type_search = lambda query: None
        backend._collect = lambda: [dict(name=c.name, info="测试班") for c in rows]
        backend._back_to_top = lambda: None
        return backend.search(query)


class CreditIdentityTests(unittest.TestCase):
    def test_real_search_missing_sid_is_confirmed_by_student_id(self):
        for task in ("admit", "credit"):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                path = workbook(folder / "名单.xlsx", [["姓名", "学号"],
                    ["李甲", "20230000001"], ["张乙", "20230000002"]])
                backend = DeviceSearchBackend([Candidate("a", "李甲", "20230000001"),
                                               Candidate("b", "张乙", "20230000002")])
                with patch.object(roster_workflow, "wait_count", side_effect=lambda fn, **kw: fn()), \
                     contextlib.redirect_stdout(io.StringIO()):
                    result = roster_workflow.run_task(path, task, "目标", folder,
                        score="0.5", backend_factory=lambda: backend)
                self.assertFalse(result.error)
                self.assertEqual(backend.queries, ["李", "张", "20230000001", "20230000002"])
                self.assertEqual(len(backend.clicks), 2)
                self.assertFalse(backend.selected)
                self.assertFalse(backend.submissions)
                self.assertTrue(all("演练勾选" in row[5] for row in read(result.feedback_path)["演练结果"][1:]))

    def test_fallback_wrong_student_id_does_not_select_or_stop_later_people(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = workbook(folder / "名单.xlsx", [["姓名", "学号"],
                ["李甲", "20230000001"], ["张乙", "20230000002"]])
            backend = DeviceSearchBackend([Candidate("wrong", "李甲", "20230000009"),
                                           Candidate("right", "张乙", "20230000002")])
            with patch.object(roster_workflow, "wait_count", side_effect=lambda fn, **kw: fn()), \
                 contextlib.redirect_stdout(io.StringIO()):
                result = roster_workflow.run_task(path, "credit", "目标", folder,
                    score="0.5", backend_factory=lambda: backend)
            self.assertFalse(result.error)
            self.assertEqual(backend.clicks, ["张乙\x1f测试班"])
            rows = read(result.feedback_path)["演练结果"][1:]
            self.assertEqual(rows[0][4], "未找到")

    def test_decimal_project_score_is_not_truncated(self):
        device = Mock()
        device.dump_hierarchy.return_value = '<hierarchy>' \
            '<node resource-id="app:id/tv_activity_name" text="学分项目" bounds="[0,100][200,140]"/>' \
            '<node resource-id="app:id/tv_score" text="积分 0.50" bounds="[0,150][200,190]"/>' \
            '<node text="颁发" bounds="[200,150][300,190]"/></hierarchy>'
        self.assertEqual(credit.projects(device)[0]["score"], Decimal("0.50"))

    def open_projects(self, scores, requested):
        device = Mock()
        backend = roster_device.DeviceBackend(device)
        backend._expect_search_box = Mock()
        projects = [dict(name="项目", score=score, btn=(100 + i, 200)) for i, score in enumerate(scores)]
        with patch.object(credit, "enter_credit"), patch.object(credit, "projects", return_value=projects), \
             patch.object(roster_device.time, "sleep"):
            backend.open(task="credit", title="目标", score=requested)
        return device

    def test_only_project_with_wrong_score_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "找不到.*0.5"):
            self.open_projects([Decimal("1")], "0.5")

    def test_equivalent_decimal_scores_match(self):
        device = self.open_projects([Decimal("0.50"), Decimal("1")], "0.5")
        device.click.assert_called_once_with(100, 200)

    def test_same_score_multiple_projects_is_ambiguous(self):
        with self.assertRaisesRegex(RuntimeError, "多个.*0.5"):
            self.open_projects([Decimal("0.5"), Decimal("0.50")], "0.5")


if __name__ == "__main__":
    unittest.main()
