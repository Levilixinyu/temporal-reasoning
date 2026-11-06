"""Closed, versioned plan language. Every operation declares its input scope."""

from __future__ import annotations

import copy
import re

from .errors import PlanValidationError, TemporalError
from .temporal import UNITS, Window, timestamp

EVENTS = {
    "admission": ("admissions", "admittime"),
    "discharge": ("admissions", "dischtime"),
    "lab": ("labevents", "charttime"),
    "icu_admission": ("icustays", "intime"),
}
FILTERS = {
    "admission": {"admission_location", "admission_type", "hadm_id"},
    "discharge": {"admission_location", "admission_type", "hadm_id"},
    "lab": {"itemid", "hadm_id"},
    "icu_admission": {"hadm_id"},
}
FIELDS = {
    "fetch": ({"id", "op", "event"}, {"subject_id", "filters"}),
    "filter_time": ({"id", "op", "source", "window"}, set()),
    "select": ({"id", "op", "source", "position", "direction", "partition"}, set()),
    "within": ({"id", "op", "source", "anchor", "lower", "upper", "unit",
                "lower_inclusive", "upper_inclusive", "encounter_scope"}, set()),
    "answer": ({"id", "op", "source", "kind"}, set()),
}
ANSWER_KINDS = {"count_events", "count_patients", "exists", "timestamps", "patient_timestamps", "records"}


def _keys(obj: dict, required: set, optional: set, label: str):
    missing, extra = required - obj.keys(), obj.keys() - required - optional
    if missing or extra:
        raise PlanValidationError(f"{label}: missing fields {sorted(missing)}, unknown fields {sorted(extra)}")


def validate_plan(plan: dict) -> dict:
    if not isinstance(plan, dict):
        raise PlanValidationError("Plan must be a JSON object")
    _keys(plan, {"version", "question", "reference_time", "steps"}, {"temporal_expressions", "planner"}, "plan")
    if type(plan["version"]) is not int or plan["version"] != 1:
        raise PlanValidationError("Only plan version 1 is supported")
    if not isinstance(plan["question"], str) or not plan["question"].strip():
        raise PlanValidationError("Question must be nonempty")
    timestamp(plan["reference_time"])
    steps = plan["steps"]
    if not isinstance(steps, list) or not 2 <= len(steps) <= 32:
        raise PlanValidationError("Plan must contain 2–32 steps")
    seen: dict[str, bool] = {}
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("op"), str) or step["op"] not in FIELDS:
            raise PlanValidationError(f"Unknown operation in step {index}")
        op = step["op"]
        _keys(step, *FIELDS[op], f"step {index}")
        name = step["id"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", name) or name in seen:
            raise PlanValidationError(f"Invalid or duplicate step id: {name!r}")
        has_anchor = False
        if op != "fetch":
            if not isinstance(step["source"], str) or step["source"] not in seen:
                raise PlanValidationError(f"{name}: source must refer to an earlier event step")
            has_anchor = seen[step["source"]]
        if op == "fetch":
            event = step["event"]
            if not isinstance(event, str) or event not in EVENTS:
                raise PlanValidationError("Unsupported event type")
            if "subject_id" in step and (type(step["subject_id"]) is not int or step["subject_id"] < 0):
                raise PlanValidationError("subject_id must be a nonnegative integer")
            filters = step.get("filters", {})
            if not isinstance(filters, dict) or filters.keys() - FILTERS[event]:
                raise PlanValidationError("Unsupported event filters")
            for key, value in filters.items():
                if key in {"hadm_id", "itemid"}:
                    if type(value) is not int or value < 0:
                        raise PlanValidationError(f"{key} requires a nonnegative integer")
                elif not isinstance(value, str) or not value:
                    raise PlanValidationError(f"{key} requires a nonempty string")
        elif op == "filter_time":
            w = step["window"]
            if not isinstance(w, dict):
                raise PlanValidationError("Window must be an object")
            _keys(w, {"start", "end", "start_inclusive", "end_inclusive"}, set(), "window")
            Window(**w)
        elif op == "select":
            if type(step["position"]) is not int or not 1 <= step["position"] <= 100:
                raise PlanValidationError("Event position must be an integer in [1, 100]")
            if step["direction"] not in ("asc", "desc") or step["partition"] not in ("patient", "anchor"):
                raise PlanValidationError("Select requires asc/desc direction and patient/anchor partition")
            if step["partition"] == "anchor" and not has_anchor:
                raise PlanValidationError("Anchor partition requires an earlier within operation")
        elif op == "within":
            if not isinstance(step["anchor"], str) or step["anchor"] not in seen:
                raise PlanValidationError("Anchor must refer to an earlier event step")
            if type(step["lower"]) is not int or type(step["upper"]) is not int or step["lower"] > step["upper"]:
                raise PlanValidationError("Relative window needs ordered integer bounds")
            if not isinstance(step["unit"], str) or step["unit"] not in UNITS:
                raise PlanValidationError("Unsupported relative window unit")
            if type(step["lower_inclusive"]) is not bool or type(step["upper_inclusive"]) is not bool:
                raise PlanValidationError("Relative boundary flags must be booleans")
            if step["lower"] == step["upper"] and not (step["lower_inclusive"] and step["upper_inclusive"]):
                raise PlanValidationError("Empty relative window")
            if step["encounter_scope"] not in ("same", "different", "any"):
                raise PlanValidationError("Declare same/different/any encounter scope")
            has_anchor = True
        elif op == "answer":
            if index != len(steps) - 1 or not isinstance(step["kind"], str) or step["kind"] not in ANSWER_KINDS:
                raise PlanValidationError("A supported answer operation must be the final step")
        seen[name] = has_anchor
    if steps[-1]["op"] != "answer":
        raise PlanValidationError("Plan must end with an answer operation")
    # No unused branches: an ignored temporal filter must not masquerade as validation.
    used = {steps[-1]["id"]}
    for step in reversed(steps):
        if step["id"] in used:
            used.update(step[k] for k in ("source", "anchor") if k in step)
    if set(seen) - used:
        raise PlanValidationError(f"Unused plan steps: {sorted(set(seen) - used)}")
    return copy.deepcopy(plan)
