import copy
import json
import sqlite3
import unittest
from pathlib import Path

from temporal_agent.errors import ExecutionLimitError, PlanValidationError, TemporalError, UnsupportedQuestion
from temporal_agent.executor import Executor
from temporal_agent.plan import validate_plan
from temporal_agent.planner import plan_question, readmission_plan

REF = "2158-08-18 00:00:00"


def fixture():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE admissions (subject_id INTEGER, hadm_id INTEGER, admittime TEXT, dischtime TEXT, admission_location TEXT, admission_type TEXT)")
    rows = [
        (1, 11, "2157-12-29 00:00:00", "2158-01-01 00:00:00"),
        (1, 12, "2158-01-01 00:00:00", "2158-01-01 12:00:00"),
        (1, 13, "2158-01-02 00:00:00", "2158-01-03 00:00:00"),
        (1, 14, "2158-01-31 00:00:00", "2158-02-01 00:00:00"),
        (2, 21, "2157-12-29 00:00:00", "2158-01-01 00:00:00"),
        (2, 22, "2158-01-31 00:00:00", "2158-02-01 00:00:00"),
        (3, 31, "2157-12-29 00:00:00", "2158-01-01 00:00:00"),
        (3, 32, "2158-01-31 00:00:01", "2158-02-01 00:00:00"),
        (4, 41, "2158-01-01 00:00:00", None),
        (5, 51, "2105-08-03 17:37:00", "2105-08-10 00:00:00"),
        (5, 52, "2106-03-31 18:44:00", "2106-04-01 00:00:00"),
    ]
    conn.executemany("INSERT INTO admissions VALUES (?,?,?,?, 'EMERGENCY ROOM ADMIT', 'EMERGENCY')", rows)
    conn.execute("CREATE TABLE labevents (subject_id INTEGER, hadm_id INTEGER, charttime TEXT, itemid INTEGER)")
    conn.executemany("INSERT INTO labevents VALUES (?,?,?,?)", [
        (1, 11, "2157-12-30 00:00:00", 50912),
        (1, 99, "2157-12-29 12:00:00", 50912),  # right patient, wrong encounter
        (2, 11, "2157-12-29 12:00:00", 50912),  # wrong patient, same encounter id
        (1, 11, "2157-12-30 00:00:01", 50912),  # just outside 24 h
    ])
    return conn


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.conn = fixture()
        self.executor = Executor(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_original_pilot_can_be_planned(self):
        path = Path(__file__).resolve().parents[1] / "data" / "original_pilot.json"
        for case in json.loads(path.read_text()):
            with self.subTest(case=case["id"]):
                plan = plan_question(case["question"], case["reference_time"])
                self.assertEqual(plan["steps"][-1]["op"], "answer")

    def test_original_absolute_year_failure(self):
        plan = plan_question("what was patient 5's first hospital admission time since 2105?", REF)
        self.assertEqual(self.executor.execute(plan)["rows"], [["2105-08-03 17:37:00"]])

    def test_calendar_filter_before_ordinal(self):
        plan = plan_question("when was patient 1 first admitted during this year?", REF)
        self.assertEqual(self.executor.execute(plan)["rows"], [["2158-01-01 00:00:00"]])

    def test_readmission_against_independent_sql(self):
        result = self.executor.execute(readmission_plan(REF))
        gold = self.conn.execute("""
          WITH first_discharge AS (
            SELECT subject_id, MIN(dischtime) AS t FROM admissions WHERE dischtime IS NOT NULL GROUP BY subject_id
          )
          SELECT a.subject_id, MIN(a.admittime)
          FROM admissions a JOIN first_discharge d ON a.subject_id=d.subject_id
          WHERE a.admittime>d.t AND a.admittime<=datetime(d.t,'+30 days')
          GROUP BY a.subject_id ORDER BY a.subject_id
        """).fetchall()
        self.assertEqual(result["rows"], [list(row) for row in gold])
        self.assertEqual(result["rows"], [[1, "2158-01-02 00:00:00"], [2, "2158-01-31 00:00:00"]])
        self.assertTrue(all(row["anchors"] for row in result["evidence"]))

    def test_cross_table_scope_and_24_hour_boundary(self):
        plan = {"version": 1, "question": "labs in first 24 hours of patient 1's first admission", "reference_time": REF,
                "steps": [
                    {"id": "visits", "op": "fetch", "event": "admission", "subject_id": 1},
                    {"id": "first", "op": "select", "source": "visits", "position": 1, "direction": "asc", "partition": "patient"},
                    {"id": "labs", "op": "fetch", "event": "lab", "filters": {"itemid": 50912}},
                    {"id": "matched", "op": "within", "source": "labs", "anchor": "first", "lower": 0, "upper": 24,
                     "unit": "hour", "lower_inclusive": True, "upper_inclusive": True, "encounter_scope": "same"},
                    {"id": "answer", "op": "answer", "source": "matched", "kind": "timestamps"}]}
        self.assertEqual(self.executor.execute(plan)["rows"], [["2157-12-30 00:00:00"]])

    def test_patient_scope_is_not_global_limit_one(self):
        plan = readmission_plan(REF)
        self.assertEqual(len(self.executor.execute(plan)["rows"]), 2)

    def test_missing_encounter_is_not_assumed_different(self):
        self.conn.execute("INSERT INTO admissions VALUES (3,NULL,'2158-01-02 00:00:00',NULL,NULL,NULL)")
        self.assertEqual(self.executor.execute(readmission_plan(REF, subject_id=3))["rows"], [])

    def test_tied_ordinal_is_reported(self):
        self.conn.execute("INSERT INTO admissions VALUES (1,15,'2158-01-02 00:00:00',NULL,NULL,NULL)")
        with self.assertRaisesRegex(TemporalError, "Ambiguous ordinal"):
            self.executor.execute(readmission_plan(REF, subject_id=1))

    def test_limits_do_not_silently_truncate(self):
        with self.assertRaises(ExecutionLimitError):
            Executor(self.conn, max_rows=1).execute(readmission_plan(REF))

    def test_unsupported_conditions_fail_closed(self):
        for q in ("how many admissions in the next quarter?", "how many admissions with diabetes since 2105?",
                  "when was patient 1 admitted within 24 hours after discharge?"):
            with self.subTest(q=q), self.assertRaises(UnsupportedQuestion):
                plan_question(q, REF)

    def test_unknown_operations_unused_filters_and_dangling_inputs(self):
        base = readmission_plan(REF)
        broken = copy.deepcopy(base)
        broken["steps"][0]["op"] = "execute_arbitrary_sql"
        with self.assertRaises(PlanValidationError):
            validate_plan(broken)
        broken = copy.deepcopy(base)
        broken["steps"][1]["source"] = "future"
        with self.assertRaises(PlanValidationError):
            validate_plan(broken)
        broken = copy.deepcopy(base)
        broken["steps"].insert(0, {"id": "unused", "op": "fetch", "event": "lab"})
        with self.assertRaises(PlanValidationError):
            validate_plan(broken)


if __name__ == "__main__":
    unittest.main()
