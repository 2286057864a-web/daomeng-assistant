"""发分人员页真实控件布局回归，不连接手机。"""
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import credit
import roster_device as rd
from device_checks import PageNotReady

PEOPLE = (Path(__file__).parent/'fixtures/credit_people.xml').read_text(encoding='utf8')


class CreditPeopleTests(unittest.TestCase):
    def backend(self, count=0):
        d=Mock()
        xml=PEOPLE.replace('颁发0人',f'颁发{count}人')
        def element(*,resourceId):
            e=Mock()
            e.exists=True
            e.info={'text':f'颁发{count}人' if resourceId==rd.PKG+':id/tv_step' else '一键发放'}
            return e
        d.side_effect=element
        b=rd.DeviceBackend(d);b.task='credit';b.credit_score='3.0'
        b._xml=Mock(return_value=xml)
        return b,d

    def test_credit_count_comes_from_left_button(self):
        for count in (0,1,12):
            b,d=self.backend(count)
            self.assertEqual(b.count(),count)
            self.assertNotIn(rd.FOOTER_ID,[c.kwargs.get('resourceId') for c in d.call_args_list])

    def test_credit_footer_is_selected_people_button(self):
        b,_=self.backend(2)
        footer=b._footer()
        self.assertEqual(footer['rid'],rd.PKG+':id/tv_step')
        self.assertEqual(footer['text'],'颁发2人')

    def test_submit_uses_left_button_and_never_one_click_grant(self):
        b,d=self.backend(2)
        b._confirm_dialog=Mock()
        with patch.object(rd.guard,'begin_submit'),patch.object(rd.guard,'end_submit'),patch.object(rd.time,'sleep'):
            receipt=b.submit(students=[{'name':'测试甲'},{'name':'测试乙'}],score='3')
        self.assertTrue(receipt.submitted)
        d.click.assert_called_once_with(359,3013)

    def test_credit_dry_clear_returns_and_reopens_without_clicking_count(self):
        b,d=self.backend(2)
        b.count=Mock(side_effect=[2,0])
        b._enter_credit_people=Mock()
        d.dump_hierarchy.return_value='<hierarchy><node text="积分 3.0"/></hierarchy>'
        with patch.object(credit,'projects',return_value=[{'score':3}]),patch.object(rd.time,'sleep'):
            b.clear_selection()
        d.press.assert_called_once_with('back')
        b._enter_credit_people.assert_called_once_with()
        d.click.assert_not_called()
        self.assertFalse(d.called)

    def test_zero_selection_never_submits_or_goes_back(self):
        b,d=self.backend()
        with self.assertRaisesRegex(RuntimeError,'没有勾选'):
            b.submit(students=[],score='3')
        b.clear_selection()
        d.click.assert_not_called();d.press.assert_not_called()

    def test_wrong_count_text_still_fails(self):
        b,d=self.backend()
        d.side_effect=None;d.return_value.exists=True;d.return_value.info={'text':'查看'}
        with self.assertRaises(PageNotReady): b.count()

    def test_next_group_reopens_if_submit_returned_to_project_page(self):
        b,d=self.backend()
        d.side_effect=None;d.return_value.exists=False
        b._enter_credit_people=Mock();b._clear_search=Mock();b.clear_selection=Mock()
        b.end_group()
        b._enter_credit_people.assert_called_once_with()
        b._clear_search.assert_not_called()
        b.clear_selection.assert_called_once_with()

    def test_two_dry_groups_select_clear_and_continue_without_awarding(self):
        b,d=self.backend()
        state={'count':0,'people':True}
        def element(*,resourceId):
            e=Mock();e.exists=state['people']
            e.info={'text':f"颁发{state['count']}人" if resourceId==rd.SELECT_ALL_ID else '一键发放'}
            return e
        d.side_effect=element
        def click(x,y):
            if (x,y)==(100,200):state['count']+=1
            elif (x,y)==(300,400):state['people']=True
            else:raise AssertionError('触碰了颁发按钮')
        d.click.side_effect=click
        d.press.side_effect=lambda key:state.update(count=0,people=False)
        b._expect_search_box=Mock();b._type_search=Mock()
        b._rows=Mock(return_value=[])
        row={'name':'测试甲','info':'测试班','cx':100,'cy':200}
        b._collect=Mock(return_value=[row]);b._back_to_top=Mock()
        b._walk_rows=lambda:iter([[row]])
        b._ready_rows=Mock(return_value=[row])
        with patch.object(credit,'projects',return_value=[dict(name='项目',score=3,btn=(300,400))]), \
             patch.object(rd.time,'sleep'):
            for query in ('20230000001','20230000002'):
                b.begin_group(query)
                hits=b.search(query)
                b.select(hits[0].key)
                self.assertEqual(b.count(),1)
                b.clear_selection()
                self.assertEqual(b.count(),0)
                b.end_group()
        self.assertEqual(d.press.call_count,2)

    def test_submit_from_credit_page_can_reopen_for_next_group(self):
        b,d=self.backend(1)
        b._confirm_dialog=Mock()
        with patch.object(rd.guard,'begin_submit'),patch.object(rd.guard,'end_submit'),patch.object(rd.time,'sleep'):
            b.submit(students=[{'name':'测试甲'}],score='3')
        d.side_effect=None;d.return_value.exists=False
        b._enter_credit_people=Mock();b._clear_search=Mock();b.clear_selection=Mock()
        b.end_group()
        b._enter_credit_people.assert_called_once()
        d.click.assert_called_once_with(359,3013)

    def test_failed_dry_clear_stops_without_touching_grant_button(self):
        b,d=self.backend(1)
        b._enter_credit_people=Mock()
        with patch.object(credit,'projects',return_value=[{'score':3}]),patch.object(rd.time,'sleep'), \
             self.assertRaisesRegex(RuntimeError,'仍有勾选'):
            b.clear_selection()
        d.click.assert_not_called()


if __name__=='__main__':unittest.main()
