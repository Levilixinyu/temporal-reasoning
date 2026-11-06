"""Explicit CSV import into a new SQLite database, with dataset provenance."""

from __future__ import annotations

import csv
import gzip
import json
import sqlite3
from pathlib import Path

from .errors import TemporalError

TABLE_COLUMNS = {
    "admissions": {"subject_id": "INTEGER", "hadm_id": "INTEGER", "admittime": "TEXT",
                   "dischtime": "TEXT", "admission_location": "TEXT", "admission_type": "TEXT"},
    "patients": {"subject_id": "INTEGER", "gender": "TEXT", "dob": "TEXT"},
    "labevents": {"subject_id": "INTEGER", "hadm_id": "INTEGER", "itemid": "INTEGER",
                  "charttime": "TEXT", "valuenum": "REAL", "valueuom": "TEXT"},
    "icustays": {"subject_id": "INTEGER", "hadm_id": "INTEGER", "icustay_id": "INTEGER",
                 "intime": "TEXT", "outtime": "TEXT"},
}


def import_csv_database(directory: str | Path, destination: str | Path, dataset_id: str,
                        tables=("admissions", "patients")) -> dict:
    directory, destination = Path(directory), Path(destination)
    if not directory.is_dir() or not dataset_id.strip():
        raise TemporalError("A CSV directory and explicit dataset_id are required")
    if destination.exists():
        raise TemporalError("Destination already exists; choose a new database filename")
    if not tables or any(t not in TABLE_COLUMNS for t in tables) or len(set(tables)) != len(tables):
        raise TemporalError("Unsupported or duplicate table selection")
    sources = {}
    for table in tables:
        matches = [p for p in directory.iterdir() if p.name.lower() in {table + ".csv", table + ".csv.gz"}]
        if len(matches) != 1:
            raise TemporalError(f"Expected exactly one CSV or CSV.GZ for {table}; found {len(matches)}")
        sources[table] = matches[0]
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting an existing artifact during a race.
    with destination.open("xb"):
        pass
    conn = sqlite3.connect(destination)
    manifest = {"dataset_id": dataset_id, "source_directory": str(directory.resolve()), "tables": {}}
    try:
        conn.execute("BEGIN")
        for table, path in sources.items():
            opener = gzip.open if path.suffix == ".gz" else open
            with opener(path, "rt", newline="", encoding="utf-8-sig") as stream:
                reader = csv.DictReader(stream)
                headers = {name.lower(): name for name in reader.fieldnames or []}
                columns = {name: typ for name, typ in TABLE_COLUMNS[table].items() if name in headers}
                required = {"subject_id"} | ({"hadm_id"} if table != "patients" else set())
                if required - columns.keys():
                    raise TemporalError(f"{table}: missing identifiers")
                declaration = ", ".join(f'"{name}" {typ}' for name, typ in columns.items())
                conn.execute(f'CREATE TABLE "{table}" ({declaration})')
                placeholders = ",".join("?" for _ in columns)
                query = f'INSERT INTO "{table}" VALUES ({placeholders})'
                batch, count = [], 0
                for row in reader:
                    values = []
                    for name, typ in columns.items():
                        value = row[headers[name]]
                        if value is None or value == "":
                            value = None
                        elif typ == "INTEGER":
                            value = int(value)
                        elif typ == "REAL":
                            value = float(value)
                        values.append(value)
                    batch.append(values)
                    count += 1
                    if len(batch) >= 1000:
                        conn.executemany(query, batch)
                        batch = []
                conn.executemany(query, batch)
                conn.execute(f'CREATE INDEX "{table}_patient" ON "{table}" (subject_id)')
                manifest["tables"][table] = {"rows": count, "columns": list(columns), "source": str(path.resolve())}
        conn.execute("CREATE TABLE temporal_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO temporal_metadata VALUES ('manifest', ?)", (json.dumps(manifest),))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        destination.unlink()  # Only the new, exclusively-created incomplete file.
        raise
    finally:
        conn.close()
    return manifest
