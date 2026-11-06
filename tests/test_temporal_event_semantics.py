"""Independent counterexamples for clipping, partitions and filter order."""
import unittest
from temporal_agent.temporal_pipeline import analyze

class EventSemanticsTests(unittest.TestCase):
    def nodes(self,text,kind):return [n for n in analyze(text)['plan'] if n['type']==kind]

    def test_service_selection_before_service_filter(self):
        q='Match all tables on subject_id and hadm_id. Require the earliest services record with non-null transfertime and curr_service to have curr_service=SURG; order by transfertime then curr_service lexicographically.'
        n=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(n['partition'],['subject_id','hadm_id'])
        self.assertEqual(n['filters_before_selection'],['Non-null transfertime and curr_service'])
        self.assertEqual(n['filters_after_selection'],[dict(field='curr_service',operator='=',value='SURG')])
        self.assertEqual(n['order_by'][1],dict(field='curr_service',direction='ascending'))

    def test_service_partition_not_invented(self):
        q='Require the earliest services record with non-null transfertime and curr_service to have curr_service=MED; order by transfertime then curr_service lexicographically.'
        self.assertEqual(self.nodes(q,'explicit_selection')[0]['partition'],'not inferred')

    def test_clipped_brackets_and_shared_offsets(self):
        q='Compare the first 36 and final 8 elapsed hours. The early window is [admittime, min(dischtime, admittime + early hours)); the late window is [max(admittime, dischtime - late hours), dischtime).'
        a,b=self.nodes(q,'explicit_event_interval')
        self.assertEqual((a['amount'],b['amount']),(36,8))
        self.assertEqual(a['window']['upper'],{'min':[{'field':'dischtime'},{'anchor':'admittime','offset':36,'unit':'hour'}]})
        self.assertEqual(b['window']['lower'],{'max':[{'field':'admittime'},{'anchor':'dischtime','offset':-8,'unit':'hour'}]})
        for n in (a,b):self.assertTrue(n['window']['lower_inclusive']);self.assertFalse(n['window']['upper_inclusive'])

    def test_unbound_or_ambiguous_offset_stays_unresolved(self):
        for prefix in ['', 'First 5 and final 2 hours. First 7 and final 3 hours. ', 'First 4 and final 1 days. ']:
            result=analyze(prefix+'[admittime,min(dischtime,admittime+early hours))')
            node=next(n for n in result['plan'] if n['type']=='explicit_event_interval')
            self.assertIsNone(node['amount']);self.assertEqual(node['resolution'],'unresolved_named_offset')
            self.assertTrue(result['unresolved'])

    def test_literal_icu_interval(self):
        node=self.nodes('Use starttime in [intime, min(outtime, intime+17 hours)).','explicit_event_interval')[0]
        self.assertEqual((node['anchor'],node['clip_at'],node['amount']),('intime','outtime',17))

    def test_icu_natural_language_clipping(self):
        q='First 36 elapsed hours after ICU intime, clipped at outtime: include charttime >= intime and exclude charttime >= either outtime or intime + 36 hours.'
        node=self.nodes(q,'explicit_event_interval')[0]
        self.assertEqual((node['event_field'],node['amount']),('charttime',36))

    def test_lab_encounter_window_with_and_without_extra_limit(self):
        base='Require admittime <= charttime < dischtime.'
        whole=self.nodes(base,'explicit_event_interval')[0]
        short=self.nodes(base+' Also require charttime < admittime + 36 elapsed hours.','explicit_event_interval')[0]
        self.assertEqual(whole['window']['upper'],{'field':'dischtime'})
        self.assertEqual(short['amount'],36)
        self.assertEqual(short['window']['upper']['min'][0],{'field':'dischtime'})

    def test_ordinal_partition_and_ties(self):
        q='For each patient, return the 3rd latest ICU stay by intime. Order by intime descending, breaking tied timestamps by stay_id ascending. Ignore rows with null intime. Do not filter by discharge or end time.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['partition'],['subject_id']);self.assertEqual(node['rank'],3)
        self.assertEqual(node['order_by'],[{'field':'intime','direction':'descending'},{'field':'stay_id','direction':'ascending'}])
        self.assertEqual(node['end_time_filter'],'none')

    def test_lab_validity_precedes_first_and_threshold_follows_first(self):
        q='Select the first eligible numeric result per admission using charttime then labevent_id ascending, after the stated eligibility filters. Only after selecting the first eligible result, retain admissions whose selected value is strictly greater than 0.07 ng/mL.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['partition'],['subject_id','hadm_id'])
        self.assertIn('numeric validity',node['filters_before_selection'][0])
        self.assertEqual(node['filters_after_selection'][0],dict(field='selected_value',operator='>',value=.07,unit='ng/mL'))

    def test_icu_selection_precedes_outtime_validation(self):
        q='Select the earliest non-null intime ICU stay per hospital admission, ordering by intime then stay_id, before checking outtime; never replace a first stay with missing/invalid outtime by a later one.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['filters_before_selection'],['Non-null intime'])
        self.assertTrue(node['no_substitution']);self.assertIn('outtime',node['filters_after_selection'][0])

    def test_measurement_ties_use_value_not_invented_row_key(self):
        q='Use the earliest eligible charttime per stay; if tied, select the smallest eligible converted value at that time.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['order_by'][1]['field'],'converted_value')
        self.assertEqual(node['partition'],['stay_id'])

    def test_first_type_timestamp_used_for_both_windows(self):
        q='For each primitive type, pool all its qualifying prescriptions and use only the earliest start within the admission for both windows; do not replace it with a later start.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['partition'],['subject_id','hadm_id','primitive_type'])
        self.assertIn('both windows',node['filters_after_selection'][0]);self.assertTrue(node['no_substitution'])

    def test_different_filter_order_does_not_trigger_wrong_rule(self):
        q='Exclude missing outtime, then select the earliest ICU stay per admission.'
        self.assertEqual(self.nodes(q,'explicit_selection'),[])

    def test_evidence_spans_are_original_question_text(self):
        q='Use [intime,min(outtime,intime+19 hours)) for events.'
        for n in analyze(q)['plan']:self.assertEqual(q[slice(*n['span'])],n['text'])

    def test_hospital_first_then_duration(self):
        q='For each patient, first select their earliest hospital admission among records with non-null admittime, breaking ties by hadm_id ascending. Then retain that selected admission only if discharge occurred strictly more than 37 elapsed hours after admission.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['filters_before_selection'],['Non-null admittime'])
        self.assertEqual(node['filters_after_selection'][0]['amount'],37)
        self.assertEqual(node['filters_after_selection'][0]['operator'],'>')

    def test_readmission_excludes_overlap_before_finding_next(self):
        q='Search every other admissions row for the same subject, regardless of diagnosis, age, insurance, discharge completeness or survival at that later admission. The event is the earliest non-null admittime strictly after index dischtime; overlapping and exactly simultaneous admissions do not qualify, and must not hide a later eligible event. Count the event if it is at or before dischtime + 17*24 hours.'
        node=self.nodes(q,'explicit_readmission')[0]
        self.assertEqual(node['followup_days'],17)
        self.assertFalse(node['lower_inclusive']);self.assertTrue(node['upper_inclusive'])
        self.assertEqual(node['candidate_cohort_filters'],'none from index cohort')
        self.assertEqual(node['operation_order'][0],'Exclude candidate times <= index discharge')

    def test_icu_across_admissions_is_partitioned_by_patient(self):
        q='For each patient select the earliest non-null ICU intime across all qualifying admissions, ordering by intime then stay_id. After selecting, exclude missing outtime or outtime<intime; never substitute a later stay.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['partition'],['subject_id'])
        self.assertTrue(node['no_substitution'])

    def test_icu_start_after_hospital_admission_clips_at_discharge(self):
        q='ICU stays in the first 9 elapsed hours after their associated hospital admission, including the admission timestamp and excluding the 9-hour endpoint; require the ICU start to be at or after admission and before discharge.'
        node=self.nodes(q,'explicit_event_interval')[0]
        self.assertEqual((node['event_field'],node['anchor'],node['clip_at'],node['amount']),('intime','admittime','dischtime',9))

    def test_first_class_pools_drugs_before_window_membership(self):
        q='For each primitive class, pool prescriptions of all its drugs and use the single earliest eligible start within the admission for both windows.'
        node=self.nodes(q,'explicit_selection')[0]
        self.assertEqual(node['partition'][-1],'primitive_class')
        self.assertIn('same earliest',node['filters_after_selection'][0])

if __name__=='__main__':unittest.main()
