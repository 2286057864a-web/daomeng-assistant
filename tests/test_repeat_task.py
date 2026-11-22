"""历史记录不再拦截用户重新运行；只使用假设备和临时目录。"""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import device_checks
import roster_workflow as flow
from submission_log import SubmissionLog
from verify_roster import Candidate, FakeBackend, workbook


class RepeatTaskTests(unittest.TestCase):
    def run_roster(self, root, task):
        source = workbook(root/'名单.xlsx', [['姓名','学号'], ['测试甲','20230000001']])
        backend = FakeBackend([Candidate('row1','测试甲','20230000001')])
        with contextlib.redirect_stdout(io.StringIO()), patch.object(flow,'wait_count',side_effect=lambda fn,**kw:fn()):
            result = flow.run_task(source,task,'目标活动',root,score='3',real=True,
                                   confirmed=True,backend_factory=lambda:backend)
        self.assertEqual(result.error,'')
        self.assertEqual(len(backend.submissions),1)
        return result

    def test_same_person_can_be_run_again_for_all_roster_tasks(self):
        for task in ('credit','admit','makeup'):
            with self.subTest(task=task), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                self.run_roster(root,task)
                original={p:p.read_bytes() for p in (root/'提交记录').glob('*.json')}
                self.run_roster(root,task)
                self.assertEqual(len(list((root/'提交记录').glob('*.json'))),2)
                self.assertTrue(all(p.read_bytes()==data for p,data in original.items()))

    def test_uncertain_submitted_and_legacy_records_do_not_block(self):
        for state in ('已提交未核查','提交结果待核对','已补签','未提交'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                record=SubmissionLog(root,'credit','目标活动','3',[('测试甲','20230000001')])
                record.save(state,'历史记录')
                original=record.path.read_bytes()
                self.run_roster(root,'credit')
                self.assertEqual(record.path.read_bytes(),original)

    def test_unreadable_historical_json_does_not_block_new_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);history=root/'提交记录';history.mkdir()
            damaged=history/'old.json';damaged.write_text('{broken',encoding='utf8')
            self.run_roster(root,'credit')
            self.assertEqual(damaged.read_text(encoding='utf8'),'{broken')

    def test_existing_grade_makeup_history_does_not_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            record=SubmissionLog(tmp,'makeup','目标活动',None,None)
            record.save('已提交未核查','历史补签')
            original=record.path.read_bytes()
            device=Mock();state={'clicked':False}
            def xml():
                count=0 if state['clicked'] else 2
                return f'<hierarchy><node resource-id="app:id/tv_next" text="补签{count}人" bounds="[0,400][200,450]"/></hierarchy>'
            device.dump_hierarchy.side_effect=xml
            device.click.side_effect=lambda *args:state.update(clicked=True)
            with patch('guard.ensure_foreground'):
                count,path=device_checks.submit_selected_makeup(device,'目标活动',tmp,timeout=.2,interval=0)
            self.assertEqual(count,2)
            device.click.assert_called_once_with(100,425)
            self.assertEqual(record.path.read_bytes(),original)
            self.assertEqual(json.loads(path.read_text(encoding='utf8'))['state'],'已提交未核查')


if __name__=='__main__':
    unittest.main()
