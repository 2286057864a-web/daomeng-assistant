import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import roster_plan
import roster_workflow as rw
from verify_roster import FakeBackend, read, workbook


class ContainsBackend(FakeBackend):
    def search(self, query, *, target_names=()):
        return [c for c in self.candidates if
                (c.sid == query if roster_plan.search_kind(query) == "sid" else query in c.name)]


class SearchContainsTests(unittest.TestCase):
    def run_roster(self, root, task, rows, candidates, backend_type=ContainsBackend):
        source = workbook(root / "名单.xlsx", [["姓名", "学号"]] + rows)
        backend = backend_type(candidates)
        with patch.object(rw, "wait_count", side_effect=lambda fn, **kwargs: fn()), \
             contextlib.redirect_stdout(io.StringIO()):
            result = rw.run_task(source, task, "测试活动", root,
                score="0.5" if task == "credit" else None,
                backend_factory=lambda: backend)
        return result, backend

    def test_name_containing_query_is_valid_but_only_exact_roster_person_selected(self):
        for task in ("makeup", "admit", "credit"):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                rows = [["万甲", "" if task == "makeup" else "20230000001"]]
                candidates = [rw.Candidate("other", "杨万盈", "20230000002"),
                              rw.Candidate("target", "万甲", "20230000001")]
                result, backend = self.run_roster(Path(tmp), task, rows, candidates)
                self.assertFalse(result.error, result.error)
                self.assertEqual(backend.clicks, ["target"])
                self.assertFalse(backend.submissions)

    def test_only_non_surname_match_completes_and_continues_next_query(self):
        for task in ("makeup", "admit", "credit"):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                rows = [["万甲", "" if task == "makeup" else "20230000001"],
                        ["张乙", "" if task == "makeup" else "20230000003"]]
                candidates = [rw.Candidate("other", "杨万盈", "20230000002"),
                              rw.Candidate("next", "张乙", "20230000003")]
                result, backend = self.run_roster(Path(tmp), task, rows, candidates)
                self.assertFalse(result.error, result.error)
                self.assertEqual(backend.queries, ["万", "张"])
                self.assertEqual(backend.clicks, ["next"])
                feedback = read(result.feedback_path)["演练结果"]
                self.assertEqual(next(r for r in feedback[1:] if r[1] == "万甲")[4], "未找到")

    def test_unrelated_unfiltered_results_still_stop(self):
        class UnfilteredBackend(ContainsBackend):
            def search(self, query, *, target_names=()):
                return self.candidates
        with tempfile.TemporaryDirectory() as tmp:
            result, backend = self.run_roster(Path(tmp), "makeup", [["万甲", ""]],
                [rw.Candidate("bad", "李乙", "")], UnfilteredBackend)
            self.assertIn("未按查询过滤", result.error)
            self.assertEqual(backend.clicks, [])

    def test_reordered_name_is_not_the_same_person(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, backend = self.run_roster(Path(tmp), "makeup", [["卢露露", ""]],
                [rw.Candidate("other", "露露卢", "")])
            self.assertFalse(result.error, result.error)
            self.assertEqual(backend.clicks, [])
            self.assertEqual(read(result.feedback_path)["演练结果"][1][4], "未找到")


if __name__ == "__main__":
    unittest.main()
