"""按年级发分：不搜索、不提交，遍历到末尾并校验人数。"""
import unittest
from unittest.mock import Mock, patch
import roster_device as rd


def row(name, grade):
    return dict(name=name, grade=grade, info=grade+'级·测试班', cx=100, cy=200)


class CreditGradeTests(unittest.TestCase):
    def backend(self, pages):
        b=rd.DeviceBackend(Mock()); b.task='credit'
        b._xml=lambda: f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="搜索" hint="搜索"/></hierarchy>'
        b._back_to_top=Mock(); b._type_search=Mock(side_effect=AssertionError('不应搜索'))
        state={'page':0,'count':0,'swipes':0}
        b._ready_rows=lambda: pages[state['page']]
        def swipe():
            state['swipes']+=1; state['page']=min(state['page']+1,len(pages)-1)
        b._swipe_down=swipe
        b.count=lambda:state['count']
        def click(*args):state['count']+=1
        b.d.click.side_effect=click
        return b,state

    def test_walks_past_other_grades_and_overlap_selects_once_without_search_or_submit(self):
        target=row('目标甲','2023'); other=row('其他','2022')
        pages=[[other],[other],[other],[target],[target,row('目标乙','2023')]]
        b,state=self.backend(pages); observe=Mock()
        with patch('device_checks.time.sleep'):
            picked=b.select_credit_grade('2023',observe=observe)
        self.assertEqual([r['name'] for r in picked],['目标甲','目标乙'])
        self.assertEqual(b.d.click.call_count,2)
        self.assertEqual(state['swipes'],7)
        b._type_search.assert_not_called();b.d.press.assert_not_called()
        self.assertEqual([c.args[1] for c in observe.call_args_list].count('已勾选未提交'),2)

    def test_future_grade_all_and_empty(self):
        for want,pages,expected in [('2027',[[row('新生','2027')]],1),
                                    (None,[[row('甲','2023'),row('乙','2024')]],2),
                                    ('2023',[[row('乙','2024')]],0),('2023',[[]],0)]:
            with self.subTest(want=want,pages=pages),patch('device_checks.time.sleep'):
                b,state=self.backend(pages)
                self.assertEqual(len(b.select_credit_grade(want)),expected)
                self.assertEqual(b.d.click.call_count,expected)

    def test_invalid_grade_existing_search_or_selection_never_click(self):
        for case in ('year','search','selection'):
            b,state=self.backend([[row('甲','2023')]])
            if case=='search': b._xml=lambda:f'<hierarchy><node resource-id="{rd.SEARCH_ID}" text="陈"/></hierarchy>'
            if case=='selection':state['count']=1
            with patch('device_checks.time.sleep'),self.assertRaises((ValueError,RuntimeError)):
                b.select_credit_grade('23' if case=='year' else '2023')
            b.d.click.assert_not_called()

    def test_failed_count_increment_is_not_reclicked(self):
        b,state=self.backend([[row('甲','2023')]])
        def wait(fn,**kwargs):
            if 'expected' in kwargs:raise RuntimeError('人数未增加')
            return fn()
        with patch('device_checks.wait_count',side_effect=wait),self.assertRaisesRegex(RuntimeError,'人数未增加'):
            b.select_credit_grade('2023')
        b.d.click.assert_called_once()

    def test_ambiguous_duplicate_rows_never_click(self):
        b,state=self.backend([[row('甲','2023'),row('甲','2023')]])
        with patch('device_checks.time.sleep'),self.assertRaisesRegex(RuntimeError,'同名同班'):
            b.select_credit_grade('2023')
        b.d.click.assert_not_called()
