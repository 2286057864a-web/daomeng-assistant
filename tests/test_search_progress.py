import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import guard
import roster_workflow as rw
from test_credit_identity import DeviceSearchBackend
from verify_roster import workbook, read


class SearchProgressTests(unittest.TestCase):
    def test_full_name_selection_happens_before_next_surname(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = workbook(root/'名单.xlsx', [['姓名', '学号'],
                ['邓测试', '20250000001'], ['万测试', '20240000002']])
            backend = DeviceSearchBackend([rw.Candidate('a', '邓测试', '20250000001'),
                                          rw.Candidate('b', '万测试', '20240000002')])
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(rw, 'wait_count', side_effect=lambda fn, **kw: fn()):
                result = rw.run_task(source, 'credit', '测试活动', root, score='3',
                                     backend_factory=lambda: backend)
            self.assertFalse(result.error)
            self.assertEqual(backend.queries, ['邓', '万'])
            log = output.getvalue()
            self.assertNotIn('[核对身份]', log)
            self.assertIn('[已勾选] 邓测试', log)
            self.assertIn('[演练完成] 邓测试', log)
            self.assertLess(log.index('[演练完成] 邓测试'), log.index('[已勾选] 万测试'))
            self.assertFalse(backend.submissions)

    def test_submission_is_logged_before_next_page_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = workbook(root/'名单.xlsx', [['姓名', '学号'], ['邓测试', '20250000001']])
            backend = DeviceSearchBackend([rw.Candidate('a', '邓测试', '20250000001')])
            original_end = backend.end_group
            def end():
                if backend.submissions:
                    raise RuntimeError('下一页面未就绪')
                original_end()
            backend.end_group = end
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(rw, 'wait_count', side_effect=lambda fn, **kw: fn()):
                result = rw.run_task(source, 'credit', '测试活动', root, score='3', real=True,
                                     confirmed=True, backend_factory=lambda: backend)
            self.assertEqual(len(backend.submissions), 1)
            self.assertIn('[已提交未核查] 邓测试', output.getvalue())
            self.assertIn('下一页面未就绪', result.error)
            self.assertEqual(read(result.feedback_path)['反馈'][1][4], '已提交未核查')

    def test_foreground_error_uses_last_checked_state(self):
        with patch.object(guard, 'foreground', side_effect=['']*8+[guard.PKG]) as check, \
             patch.object(guard.time, 'sleep'), self.assertRaisesRegex(guard.GuardError, '读不到'):
            guard.ensure_foreground(Mock())
        self.assertEqual(check.call_count, 8)


if __name__ == '__main__':
    unittest.main()
