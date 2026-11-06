"""Deterministic temporal grounding, with explicit and inspectable policies.

Dates are naive database-local timestamps. Calendar weeks begin on Monday.
Calendar month/year offsets clamp to the last valid day of the target month.
`last month` means the previous calendar month; `last 1 month` is a rolling
window. `since`/`until N units ago` have one bound, preserving the pilot's
semantics. Relative calendar expressions require a reference timestamp, never taken from the clock.
Absolute expressions can be grounded without a reference timestamp.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from .errors import TemporalError

UNITS = {"hour", "day", "week", "month", "year"}
UNIT_PATTERN = r"(hours?|days?|weeks?|months?|years?)"


def timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise TemporalError("Timestamp must be an ISO date or datetime string")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TemporalError(f"Invalid timestamp: {value!r}") from exc
    if result.tzinfo is not None:
        raise TemporalError("Timezone-aware values require an explicit dataset timezone adapter")
    return result


def iso(value: datetime) -> str:
    return value.isoformat(sep=" ")


def shift(value: datetime, amount: int, unit: str) -> datetime:
    if type(amount) is not int or unit not in UNITS:
        raise TemporalError("Offset requires an integer and a supported unit")
    try:
        if unit in {"hour", "day", "week"}:
            return value + timedelta(**{unit + "s": amount})
        months = amount * (12 if unit == "year" else 1)
        index = value.year * 12 + value.month - 1 + months
        year, month0 = divmod(index, 12)
        if not 1 <= year <= 9999:
            raise TemporalError("Date offset exceeds supported datetime range")
        day = min(value.day, calendar.monthrange(year, month0 + 1)[1])
        return value.replace(year=year, month=month0 + 1, day=day)
    except (ValueError, OverflowError) as exc:
        raise TemporalError("Date offset exceeds supported datetime range") from exc


def floor(value: datetime, unit: str) -> datetime:
    if unit not in UNITS:
        raise TemporalError(f"Unsupported calendar unit: {unit}")
    if unit == "hour":
        return value.replace(minute=0, second=0, microsecond=0)
    day = value.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "day":
        return day
    if unit == "week":
        return day - timedelta(days=day.weekday())
    if unit == "month":
        return day.replace(day=1)
    return day.replace(month=1, day=1)


@dataclass(frozen=True)
class Window:
    start: str | None = None
    end: str | None = None
    start_inclusive: bool = True
    end_inclusive: bool = False

    def __post_init__(self):
        if type(self.start_inclusive) is not bool or type(self.end_inclusive) is not bool:
            raise TemporalError("Window inclusivity flags must be booleans")
        start = timestamp(self.start) if self.start is not None else None
        end = timestamp(self.end) if self.end is not None else None
        if start is None and end is None:
            raise TemporalError("A time window needs at least one bound")
        if start is not None and end is not None:
            if start > end or (start == end and not (self.start_inclusive and self.end_inclusive)):
                raise TemporalError("Empty or reversed time window")

    def contains(self, value: datetime) -> bool:
        if self.start is not None:
            start = timestamp(self.start)
            if value < start or (value == start and not self.start_inclusive):
                return False
        if self.end is not None:
            end = timestamp(self.end)
            if value > end or (value == end and not self.end_inclusive):
                return False
        return True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class TimeExpression:
    text: str
    span: tuple[int, int]
    kind: str
    relation: str
    amount: int | None
    unit: str
    anchor: str
    window: Window
    policy: str

    def to_dict(self) -> dict:
        return asdict(self)


def _absolute(value: str) -> tuple[datetime, str]:
    if re.fullmatch(r"\d{4}", value):
        return timestamp(value + "-01-01"), "year"
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return timestamp(value + "-01"), "month"
    if "/" in value:
        try:
            return datetime.strptime(value, "%m/%d/%Y"), "day"
        except ValueError as exc:
            raise TemporalError(f"Invalid MM/DD/YYYY date: {value}") from exc
    return timestamp(value), "day"


def extract_time_expressions(question: str, reference_time: str | None) -> list[TimeExpression]:
    """Extract supported phrases, preferring the longest overlapping match.

    This function only extracts expressions. The rule planner additionally rejects
    leftover temporal operators so that partial recognition cannot silently run.
    """
    now = timestamp(reference_time) if reference_time is not None else None
    patterns = [
        ("offset", rf"\b(since|until)\s+(\d+)\s+{UNIT_PATTERN}\s+ago\b"),
        ("calendar_boundary", rf"\b(since|until)\s+(last|this)\s+{UNIT_PATTERN}\b"),
        ("absolute", r"\b(since|until|before|after)\s+(\d{4}(?:-\d{2}(?:-\d{2})?)?|\d{1,2}/\d{1,2}/\d{4})\b"),
        ("rolling", rf"\b(?:(?:in|during|over)\s+)?(?:the\s+)?(?:last|past)\s+(\d+)\s+{UNIT_PATTERN}\b"),
        ("calendar", rf"\b(?:(?:in|during)\s+)?(?:the\s+)?(last|this)\s+{UNIT_PATTERN}\b"),
    ]
    matches = []
    for kind, pattern in patterns:
        for match in re.finditer(pattern, question, re.I):
            matches.append((match.start(), -(match.end() - match.start()), kind, match))
    selected = []
    for _, _, kind, match in sorted(matches, key=lambda x: (x[0], x[1])):
        if any(match.start() < other.end() and match.end() > other.start() for _, other in selected):
            continue
        selected.append((kind, match))
    result = []
    for kind, match in selected:
        amount = None
        if kind != "absolute" and now is None:
            raise TemporalError("Relative calendar expressions require an explicit reference_time")
        anchor = iso(now) if now is not None else None
        policy = "calendar-offset-clamp"
        if kind == "offset":
            relation, number, unit = match.groups()
            relation, amount, unit = relation.lower(), int(number), unit.lower().rstrip("s")
            edge = iso(shift(now, -amount, unit))
            window = Window(start=edge) if relation == "since" else Window(end=edge, end_inclusive=True)
        elif kind == "calendar_boundary":
            relation, which, unit = (s.lower() for s in match.groups())
            unit = unit.rstrip("s")
            amount = -1 if which == "last" else 0
            edge = iso(shift(floor(now, unit), amount, unit))
            # `until last/this year` is defined as through the start of that period.
            window = Window(start=edge) if relation == "since" else Window(end=edge, end_inclusive=True)
            policy = "boundary-at-start-of-calendar-period"
        elif kind == "absolute":
            relation, literal = match.groups()
            relation = relation.lower()
            start, unit = _absolute(literal)
            anchor = literal
            if relation == "since":
                window = Window(start=iso(start))
            elif relation == "until":
                window = Window(end=iso(shift(start, 1, unit)))
            elif relation == "before":
                window = Window(end=iso(start))
            else:
                window = Window(start=iso(shift(start, 1, unit)))
            policy = "whole-named-calendar-period"
        elif kind == "rolling":
            number, unit = match.groups()
            amount, unit, relation = int(number), unit.lower().rstrip("s"), "in"
            if amount <= 0:
                raise TemporalError("Rolling windows require a positive amount")
            window = Window(start=iso(shift(now, -amount, unit)), end=iso(now), end_inclusive=True)
            policy = "rolling-window-ending-at-reference"
        else:
            which, unit = (s.lower() for s in match.groups())
            unit, relation = unit.rstrip("s"), "in"
            amount = -1 if which == "last" else 0
            start = shift(floor(now, unit), amount, unit)
            window = Window(start=iso(start), end=iso(shift(start, 1, unit)))
            policy = "whole-calendar-period"
        result.append(TimeExpression(match.group(), match.span(), kind, relation, amount,
                                     unit, anchor, window, policy))
    return result
