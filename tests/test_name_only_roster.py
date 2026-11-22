import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from roster_workflow import Candidate, run_task
import test_credit_grade as grade_tests
from test_credit_grade import row
from verify_roster import FakeBackend, read, workbook


class NameOnlyRosterTests(unittest.TestCase):
    def test_skip_excel_and_phone_duplicates_then_submit_unique_person(self):
        for task in ('admit', 'credit'):
            with self.subTest(task=task), TemporaryDirectory() as tmp:
                source = workbook(Path(tmp)/'名单.xlsx', [['姓名'], ['陈甲'], ['陈甲'], ['陈乙'], ['陈丙']])
                backend = FakeBackend([Candidate('a', '陈甲', ''), Candidate('b', '陈乙', ''),
                                       Candidate('b', '陈乙', ''), Candidate('c', '陈丙', '')])
                log = io.StringIO()
                with redirect_stdout(log), patch('roster_workflow.wait_count', side_effect=lambda fn, **kw: fn()):
                    result = run_task(source, task, '测试', tmp, score='3', real=True,
                                      confirmed=True, backend_factory=lambda: backend)
                self.assertFalse(result.error, result.error)
                self.assertEqual(backend.clicks, ['c'])
                self.assertEqual(result.submitted, 1)
                self.assertEqual(backend.submissions[0][0][0]['name'], '陈丙')
                summary = log.getvalue().split('[重名跳过] 共')[-1]
                self.assertIn('2 个姓名', summary)
                self.assertIn('陈甲', summary)
                self.assertIn('陈乙', summary)
                self.assertIn(('陈乙', '重名跳过'), [(r[1], r[4]) for r in read(result.feedback_path)['待核对'][1:]])

    def test_only_duplicates_does_not_open_device_even_with_ids(self):
        with TemporaryDirectory() as tmp:
            source = workbook(Path(tmp)/'名单.xlsx', [['姓名', '学号'], ['张甲', '20240000001'], ['张甲', '20250000001']])
            connect = Mock(side_effect=AssertionError('不应连接设备'))
            result = run_task(source, 'admit', '测试', tmp, real=True, confirmed=True, backend_factory=connect)
            self.assertFalse(result.error, result.error)
            self.assertEqual(result.submitted, 0)
            connect.assert_not_called()

    def test_same_class_duplicates_and_overlapping_pages(self):
        a, b = row('陈甲', '2023'), row('陈乙', '2023')
        pages = [[a, a, b], [b, row('陈丙', '2024')], [row('陈乙', '2025')]]
        backend, _ = grade_tests.CreditGradeTests().backend(pages)
        backend.unique_names = {'陈甲', '陈乙'}
        backend.search_max_grade = '2023'
        backend._type_search = Mock(return_value=None)
        hits = backend.search('陈')
        self.assertEqual([c.name for c in hits].count('陈甲'), 2)
        self.assertEqual([c.name for c in hits].count('陈乙'), 2)
