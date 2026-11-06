import gzip
import sqlite3
import tempfile
import unittest
from pathlib import Path

from temporal_agent.database import import_csv_database
from temporal_agent.errors import TemporalError
from temporal_agent.executor import Executor, connect_readonly
from temporal_agent.planner import plan_question


class DatabaseTests(unittest.TestCase):
    def test_uppercase_csv_gzip_nulls_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ADMISSIONS.csv").write_text("SUBJECT_ID,HADM_ID,ADMITTIME,DISCHTIME\n1,10,2105-08-03 17:37:00,\n")
            with gzip.open(root / "patients.csv.gz", "wt") as f:
                f.write("subject_id,gender,dob\n1,F,2080-01-01\n")
            path = root / "demo.sqlite"
            manifest = import_csv_database(root, path, "synthetic-test")
            self.assertEqual(manifest["tables"]["admissions"]["rows"], 1)
            conn = connect_readonly(path)
            try:
                plan = plan_question("when was patient 1 first admitted since 2105?", "2158-08-18")
                self.assertEqual(Executor(conn).execute(plan)["rows"], [["2105-08-03 17:37:00"]])
                self.assertIsNone(conn.execute("SELECT dischtime FROM admissions").fetchone()[0])
                with self.assertRaises(sqlite3.OperationalError):
                    conn.execute("DELETE FROM admissions")
            finally:
                conn.close()
            with self.assertRaises(TemporalError):
                import_csv_database(root, path, "synthetic-test")

    def test_failed_import_does_not_leave_a_partial_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "admissions.csv").write_text("subject_id,hadm_id,admittime\nBAD,10,2024-01-01\n")
            dest = root / "broken.sqlite"
            with self.assertRaises(ValueError):
                import_csv_database(root, dest, "synthetic", ["admissions"])
            self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
