"""Question-only ordinal regression cases; no benchmark answers are imported."""
import unittest
from temporal_agent.temporal_pipeline import analyze


class OrdinalTests(unittest.TestCase):
    def orders(self, question):
        return [s for s in analyze(question)['plan'] if s['type']=='event_order']

    def test_numeric_ranks_and_directions(self):
        for rank, ordinal in enumerate(('1st', '2nd', '3rd', '4th'), 1):
            for adjective, direction in [('earliest','ascending'), ('latest','descending')]:
                for event in ('hospital admission', 'ICU stay'):
                    with self.subTest(ordinal=ordinal, adjective=adjective, event=event):
                        steps=self.orders(f'Return the {ordinal} {adjective} {event}.')
                        self.assertEqual(len(steps),1)
                        self.assertEqual((steps[0]['rank'],steps[0]['direction']),(rank,direction))

    def test_word_and_multidigit_ordinals(self):
        for ordinal, rank in [('second',2),('fourth',4),('11th',11),('21st',21),('3-th',3)]:
            steps=self.orders(f'Select the {ordinal} most recent ICU stay.')
            self.assertEqual(len(steps),1)
            self.assertEqual((steps[0]['rank'],steps[0]['direction']),(rank,'descending'))

    def test_separate_orders_do_not_collapse(self):
        steps=self.orders('Compare the 2nd latest ICU stay and the first admission.')
        self.assertEqual([(s['rank'],s['direction']) for s in steps],[(2,'descending'),(1,'ascending')])

    def test_event_window_is_not_a_rank(self):
        self.assertEqual(self.orders('Prescriptions in the first 48 hours after admission.'),[])

    def test_no_unstated_partition_or_ties(self):
        step=self.orders('Select the 3rd earliest ICU stay.')[0]
        self.assertEqual(step['partition'],'not inferred')
        self.assertEqual(step['ties'],'not specified')
        self.assertTrue(analyze('Select the 3rd earliest ICU stay.')['unresolved'])

    def test_spans_preserve_full_ordinal(self):
        q='Return the 2nd latest ICU stay.'
        step=self.orders(q)[0]
        self.assertEqual(step['text'],'2nd latest ICU stay')
        self.assertEqual(q[slice(*step['span'])],step['text'])


if __name__=='__main__':
    unittest.main()
