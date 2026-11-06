import unittest
from datetime import datetime

from temporal_agent.errors import TemporalError
from temporal_agent.temporal import Window, extract_time_expressions, shift

REF = "2158-08-18 00:00:00"


class TemporalTests(unittest.TestCase):
    def parse(self, text, reference=REF):
        result = extract_time_expressions(text, reference)
        self.assertEqual(len(result), 1)
        return result[0]

    def test_retains_offset_and_absolute_year(self):
        three = self.parse("admissions since 3 years ago")
        five = self.parse("admissions since 5 years ago")
        self.assertEqual(three.amount, 3)
        self.assertEqual(three.window.start, "2155-08-18 00:00:00")
        self.assertEqual(five.window.start, "2153-08-18 00:00:00")
        self.assertEqual(self.parse("since 2105").window.start, "2105-01-01 00:00:00")

    def test_since_last_year_has_one_unbounded_constraint(self):
        item = self.parse("admissions since last year")
        self.assertEqual(item.window.start, "2157-01-01 00:00:00")
        self.assertIsNone(item.window.end)

    def test_calendar_and_rolling_are_distinct(self):
        cal = self.parse("in the last year").window
        rolling = self.parse("in the last 1 year").window
        self.assertEqual((cal.start, cal.end), ("2157-01-01 00:00:00", "2158-01-01 00:00:00"))
        self.assertEqual((rolling.start, rolling.end), ("2157-08-18 00:00:00", REF))
        self.assertFalse(cal.end_inclusive)
        self.assertTrue(rolling.end_inclusive)

    def test_last_three_months(self):
        item = self.parse("in the last 3 months")
        self.assertEqual((item.amount, item.unit), (3, "month"))
        self.assertEqual(item.window.start, "2158-05-18 00:00:00")

    def test_until_date_includes_entire_named_day(self):
        window = self.parse("until 2024-02-29").window
        self.assertTrue(window.contains(datetime(2024, 2, 29, 23, 59, 59)))
        self.assertFalse(window.contains(datetime(2024, 3, 1)))

    def test_until_relative_includes_exact_boundary(self):
        window = self.parse("until 1 year ago").window
        self.assertTrue(window.contains(datetime(2157, 8, 18)))
        self.assertFalse(window.contains(datetime(2157, 8, 18, 0, 0, 1)))

    def test_leap_day_clamps(self):
        self.assertEqual(shift(datetime(2024, 2, 29, 12), -1, "year"), datetime(2023, 2, 28, 12))
        self.assertEqual(shift(datetime(2024, 3, 31), -1, "month"), datetime(2024, 2, 29))

    def test_week_starts_on_monday(self):
        self.assertEqual(self.parse("last week", "2024-03-06 12:00:00").window.start, "2024-02-26 00:00:00")

    def test_multiple_nonoverlapping_constraints(self):
        items = extract_time_expressions("since 2105 until 2106", REF)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[1].window.end, "2107-01-01 00:00:00")

    def test_invalid_dates_and_zero_windows(self):
        for q in ("since 2023-02-29", "last 0 months"):
            with self.subTest(q=q), self.assertRaises(TemporalError):
                extract_time_expressions(q, REF)
        with self.assertRaises(TemporalError):
            Window(start="2024-02-01", end="2024-01-01")

    def test_reference_is_required_and_not_system_clock(self):
        with self.assertRaises(TemporalError):
            extract_time_expressions("last year", "")
        self.assertEqual(self.parse("since 2105", "2020-01-01").window,
                         self.parse("since 2105", "2158-08-18").window)


if __name__ == "__main__":
    unittest.main()
