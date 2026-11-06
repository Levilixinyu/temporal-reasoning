import copy
import unittest
from decimal import Decimal
from temporal_agent.benchmark_scoring import compare_result,score_axes,summarize_first_pass

class ResultScoringTests(unittest.TestCase):
    def config(self):
        return dict(columns=[dict(name='group',kind='string',nullable=False),dict(name='n',kind='integer',nullable=False),dict(name='mean',kind='continuous',nullable=True,abs_tolerance=1e-9,rel_tolerance=1e-9)],row_order='ordered',column_order='named')
    def compare(self,a,b,config=None,columns=None):
        return compare_result(columns or ['group','n','mean'],a,['group','n','mean'],b,config or self.config())
    def test_named_column_alignment(self):
        self.assertTrue(self.compare([[4,'a',2.5]],[['a',4,2.5]],columns=['n','group','mean'])['match'])
    def test_requested_column_order(self):
        cfg=self.config();cfg['column_order']='ordered'
        self.assertFalse(self.compare([[4,'a',2.5]],[['a',4,2.5]],cfg,['n','group','mean'])['match'])
    def test_integer_does_not_use_tolerance(self):
        self.assertFalse(self.compare([['a',1000000001,2]],[['a',1000000000,2]])['match'])
    def test_large_integer_precision(self):
        self.assertFalse(self.compare([['a',2**60+1,2]],[['a',2**60,2]])['match'])
    def test_boolean_and_fractional_count(self):
        for x in [True,1.0000000001,'1']:
            self.assertFalse(self.compare([['a',x,2]],[['a',1,2]])['match'])
    def test_decimal_continuous_tolerance(self):
        self.assertTrue(self.compare([['a',1,Decimal('0.3333333333333333')]],[['a',1,1/3]])['match'])
        self.assertFalse(self.compare([['a',1,.3334]],[['a',1,1/3]])['match'])
    def test_null_is_not_zero(self):
        self.assertFalse(self.compare([['a',1,0]],[['a',1,None]])['match'])
        self.assertTrue(self.compare([['a',1,None]],[['a',1,None]])['match'])
    def test_case_sensitive_labels(self):
        self.assertFalse(self.compare([['A',1,2]],[['a',1,2]])['match'])
    def test_duplicate_multiplicity_and_order(self):
        a=[['a',1,2],['b',2,3]]
        self.assertFalse(self.compare(a[::-1],a)['match'])
        self.assertFalse(self.compare(a+[a[0]],a)['match'])
    def test_empty_shape_not_silent_success(self):
        self.assertFalse(self.compare([],[],columns=['wrong','n','mean'])['match'])
        self.assertTrue(self.compare([],[])['match'])
    def test_nonfinite_values(self):
        for x in [float('nan'),float('inf'),Decimal('NaN')]:
            self.assertFalse(self.compare([['a',1,x]],[['a',1,2]])['match'])
            r=self.compare([['a',1,2]],[['a',1,x]])
            self.assertEqual(r['status'],'invalid_reference');self.assertIsNone(r['match'])
    def test_invalid_reference_shape_or_count(self):
        cfg=self.config();cfg['rows']=1
        self.assertEqual(self.compare([],[],cfg)['status'],'invalid_reference')
    def test_duplicate_columns(self):
        self.assertFalse(self.compare([['a',1,2]],[['a',1,2]],columns=['group','n','n'])['match'])
    def test_bad_config_fails_closed(self):
        cfg=self.config();cfg['columns'][2]['abs_tolerance']=-1
        self.assertEqual(self.compare([],[],cfg)['status'],'invalid_scoring_configuration')
    def test_result_match_does_not_prove_temporal_correctness(self):
        result=score_axes(dict(status='evaluated',match=True))
        self.assertIsNone(result['temporal_correctness']['match'])
        self.assertIsNone(result['template_alignment']['match'])
    def test_denominators_keep_failed_predictions(self):
        rows=[dict(id='a',status='evaluated',match=True),dict(id='b',status='budget_block',match=False),dict(id='c',status='invalid_reference',match=None)]
        result=summarize_first_pass(rows,['a','b','c'])
        self.assertEqual(result['valid_reference_denominator'],2);self.assertEqual(result['first_pass_accuracy'],.5)
    def test_missing_or_duplicate_predictions_block_summary(self):
        with self.assertRaises(ValueError):summarize_first_pass([],['a'])
        with self.assertRaises(ValueError):summarize_first_pass([dict(id='a'),dict(id='a')],['a'])
    def test_failure_cannot_claim_success(self):
        with self.assertRaises(ValueError):summarize_first_pass([dict(id='a',status='execution_failure',match=True)],['a'])
if __name__=='__main__':unittest.main()
