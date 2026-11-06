"""Result-based evaluation. Invalid gold queries never become model errors.

Aliases are ignored, column position and duplicate multiplicity are preserved.
Row ordering is controlled by the task. String values remain case-sensitive.
No SQL substring heuristic is reported as temporal correctness.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from datetime import date, datetime

from .errors import TemporalError
from .temporal import Window, iso, timestamp


def _cell(value):
    if isinstance(value, datetime):
        return ("datetime", iso(value))
    if isinstance(value, date):
        return ("date", value.isoformat())
    if value is None:
        return ("null", None)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise TemporalError("Non-finite numeric results require a task-specific policy")
        return ("number", value)
    return (type(value).__name__, value)


def compare_rows(predicted: list, gold: list, *, order_sensitive: bool = False) -> bool:
    a = [tuple(_cell(v) for v in row) for row in predicted]
    b = [tuple(_cell(v) for v in row) for row in gold]
    return a == b if order_sensitive else Counter(a) == Counter(b)


def execute_readonly_sql(conn: sqlite3.Connection, sql: str, *, max_rows=50000, max_vm_steps=2000000) -> dict:
    if not isinstance(sql, str) or not sql.strip():
        return {"success": False, "error": "Empty SQL"}
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorize(action, arg1, arg2, database, trigger):
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {"load_extension", "writefile", "readfile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    ticks = 0

    def progress():
        nonlocal ticks
        ticks += 1000
        return int(ticks > max_vm_steps)

    conn.set_authorizer(authorize)
    conn.set_progress_handler(progress, 1000)
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            return {"success": False, "error": "Result exceeds row limit"}
        if cursor.description is None:
            return {"success": False, "error": "Query did not return columns"}
        return {"success": True, "columns": [col[0] for col in cursor.description], "rows": [list(r) for r in rows]}
    except sqlite3.Error as exc:
        return {"success": False, "error": str(exc)}
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)


def evaluate_sql_pair(conn: sqlite3.Connection, predicted_sql: str, gold_sql: str, *, order_sensitive=False) -> dict:
    gold = execute_readonly_sql(conn, gold_sql)
    if not gold["success"]:
        return {"status": "invalid_gold", "match": None, "gold": gold, "prediction": None}
    prediction = execute_readonly_sql(conn, predicted_sql)
    if not prediction["success"]:
        return {"status": "prediction_error", "match": False, "gold": gold, "prediction": prediction}
    match = len(prediction["columns"]) == len(gold["columns"]) and compare_rows(
        prediction["rows"], gold["rows"], order_sensitive=order_sensitive)
    return {"status": "evaluated", "match": match, "gold": gold, "prediction": prediction}


def compare_labeled_window(predicted: dict | None, gold_window: dict | None) -> dict:
    """Only evaluate against an independent window label, never against SQL text."""
    if gold_window is None:
        return {"status": "not_evaluated", "match": None, "reason": "No independent temporal label"}
    try:
        expected = Window(**gold_window)
    except (TemporalError, TypeError) as exc:
        return {"status": "invalid_gold", "match": None, "reason": str(exc)}
    if predicted is None:
        return {"status": "prediction_error", "match": False, "reason": "Missing predicted window"}
    try:
        actual = Window(**predicted)
    except (TemporalError, TypeError) as exc:
        return {"status": "prediction_error", "match": False, "reason": str(exc)}

    def normalize(w):
        return (timestamp(w.start) if w.start else None, timestamp(w.end) if w.end else None,
                w.start_inclusive if w.start else None, w.end_inclusive if w.end else None)

    return {"status": "evaluated", "match": normalize(actual) == normalize(expected)}


def summarize(evaluations: list[dict]) -> dict:
    valid = [row for row in evaluations if row["match"] is not None]
    correct = sum(row["match"] is True for row in valid)
    return {"total": len(evaluations), "evaluated": len(valid), "correct": correct,
            "invalid_gold": sum(row["status"] == "invalid_gold" for row in evaluations),
            "not_evaluated": sum(row["status"] == "not_evaluated" for row in evaluations),
            "accuracy": correct / len(valid) if valid else None}
