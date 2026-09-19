"""Readers for the Part 1 and Part 2 inputs, and the join that produces timelines.

Both readers are deliberately forgiving: the real upstream files may carry extra
keys or omit optional ones, and a missing field should degrade a single feature,
not kill the run. Where Part 2 omits a derived flag we store None so features.py
recomputes it from the clock times instead of silently reading it as False.
"""

from __future__ import annotations

import csv
import json
from datetime import date as Date
from pathlib import Path
from typing import Any

from .features import compute_day_features
from .models import DayFeatures, MeetingEvent, PersonTimeline, StressRecord


def _parse_date(value: Any) -> Date | None:
    if isinstance(value, Date):
        return value
    if not value:
        return None
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    try:
        return Date.fromisoformat(text)
    except ValueError:
        return None


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def load_stress_scores(path: Path) -> list[StressRecord]:
    """Read Part 1's stress_scores.csv, skipping `#` provenance comment lines."""
    records: list[StressRecord] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as f:
        rows = (line for line in f if not line.lstrip().startswith("#"))
        for row in csv.DictReader(rows):
            person_id = (row.get("person_id") or "").strip()
            day = _parse_date(row.get("date"))
            if not person_id or day is None:
                continue
            raw_factors = (row.get("contributing_factors") or "").strip()
            factors = [f.strip() for f in raw_factors.split("|") if f.strip()]
            records.append(StressRecord(
                person_id=person_id,
                date=day,
                stress_score=_as_float(row.get("stress_score")),
                contributing_factors=factors,
            ))
    return records


def load_meeting_events(path: Path) -> list[MeetingEvent]:
    """Read Part 2's meeting_features.json (dict-with-"events" or a bare list)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw_events = payload.get("events") or payload.get("meetings") or []
    else:
        raw_events = payload
    if not isinstance(raw_events, list):
        return []

    events: list[MeetingEvent] = []
    for i, raw in enumerate(raw_events):
        if not isinstance(raw, dict):
            continue
        person_id = str(raw.get("person_id") or raw.get("employee_id") or "").strip()
        day = _parse_date(raw.get("date") or raw.get("start_date"))
        if not person_id or day is None:
            continue
        events.append(MeetingEvent(
            event_id=str(raw.get("event_id") or f"{person_id}-{day.isoformat()}-{i}"),
            person_id=person_id,
            date=day,
            start=str(raw.get("start") or raw.get("start_time") or "00:00"),
            end=str(raw.get("end") or raw.get("end_time") or raw.get("start") or "00:00"),
            title=str(raw.get("title") or raw.get("summary") or ""),
            attendee_count=_as_int(raw.get("attendee_count") or raw.get("attendees")),
            has_agenda=_as_bool(raw.get("has_agenda", False)),
            is_recurring=_as_bool(raw.get("is_recurring", False)),
            # None => features.py derives the flag from start/end instead.
            is_after_hours=(_as_bool(raw["is_after_hours"]) if "is_after_hours" in raw else None),
            is_back_to_back=(_as_bool(raw["is_back_to_back"]) if "is_back_to_back" in raw else None),
            sentiment=_as_float(raw.get("sentiment"), 0.0),
            category=str(raw.get("category") or raw.get("topic") or "unknown"),
        ))
    return events


def build_timelines(
    stress: list[StressRecord],
    events: list[MeetingEvent],
) -> dict[str, PersonTimeline]:
    """Join stress and calendar data on (person_id, date) into per-person timelines.

    A person-day survives if either side has it: days with meetings but no
    stress reading keep stress_score=None, and days with a reading but no
    meetings get an all-quiet feature vector.
    """
    by_person_day: dict[str, dict[Date, list[MeetingEvent]]] = {}
    for e in events:
        by_person_day.setdefault(e.person_id, {}).setdefault(e.date, []).append(e)

    stress_index: dict[tuple[str, Date], StressRecord] = {}
    for s in stress:
        stress_index[(s.person_id, s.date)] = s

    people = set(by_person_day) | {s.person_id for s in stress}
    timelines: dict[str, PersonTimeline] = {}

    for person_id in sorted(people):
        day_meetings = by_person_day.get(person_id, {})
        days = set(day_meetings) | {d for (p, d) in stress_index if p == person_id}
        rows: list[DayFeatures] = []
        for day in sorted(days):
            record = stress_index.get((person_id, day))
            rows.append(DayFeatures(
                person_id=person_id,
                date=day,
                stress_score=record.stress_score if record else None,
                features=compute_day_features(day_meetings.get(day, [])),
                contributing_factors=list(record.contributing_factors) if record else [],
            ))
        timelines[person_id] = PersonTimeline(person_id=person_id, days=rows)

    return timelines
