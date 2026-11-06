import unittest
from temporal_agent.temporal_pipeline import analyze

class DurationSemanticsTests(unittest.TestCase):
    def nodes(self,q,t):return [n for n in analyze(q)['plan'] if n['type']==t]
    def test_prescription_zero_invalid_duplicates_and_no_clipping(self):
        q='Use individual prescription durations in fractional 24-hour days. Measure each prescribed interval from starttime to stoptime, without clipping it to admission or discharge. Exclude missing endpoints and stop times before start times; retain zero-duration records and count every qualifying prescription row, including repeated rows, separately.'
        n=self.nodes(q,'duration_definition')[0]
        self.assertEqual((n['start'],n['end'],n['unit']),('starttime','stoptime','elapsed_day'))
        self.assertEqual((n['missing_endpoints'],n['reversed_endpoints'],n['zero_duration']),('exclude','exclude','retain'))
        self.assertIs(n['clip_to_encounter'],False)
        self.assertEqual(n['weight'],'each prescription row including duplicates')
        for e in n['evidence']:self.assertEqual(q[slice(*e['span'])],e['text'])
    def test_hospital_duration_counts_each_encounter_once(self):
        q='Duration in fractional 24-hour days. Measure each hospital admission from admittime to dischtime. Use non-null endpoints with end at or after start; retain zero duration. Count each hadm_id once.'
        n=self.nodes(q,'duration_definition')[0]
        self.assertEqual(n['weight'],'each hadm_id once');self.assertEqual(n['zero_duration'],'retain')
    def test_unspecified_invalid_policy_is_not_invented(self):
        n=self.nodes('Measure each ICU stay from intime to outtime.','duration_definition')[0]
        self.assertEqual(n['unit'],'not inferred');self.assertEqual(n['missing_endpoints'],'not inferred')
    def test_bins_preserve_half_open_edges(self):
        q="Restrict exact LOS to [2,11) days and label [2,6) as group_id 'short' and [6,11) as 'long'."
        n=self.nodes(q,'duration_bins')[0]
        self.assertEqual(n['eligibility'],dict(lower=2.,upper=11.,lower_inclusive=True,upper_inclusive=False))
        self.assertEqual(n['bins'][0]['upper'],6);self.assertEqual(n['bins'][1]['lower'],6)
    def test_zero_before_bins_does_not_override_bin_eligibility(self):
        n=self.nodes('LOS is exact elapsed fractional 24-hour days, including zero except where the bins exclude it.','duration_unit_policy')[0]
        self.assertEqual(n['zero_before_bins'],'retain');self.assertFalse(n['truncate'])
    def test_sum_overlapping_stays_without_union(self):
        q='Within that selected admission sum all linked ICU stay durations with non-null endpoints and outtime>=intime, retaining zero durations. Sum recorded stay durations even if intervals overlap.'
        n=self.nodes(q,'duration_aggregation')[0]
        self.assertEqual(n['aggregation'],'sum');self.assertIs(n['merge_overlaps'],False)
        self.assertIn('already selected',n['scope'])
    def test_formula_does_not_swap_endpoints(self):
        q='LOS is (dischtime-admittime) in fractional 24-hour days.'
        n=self.nodes(q,'duration_definition')[0]
        self.assertEqual((n['start'],n['end']),('admittime','dischtime'))
    def test_age_ranges_are_not_duration_bins(self):
        self.assertEqual(self.nodes('Age [20,40), glucose [3,8).','duration_bins'),[])
if __name__=='__main__':unittest.main()
