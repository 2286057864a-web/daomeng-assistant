"""年级边界与协作停止，保留当前勾选及已提交记录。"""
import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import Mock,patch
import roster_device as rd
import roster_workflow as rw
from device_checks import TaskStopped
import test_credit_grade as cg
from test_credit_grade import row
from verify_roster import FakeBackend,Candidate,workbook,read


class BoundaryStopTests(unittest.TestCase):
    def backend(self,pages):return cg.CreditGradeTests().backend(pages)

    def test_grade_boundary_processes_same_screen_targets_without_further_swipe(self):
        b,s=self.backend([[row('甲','2023'),row('乙','2024')],[row('丙','2025')]])
        with patch('device_checks.time.sleep'):
            self.assertEqual([p['name'] for p in b.select_credit_grade('2023')],['甲'])
        self.assertEqual(s['swipes'],0)
        b.d.click.assert_called_once()

    def test_surname_search_stops_at_batch_max_grade_not_first_other_grade(self):
        for maximum,expected_swipes in [('2023',1),('2024',2)]:
            b,s=self.backend([[row('陈甲','2023')],[row('陈乙','2024')],[row('陈丙','2025')]])
            b.search_max_grade=maximum
            b._type_search=Mock(return_value=None)
            found=b.search('陈',target_names={'陈未找到'})
            self.assertEqual(s['swipes'],expected_swipes)
            self.assertIn('陈甲',[p.name for p in found])

    def test_unknown_or_observed_reverse_disables_cutoff(self):
        for first in ([row('未知','')],[row('乙','2024'),row('甲','2023')]):
            b,s=self.backend([first,[row('后续','2023')]])
            with patch('device_checks.time.sleep'):
                found=b.select_credit_grade('2023')
            self.assertIn('后续',[r['name'] for r in found])
            self.assertGreaterEqual(s['swipes'],4)

    def test_unreadable_grade_is_reread_before_boundary(self):
        b,s=self.backend([[row('甲','2023'),row('乙','2024')]])
        snapshots=iter([[row('甲','2023'),row('乙','')]])
        ready=b._ready_rows
        b._ready_rows=lambda:next(snapshots,ready())
        with patch('device_checks.time.sleep'):
            found=b.select_credit_grade('2023')
        self.assertEqual(len(found),1);self.assertEqual(s['swipes'],0)

    def test_stop_after_grade_click_records_it_and_never_swipes_or_reclicks(self):
        b,s=self.backend([[row('甲','2023'),row('乙','2023')]])
        stop=threading.Event();observe=Mock()
        click=b.d.click.side_effect
        def clicked(*args):click(*args);stop.set()
        b.d.click.side_effect=clicked
        def check():
            if stop.is_set():raise TaskStopped('用户停止')
        b.check_stop=check
        with patch('device_checks.time.sleep'),self.assertRaises(TaskStopped):
            b.select_credit_grade('2023',observe=observe)
        self.assertEqual(s['count'],1);self.assertEqual(s['swipes'],0)
        self.assertIn('已勾选未提交',[c.args[1] for c in observe.call_args_list])

    def run_roster(self,stop_when):
        stop=threading.Event()
        class Backend(FakeBackend):
            def select(self,key,**kwargs):
                result=super().select(key,**kwargs)
                if stop_when=='select':stop.set()
                return result
            def submit(self,**kwargs):
                result=super().submit(**kwargs)
                if stop_when=='submit':
                    stop.set();self.check_stop()  # 提交核对期间不被打断
                return result
        backend=Backend([Candidate('a','陈甲','20230000001'),Candidate('b','陈乙','20240000001')])
        def check():
            if stop.is_set():raise TaskStopped('用户停止')
        with tempfile.TemporaryDirectory() as tmp,patch('roster_workflow.wait_count',side_effect=lambda fn,**kw:fn()):
            path=workbook(Path(tmp)/'名单.xlsx',[['姓名','学号'],['陈甲','20230000001'],['陈乙','20240000001']])
            result=rw.run_task(path,'credit','测试',tmp,score=3,real=True,confirmed=True,backend_factory=lambda:backend,check_stop=check)
            data=read(result.feedback_path)
            statuses=[r[4] for r in next(iter(data.values()))[1:]]
        return backend,result,statuses

    def test_roster_stop_preserves_selected_feedback_without_submission(self):
        b,result,status=self.run_roster('select')
        self.assertEqual(b.search_max_grade,'2024')
        self.assertEqual(b.selected,['a']);self.assertFalse(b.submissions)
        self.assertEqual(status,['已勾选未提交','未处理']);self.assertIn('用户停止',result.error)

    def test_stop_during_submit_finishes_record_and_does_not_begin_cleanup(self):
        b,result,status=self.run_roster('submit')
        self.assertEqual(len(b.submissions),1);self.assertEqual(result.submitted,2)
        self.assertEqual(status,['已提交未核查']*2)
        self.assertEqual(b.selected,['a','b']);self.assertNotIn('提交结果待核对',result.error)

    def test_select_earlier_candidate_does_not_scan_later_grades_before_returning_top(self):
        b,s=self.backend([[row('陈甲','2023')],[row('陈乙','2024')],[row('陈丙','2025')]])
        s['page']=1
        b._back_to_top=lambda:s.update(page=0)
        with patch('device_checks.time.sleep'):
            b.select('陈甲\x1f2023级·测试班')
        self.assertEqual(s['swipes'],0)
        b.d.click.assert_called_once()

    def test_roster_missing_person_continues_to_next_surname_with_own_boundary(self):
        captures=[]
        class Backend(FakeBackend):
            def search(self,query,**kwargs):
                captures.append((query,self.search_max_grade))
                return super().search(query,**kwargs)
        b=Backend([Candidate('b','张乙','20240000001')])
        with tempfile.TemporaryDirectory() as tmp,patch('roster_workflow.wait_count',side_effect=lambda fn,**kw:fn()):
            path=workbook(Path(tmp)/'名单.xlsx',[['姓名','学号'],['陈甲','20230000001'],['张乙','20240000001']])
            result=rw.run_task(path,'credit','测试',tmp,score=3,real=True,confirmed=True,backend_factory=lambda:b)
            statuses=[r[4] for r in read(result.feedback_path)['反馈'][1:]]
        self.assertEqual(captures,[('陈','2023'),('张','2024')])
        self.assertEqual(statuses,['未找到','已提交未核查'])
        self.assertFalse(result.error)

    def test_stop_before_scan_sends_no_swipe_or_click(self):
        b,s=self.backend([[row('甲','2023')]])
        b.check_stop=Mock(side_effect=TaskStopped('用户停止'))
        with self.assertRaises(TaskStopped):list(b._walk_rows(max_grade='2023'))
        self.assertEqual(s['swipes'],0);b.d.click.assert_not_called()

    def test_gui_stop_reset_allows_next_task(self):
        import gui
        from test_continuous_tasks import ContinuousTasks,ImmediateThread
        fixture=ContinuousTasks();app=fixture.app()
        actions=[]
        def first():
            app._request_stop();app._check_stop();actions.append('unexpected')
        with patch.object(gui.threading,'Thread',ImmediateThread),patch.object(gui.errcatch,'record') as errors:
            app.run(first,stoppable=True);fixture.drain(app)
            self.assertFalse(app.running)
            app.run(lambda:actions.append('next'),stoppable=True);fixture.drain(app)
        self.assertEqual(actions,['next']);self.assertFalse(app._stop_event.is_set())
        errors.assert_not_called()

    def test_stop_after_intent_saved_never_submits(self):
        stop=threading.Event()
        b=FakeBackend([Candidate('a','陈甲','20230000001')])
        original=rw.write_feedback
        def save(rows,*args,**kwargs):
            result=original(rows,*args,**kwargs)
            if any(r[4]=='提交结果待核对' for r in rows):stop.set()
            return result
        def check():
            if stop.is_set():raise TaskStopped('用户停止')
        with tempfile.TemporaryDirectory() as tmp,patch('roster_workflow.wait_count',side_effect=lambda fn,**kw:fn()),patch.object(rw,'write_feedback',side_effect=save):
            path=workbook(Path(tmp)/'名单.xlsx',[['姓名','学号'],['陈甲','20230000001']])
            result=rw.run_task(path,'credit','测试',tmp,score=3,real=True,confirmed=True,backend_factory=lambda:b,check_stop=check)
            data=read(result.feedback_path)
            self.assertEqual(data['反馈'][1][4],'已勾选未提交')
            self.assertEqual(data['待核对'][1][4],'已勾选未提交')
        self.assertFalse(b.submissions);self.assertEqual(b.selected,['a'])
        self.assertNotIn('可能已经提交',result.error)
