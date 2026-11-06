# Clinical Temporal Reasoning Agent

This repository contains a local clinical temporal-planning package for explicit, reproducible reasoning over time-sensitive clinical database questions.

## Package

The `temporal_agent` package extracts time expressions, builds explicit temporal plans, imports local CSV tables, and executes plans against a read-only local database. It does not call a model API.

```bash
python -m pip install -e .
temporal-agent parse "admissions in the previous calendar month" --reference-time 2026-01-15T00:00:00
```

## Tests

```bash
python -m unittest discover -s tests
```

## Data and credentials

MIMIC-IV source data, benchmark question banks, model outputs, review records, execution logs, and API credentials are not included in this public source repository.
