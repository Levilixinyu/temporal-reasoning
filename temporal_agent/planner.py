"""Conservative local planner for the original admission pilot.

This is a rule baseline, not a general language model. Unsupported vocabulary or
unresolved temporal operators produce an error instead of a partial query.
The executor accepts richer manually annotated plans independently of this parser.
"""

from __future__ import annotations

import re

from .errors import UnsupportedQuestion
from .plan import validate_plan
from .temporal import extract_time_expressions, iso, timestamp


def _selection(name, source, ordinal, partition="patient"):
    ordinal = ordinal.lower()
    return {"id": name, "op": "select", "source": source,
            "position": {"second": 2, "third": 3}.get(ordinal, 1),
            "direction": "desc" if ordinal in {"last", "latest", "most recent"} else "asc",
            "partition": partition}


def readmission_plan(reference_time: str, *, days: int = 30, subject_id: int | None = None) -> dict:
    """First readmission within (0, days] after each patient's first discharge."""
    if type(days) is not int or days <= 0:
        raise UnsupportedQuestion("Readmission window requires positive integer days")
    patient = {"subject_id": subject_id} if subject_id is not None else {}
    steps = [
        {"id": "discharges", "op": "fetch", "event": "discharge", **patient},
        _selection("index_discharge", "discharges", "first"),
        {"id": "admissions", "op": "fetch", "event": "admission", **patient},
        {"id": "eligible", "op": "within", "source": "admissions", "anchor": "index_discharge",
         "lower": 0, "upper": days, "unit": "day", "lower_inclusive": False,
         "upper_inclusive": True, "encounter_scope": "different"},
        _selection("first_readmission", "eligible", "first", "anchor"),
        {"id": "answer", "op": "answer", "source": "first_readmission", "kind": "patient_timestamps"},
    ]
    question = f"Find the first readmission within {days} days after the first discharge for each patient"
    if subject_id is not None:
        question = f"Find the first readmission within {days} days after the first discharge for patient {subject_id}"
    return validate_plan({"version": 1, "question": question, "reference_time": iso(timestamp(reference_time)),
                          "planner": "explicit-readmission-builder-v1", "steps": steps})


def plan_question(question: str, reference_time: str) -> dict:
    if not isinstance(question, str) or not question.strip():
        raise UnsupportedQuestion("Question must be nonempty")
    q = question.strip().rstrip(".?").lower()
    composite = re.fullmatch(
        r"find the first readmission within (\d+) days after the first discharge for (each patient|patient (\d+))", q)
    if composite:
        return readmission_plan(reference_time, days=int(composite.group(1)),
                                subject_id=int(composite.group(3)) if composite.group(3) else None)
    expressions = extract_time_expressions(question, reference_time)
    # Strip spans from the original string before normalizing whitespace.
    original = question.lower()
    chars = list(original)
    for expression in expressions:
        chars[expression.span[0]:expression.span[1]] = " " * (expression.span[1] - expression.span[0])
    remaining = "".join(chars)
    subjects = re.findall(r"\bpatient\s+(\d+)\b", remaining)
    if len(subjects) > 1:
        raise UnsupportedQuestion("Multiple patient references require an explicit plan")
    subject_id = int(subjects[0]) if subjects else None
    remaining = re.sub(r"\bpatient\s+\d+(?:'s)?\b", "patient", remaining)
    filters = {}
    if "transfer from hosp/extram" in remaining:
        filters["admission_location"] = "TRANSFER FROM HOSP/EXTRAM"
        remaining = remaining.replace("transfer from hosp/extram", " ")
    if re.search(r"\bemergency room\b|\ber\b", remaining):
        if filters:
            raise UnsupportedQuestion("Conflicting admission locations")
        filters["admission_location"] = "EMERGENCY ROOM ADMIT"
        remaining = re.sub(r"\bemergency room\b|\ber\b", " ", remaining)
    ordinals = list(re.finditer(r"\b(first|second|third|last|earliest|latest|most recent)\b", remaining))
    if len(ordinals) > 1:
        raise UnsupportedQuestion("Multiple event selections require an explicit scoped plan")
    ordinal = ordinals[0].group() if ordinals else None
    remaining = re.sub(r"\b(first|second|third|last|earliest|latest|most recent)\b", " ", remaining)
    discharge = bool(re.search(r"\b(discharged?|leaving)\b", remaining))
    admission = bool(re.search(r"\b(admitted|admissions?|visits?|visited|come)\b", remaining)) or bool(filters)
    if discharge and admission:
        raise UnsupportedQuestion("Multiple event types require explicit event scope")
    if not discharge and not admission:
        raise UnsupportedQuestion("Only admission/discharge pilot questions are supported by this rule planner")
    if re.search(r"\b(since|until|before|after|within|between|during|ago|past|previous|same)\b", remaining):
        raise UnsupportedQuestion("Unresolved temporal condition; refusing a partial plan")
    allowed = set("when what was were is are did does do has have had patient patients hospital admitted admission admissions visit visits visited discharged discharge leaving come count on the number of how many times to a an in at via get got go went me tell time that this their for each show find list and".split())
    tokens = set(re.findall(r"[a-z]+|\d+", remaining))
    unknown = tokens - allowed
    if unknown:
        raise UnsupportedQuestion(f"Unsupported question terms: {', '.join(sorted(unknown))}")
    if q.startswith(("did ", "has ", "have ")):
        kind = "exists"
    elif re.search(r"\bwhen\b|\badmission time\b|\bdischarge time\b", q):
        kind = "timestamps" if subject_id is not None else "patient_timestamps"
    elif re.search(r"\b(how many|number of|count)\b", q):
        kind = "count_patients" if "patients" in remaining else "count_events"
    else:
        raise UnsupportedQuestion("Use a count, existence, or event-time pilot question; other answer types need an explicit plan")
    fetch = {"id": "events", "op": "fetch", "event": "discharge" if discharge else "admission", "filters": filters}
    if subject_id is not None:
        fetch["subject_id"] = subject_id
    steps, source = [fetch], "events"
    for i, expression in enumerate(expressions, 1):
        name = f"time_{i}"
        steps.append({"id": name, "op": "filter_time", "source": source, "window": expression.window.to_dict()})
        source = name
    if ordinal:
        steps.append(_selection("selected", source, ordinal))
        source = "selected"
    steps.append({"id": "answer", "op": "answer", "source": source, "kind": kind})
    return validate_plan({"version": 1, "question": question, "reference_time": iso(timestamp(reference_time)),
                          "planner": "admission-rule-baseline-v1", "steps": steps,
                          "temporal_expressions": [e.to_dict() for e in expressions]})
