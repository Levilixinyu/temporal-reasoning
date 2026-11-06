"""Read-only SQLite retrieval plus deterministic temporal operations.

Row limits are errors, never silent truncation. Ties at an ordinal cutoff are
errors, because inventing a row-id tie break would hide temporal ambiguity.
"""

from __future__ import annotations

import bisect
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ExecutionLimitError, TemporalError
from .plan import EVENTS, validate_plan
from .temporal import Window, iso, shift, timestamp


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    path = Path(path).resolve()
    if not path.is_file():
        raise TemporalError(f"Database does not exist: {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


@dataclass(frozen=True)
class Event:
    event_id: str
    subject_id: int
    hadm_id: int | None
    time: str
    kind: str
    attributes: dict = field(default_factory=dict)
    anchors: tuple[tuple[str, str], ...] = ()

    def evidence(self) -> dict:
        return {"event_id": self.event_id, "subject_id": self.subject_id,
                "hadm_id": self.hadm_id, "time": self.time, "kind": self.kind,
                "anchors": [{"event_id": k, "time": t} for k, t in self.anchors]}


def _identity(row: Event):
    return row.event_id, row.anchors


class Executor:
    def __init__(self, connection: sqlite3.Connection, max_rows: int = 50000):
        if type(max_rows) is not int or max_rows < 1:
            raise TemporalError("max_rows must be positive")
        self.conn, self.max_rows = connection, max_rows

    def _fetch(self, step: dict) -> tuple[list[Event], dict]:
        table, time_col = EVENTS[step["event"]]
        schema = {r[1].lower() for r in self.conn.execute(f'PRAGMA table_info("{table}")')}
        required = {"subject_id", "hadm_id", time_col} | step.get("filters", {}).keys()
        if required - schema:
            raise TemporalError(f"{table}: missing columns {sorted(required - schema)}")
        clauses, params = [f'"{time_col}" IS NOT NULL'], []
        if "subject_id" in step:
            clauses.append('"subject_id" = ?')
            params.append(step["subject_id"])
        for key, value in sorted(step.get("filters", {}).items()):
            clauses.append(f'"{key}" = ?')
            params.append(value)
        sql = f'SELECT rowid, subject_id, hadm_id, "{time_col}" FROM "{table}" WHERE ' + " AND ".join(clauses)
        raw = self.conn.execute(sql, params).fetchmany(self.max_rows + 1)
        if len(raw) > self.max_rows:
            raise ExecutionLimitError(f"{step['id']}: retrieval exceeds {self.max_rows} rows; narrow scope or raise the explicit limit")
        events = []
        for rowid, subject, encounter, time in raw:
            if subject is None:
                raise TemporalError(f"{table}: event has no patient identifier")
            # Invalid non-null timestamps are data errors, not silently dropped records.
            events.append(Event(f"{table}:{rowid}:{time_col}", int(subject),
                                int(encounter) if encounter is not None else None,
                                iso(timestamp(str(time))), step["event"]))
        return events, {"sql": sql, "parameters": params}

    def _select(self, rows: list[Event], step: dict) -> list[Event]:
        groups = defaultdict(list)
        for row in rows:
            key = (row.subject_id, row.anchors) if step["partition"] == "anchor" else (row.subject_id,)
            groups[key].append(row)
        chosen = []
        for group in groups.values():
            # Repeated appearances of the same event are not separate admissions.
            group = list({_identity(row): row for row in group}.values())
            group.sort(key=lambda row: timestamp(row.time), reverse=step["direction"] == "desc")
            index = step["position"] - 1
            if index >= len(group):
                continue
            pick = group[index]
            if sum(row.time == pick.time for row in group) > 1:
                raise TemporalError("Ambiguous ordinal selection: multiple events share the selected timestamp")
            chosen.append(pick)
        return chosen

    def _within(self, candidates: list[Event], anchors: list[Event], step: dict) -> list[Event]:
        groups = defaultdict(list)
        for row in candidates:
            groups[row.subject_id].append(row)
        indexed = {}
        for patient, rows in groups.items():
            rows.sort(key=lambda row: timestamp(row.time))
            indexed[patient] = ([timestamp(row.time) for row in rows], rows)
        matches = {}
        for anchor in anchors:
            times, rows = indexed.get(anchor.subject_id, ([], []))
            start = shift(timestamp(anchor.time), step["lower"], step["unit"])
            end = shift(timestamp(anchor.time), step["upper"], step["unit"])
            lower = (bisect.bisect_left if step["lower_inclusive"] else bisect.bisect_right)(times, start)
            upper = (bisect.bisect_right if step["upper_inclusive"] else bisect.bisect_left)(times, end)
            for row in rows[lower:upper]:
                scope = step["encounter_scope"]
                if scope != "any" and (row.hadm_id is None or anchor.hadm_id is None):
                    # Unknown encounter identity is not evidence of same/different identity.
                    continue
                if scope == "same" and row.hadm_id != anchor.hadm_id:
                    continue
                if scope == "different" and row.hadm_id == anchor.hadm_id:
                    continue
                lineage = tuple(dict.fromkeys((*row.anchors, *anchor.anchors, (anchor.event_id, anchor.time))))
                linked = Event(row.event_id, row.subject_id, row.hadm_id, row.time, row.kind, row.attributes, lineage)
                matches[_identity(linked)] = linked
                if len(matches) > self.max_rows:
                    raise ExecutionLimitError("Relative temporal join exceeds max_rows")
        return list(matches.values())

    @staticmethod
    def _answer(rows: list[Event], kind: str) -> tuple[list[str], list[list]]:
        unique = list({row.event_id: row for row in rows}.values())
        unique.sort(key=lambda row: (row.subject_id, timestamp(row.time), row.event_id))
        if kind == "count_events":
            return ["count_events"], [[len(unique)]]
        if kind == "count_patients":
            return ["count_patients"], [[len({row.subject_id for row in unique})]]
        if kind == "exists":
            return ["exists"], [[int(bool(unique))]]
        if kind == "timestamps":
            return ["time"], [[row.time] for row in unique]
        if kind == "patient_timestamps":
            return ["subject_id", "time"], [[row.subject_id, row.time] for row in unique]
        return ["subject_id", "hadm_id", "time", "event_id"], [[row.subject_id, row.hadm_id, row.time, row.event_id] for row in unique]

    def execute(self, plan: dict) -> dict:
        plan = validate_plan(plan)
        values, trace = {}, []
        for step in plan["steps"]:
            op, extra = step["op"], {}
            if op == "fetch":
                rows, extra = self._fetch(step)
            else:
                rows = values[step["source"]]
                if op == "filter_time":
                    window = Window(**step["window"])
                    rows = [r for r in rows if window.contains(timestamp(r.time))]
                elif op == "select":
                    rows = self._select(rows, step)
                elif op == "within":
                    rows = self._within(rows, values[step["anchor"]], step)
                elif op == "answer":
                    columns, result = self._answer(rows, step["kind"])
                    trace.append({"id": step["id"], "op": op, "input_rows": len(rows), "output_rows": len(result)})
                    return {"columns": columns, "rows": result, "trace": trace,
                            "evidence": [r.evidence() for r in rows], "plan": plan}
            trace.append({"id": step["id"], "op": op, "output_rows": len(rows), **extra})
            values[step["id"]] = rows
        raise TemporalError("Missing answer")
