import sqlite3
import unittest

from temporal_agent.evaluation import compare_labeled_window, compare_rows, evaluate_sql_pair, execute_readonly_sql, summarize


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE admissions (subject_id INTEGER, admittime TEXT)")
        self.conn.executemany("INSERT INTO admissions VALUES (?,?)", [(1, "2105-01-01"), (2, "2105-08-03")])

    def tearDown(self):
        self.conn.close()

    def test_alias_difference_does_not_change_answer(self):
        row = evaluate_sql_pair(self.conn, "SELECT count(*) AS n FROM admissions", "SELECT count(*) AS total FROM admissions")
        self.assertTrue(row["match"])

    def test_invalid_gold_is_not_a_model_failure(self):
        row = evaluate_sql_pair(self.conn, "SELECT subject_id FROM admissions", "SELECT age FROM admissions")
        self.assertEqual(row["status"], "invalid_gold")
        self.assertIsNone(row["match"])
        summary = summarize([row, {"status": "evaluated", "match": True}])
        self.assertEqual(summary["accuracy"], 1)
        self.assertEqual((summary["total"], summary["evaluated"], summary["invalid_gold"]), (2, 1, 1))

    def test_failed_prediction_stays_in_denominator(self):
        row = evaluate_sql_pair(self.conn, "SELECT age FROM admissions", "SELECT subject_id FROM admissions")
        self.assertEqual(row["status"], "prediction_error")
        self.assertFalse(row["match"])

    def test_missing_temporal_label_is_not_success(self):
        self.assertEqual(compare_labeled_window(None, None)["status"], "not_evaluated")
        self.assertIsNone(compare_labeled_window({}, None)["match"])
        gold = {"start": "2105-01-01", "end": None, "start_inclusive": True, "end_inclusive": False}
        self.assertFalse(compare_labeled_window(None, gold)["match"])
        self.assertFalse(compare_labeled_window({**gold, "start": "2105-08-18"}, gold)["match"])

    def test_duplicates_case_nulls_and_order(self):
        self.assertFalse(compare_rows([[1], [1]], [[1]]))
        self.assertFalse(compare_rows([["A"]], [["a"]]))
        self.assertTrue(compare_rows([[None], [1]], [[1], [None]]))
        self.assertFalse(compare_rows([[1], [2]], [[2], [1]], order_sensitive=True))
        self.assertFalse(compare_rows([["1"]], [[1]]))

    def test_multiple_column_shape_even_for_empty_results(self):
        row = evaluate_sql_pair(self.conn, "SELECT subject_id, admittime FROM admissions WHERE 0", "SELECT subject_id FROM admissions WHERE 0")
        self.assertFalse(row["match"])

    def test_boundary_fixture_distinguishes_equal_from_strict(self):
        row = evaluate_sql_pair(self.conn, "SELECT subject_id FROM admissions WHERE admittime > '2105-01-01'",
                                "SELECT subject_id FROM admissions WHERE admittime >= '2105-01-01'")
        self.assertFalse(row["match"])

    def test_write_operations_and_multiple_statements_are_denied(self):
        for sql in ("DELETE FROM admissions", "WITH x AS (SELECT 1) DELETE FROM admissions",
                    "SELECT 1; DROP TABLE admissions", "PRAGMA writable_schema=ON", "SELECT load_extension('x')"):
            with self.subTest(sql=sql):
                self.assertFalse(execute_readonly_sql(self.conn, sql)["success"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM admissions").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
