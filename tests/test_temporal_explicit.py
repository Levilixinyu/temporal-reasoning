"""Independent examples for explicit time extraction, without benchmark files."""
import unittest
from temporal_agent.temporal_pipeline import analyze

class ExplicitTimeTests(unittest.TestCase):
    def nodes(self, question, reference=None, kind=None):
        result=analyze(question,reference)['plan']
        return [n for n in result if kind is None or n['type']==kind]

    def test_timestamp_precision_and_all_boundary_combinations(self):
        for lower,li in [('at or after',True),('strictly after',False)]:
            for upper,ui in [('before',False),('at or before',True)]:
                q=f'Events {lower} 2040-07-03 04:05:06.123456 and {upper} 2040-07-03 04:05:06.123459.'
                nodes=self.nodes(q,kind='absolute_window');self.assertEqual(len(nodes),1)
                self.assertEqual(nodes[0]['window'],dict(start='2040-07-03 04:05:06.123456',end='2040-07-03 04:05:06.123459',start_inclusive=li,end_inclusive=ui))

    def test_between_requires_boundary_policy(self):
        q='Between 2040-01-01 02:00:00 and 2040-01-02 03:00:00'
        self.assertIsNone(self.nodes(q,kind='absolute_window')[0]['window'])
        self.assertTrue(analyze(q)['unresolved'])
        self.assertTrue(self.nodes(q+', including both endpoints',kind='absolute_window')[0]['window']['end_inclusive'])

    def test_single_timestamp_is_not_shifted_to_next_day(self):
        node=self.nodes('After 2044-02-29 12:15:00.',kind='absolute_window')[0]
        self.assertEqual(node['window']['start'],'2044-02-29 12:15:00')
        self.assertFalse(node['window']['start_inclusive'])

    def test_named_dates_months_quarters_years(self):
        for text,start,end in [
            ('on February 29, 2044','2044-02-29 00:00:00','2044-03-01 00:00:00'),
            ('from December 31, 2044 through January 2, 2045','2044-12-31 00:00:00','2045-01-03 00:00:00'),
            ('in February 2044','2044-02-01 00:00:00','2044-03-01 00:00:00'),
            ('in the second calendar quarter of 2044','2044-04-01 00:00:00','2044-07-01 00:00:00'),
            ('in calendar year 2044','2044-01-01 00:00:00','2045-01-01 00:00:00')]:
            with self.subTest(text=text):
                nodes=self.nodes(text,kind='absolute_window');self.assertEqual(len(nodes),1)
                self.assertEqual((nodes[0]['window']['start'],nodes[0]['window']['end']),(start,end))

    def test_previous_quarter_crosses_year_and_suppresses_boundary_phrase(self):
        nodes=self.nodes('During the previous complete calendar quarter, including its start and excluding the start of this quarter?', '2044-01-10 03:00:00', 'relative_calendar')
        self.assertEqual(len(nodes),1)
        self.assertEqual(nodes[0]['window']['start'],'2043-10-01 00:00:00')
        self.assertEqual(nodes[0]['window']['end'],'2044-01-01 00:00:00')

    def test_week_monday_and_today_to_reference(self):
        for expression,start in [('this calendar week','2044-06-13 00:00:00'),('today','2044-06-15 00:00:00')]:
            node=self.nodes(expression+', from midnight inclusive up to the reference time exclusive?', '2044-06-15 07:00:00','relative_calendar')[0]
            self.assertEqual(node['window']['start'],start)
            self.assertEqual(node['window']['end'],'2044-06-15 07:00:00')

    def test_empty_calendar_to_date_is_represented(self):
        node=self.nodes('this calendar month, from its first day at midnight inclusive up to the reference time exclusive?', '2044-06-01 00:00:00','relative_calendar')[0]
        self.assertEqual(node['window']['start'],node['window']['end'])
        self.assertFalse(node['window']['end_inclusive'])

    def test_calendar_without_reference_never_uses_clock(self):
        result=analyze('Events yesterday?')
        self.assertTrue(result['requires_reference']);self.assertIsNone(result['plan'][0]['window'])

    def test_elapsed_windows_and_shared_units(self):
        node=self.nodes('Events in the first 36 elapsed hours after ICU intime.',kind='event_window')[0]
        self.assertEqual((node['amount'],node['anchor']),(36,'ICU intime'))
        node=self.nodes('Compare the first 18 and final 6 elapsed hours.',kind='paired_event_windows')[0]
        self.assertEqual((node['early_amount'],node['late_amount']),(18,6))

    def test_symbolic_constraints_preserve_operators(self):
        node=self.nodes('Require admittime <= charttime < dischtime.',kind='explicit_event_interval')[0]
        self.assertEqual(node['window'],dict(lower={'field':'admittime'},upper={'field':'dischtime'},lower_inclusive=True,upper_inclusive=False))
        node=self.nodes('Measure from starttime to stoptime.',kind='explicit_duration')[0]
        self.assertEqual((node['start'],node['end']),('starttime','stoptime'))

    def test_eligible_result_and_hospital_order(self):
        for text in ['Select the first eligible numeric result per admission.', 'Select their earliest hospital admission.']:
            nodes=self.nodes(text,kind='event_order');self.assertEqual(len(nodes),1)
            self.assertEqual(nodes[0]['rank'],1)
            self.assertEqual(nodes[0]['partition'],'not inferred')

    def test_unrelated_numbers_are_not_time_expressions(self):
        self.assertEqual(analyze('Female patients age 45 through 55, code I50, glucose 7.1.')['templates'],[])

    def test_original_spans_and_no_full_coverage_claim(self):
        text='Count events strictly after 2044-01-01 10:00:00 and before 2044-01-02 11:00:00.'
        result=analyze(text)
        for node in result['plan']:self.assertEqual(text[slice(*node['span'])],node['text'])
        self.assertEqual(result['coverage'],'partial_unvalidated')

if __name__=='__main__':unittest.main()
