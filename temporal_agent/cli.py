from __future__ import annotations

import argparse
import json
from pathlib import Path

from .database import import_csv_database
from .errors import TemporalError
from .executor import Executor, connect_readonly
from .planner import plan_question, readmission_plan
from .temporal import extract_time_expressions


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local clinical temporal planning; no model API calls")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("parse", "plan", "query"):
        command = sub.add_parser(name)
        command.add_argument("question")
        command.add_argument("--reference-time", required=True)
        if name == "query":
            command.add_argument("--database", required=True)
            command.add_argument("--max-rows", type=int, default=50000)
    build = sub.add_parser("readmission-plan")
    build.add_argument("--reference-time", required=True)
    build.add_argument("--days", type=int, default=30)
    build.add_argument("--subject-id", type=int)
    execute = sub.add_parser("execute-plan")
    execute.add_argument("plan_file")
    execute.add_argument("--database", required=True)
    execute.add_argument("--max-rows", type=int, default=50000)
    load = sub.add_parser("import-csv")
    load.add_argument("directory")
    load.add_argument("--database", required=True)
    load.add_argument("--dataset-id", required=True)
    load.add_argument("--tables", nargs="+", default=["admissions", "patients"])
    args = parser.parse_args(argv)
    try:
        if args.command == "parse":
            expressions = extract_time_expressions(args.question, args.reference_time)
            result = {"reference_time": args.reference_time, "expressions": [e.to_dict() for e in expressions],
                      "note": "Expression extraction only; does not assert full question coverage"}
        elif args.command == "plan":
            result = plan_question(args.question, args.reference_time)
        elif args.command == "readmission-plan":
            result = readmission_plan(args.reference_time, days=args.days, subject_id=args.subject_id)
        elif args.command == "import-csv":
            result = import_csv_database(args.directory, args.database, args.dataset_id, args.tables)
        else:
            plan = (plan_question(args.question, args.reference_time) if args.command == "query"
                    else json.loads(Path(args.plan_file).read_text()))
            conn = connect_readonly(args.database)
            try:
                result = Executor(conn, max_rows=args.max_rows).execute(plan)
            finally:
                conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (TemporalError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
