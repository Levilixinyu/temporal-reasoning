import copy
import unittest
from temporal_agent.semantic_adjudication import aggregate, blinded_packet, digest, rubric_digest


class SemanticEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.sql = 'SELECT n FROM events WHERE t >= 10 AND t < 20'
        self.q = {'question':'Use [10,20).', 'reference_time':None}
        self.r = dict(axis='temporal_correctness',question_sha256=digest(self.q['question']),
            coverage_status='reviewed',leaves=[{'id':'lower'},{'id':'upper'}])
        self.a = dict(axis='temporal_correctness',question_sha256=self.r['question_sha256'],
            sql_sha256=digest(self.sql),rubric_sha256=rubric_digest(self.r),
            reviewer={'id':'test-reviewer','method':'human_review'},decisions=[dict(leaf_id=k,decision='pass',
                rationale='The active predicate implements this endpoint.',
                sql_evidence=[dict(span=[0,len(self.sql)],text=self.sql)]) for k in ('lower','upper')])

    def score(self): return aggregate(self.r,self.sql,self.a)

    def test_complete_evidence(self):
        self.assertTrue(self.score()['reportable'])
        self.assertTrue(self.score()['match'])

    def test_result_match_cannot_fill_missing_review(self):
        self.a={'execution_match':True}
        self.assertIsNone(self.score()['match'])

    def test_missing_duplicate_and_extra_leaves_rejected(self):
        original=copy.deepcopy(self.a)
        for decisions in (original['decisions'][:1], [original['decisions'][0]]*2,original['decisions']*2):
            self.a['decisions']=decisions
            self.assertEqual(self.score()['status'],'invalid_adjudication')

    def test_stale_sql_even_if_output_unchanged(self):
        self.sql+=' -- changed submission'
        self.assertIsNone(self.score()['match'])

    def test_stale_rubric(self):
        self.r['leaves'].append({'id':'null'})
        self.assertIsNone(self.score()['match'])

    def test_cross_axis_rejected(self):
        self.a['axis']='template_alignment'
        self.assertIsNone(self.score()['match'])

    def test_uncertainty_does_not_pass(self):
        self.a['decisions'][1]['decision']='unresolved'
        self.a['decisions'][1]['sql_evidence']=[]
        self.assertIsNone(self.score()['match'])
        self.assertFalse(self.score()['reportable'])

    def test_one_counterexample_disproves_conjunction(self):
        self.a['decisions'][0]['decision']='fail'
        self.a['decisions'][1]['decision']='unresolved'
        self.assertFalse(self.score()['match'])
        self.assertTrue(self.score()['reportable'])

    def test_no_na_override(self):
        self.a['decisions'][0]['decision']='not_applicable'
        self.assertIsNone(self.score()['match'])

    def test_fabricated_or_empty_evidence_rejected(self):
        for evidence in ([],[dict(span=[0,6],text='DELETE')],[dict(span=[True,6],text='ELECT')]):
            self.a['decisions'][0]['sql_evidence']=evidence
            self.assertIsNone(self.score()['match'])

    def test_draft_and_calibration_cannot_be_formal(self):
        self.r['coverage_status']='draft';self.a['rubric_sha256']=rubric_digest(self.r)
        self.assertFalse(self.score()['reportable'])
        self.r['coverage_status']='reviewed';self.a['rubric_sha256']=rubric_digest(self.r)
        self.a['reviewer']['method']='developer_calibration'
        self.assertFalse(self.score()['reportable'])

    def test_blinding_ignores_extra_fields(self):
        self.q.update(condition='D',explanation='trust me',gold_sql='secret',result=[[1]])
        packet=blinded_packet(self.q,self.sql,self.r)
        for forbidden in ('condition','explanation','gold_sql','result'):self.assertNotIn(forbidden,packet)


if __name__=='__main__':unittest.main()
