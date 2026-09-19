"""Adapter for Part 2's day-aggregate calendar format.

Part 2 ships `data/meeting_features.json` as one record per person per DAY,
already aggregated:

    {"person_id": "user_101", "date": "2026-09-14", "total_meetings": 4,
     "total_meeting_minutes": 210, "back_to_back_count": 1,
     "no_lunch_buffer": false, "after_hours_count": 0, "no_agenda_pct": 0.25,
     "avg_attendee_count": 4.5, "longest_continuous_meeting_stretch_min": 90}

`loaders.py` + `features.py` expect the other shape -- one record per EVENT,
which they then aggregate themselves. Pointed at the day-aggregate file they
parse happily and emit an all-zero feature vector, which is the worst kind of
failure: silent. This module is the bridge.

Their aggregation is genuinely the same computation `features.py` performs, so
nothing is lost by accepting it pre-computed; we map field names and skip the
derivation step. Features they do not carry are left at 0.0 and, being
constant, are automatically skipped by `adaptive_hypotheses` and rejected by
the validator's support floor -- so an absent feature is never reported as a
null finding.

Format detection is automatic: `load_meetings_any()` sniffs the file and routes
to this adapter or to the event-level loader, so the pipeline works with either
Part 2 output or our own fixtures.
"""

from __future__ import annotations

import json
from datetime import date as Date
from pathlib import Path
from typing import Any

from .models import FEATURE_VOCABULARY, DayFeatures, PersonTimeline, StressRecord

__all__ = [
    "is_day_aggregate_format",
    "load_day_aggregates",
    "load_meetings_any",
    "build_timelines_any",
    "infer_scale_max",
]

# A full working day, used to turn "minutes in meetings" into "minutes free".
_WORKDAY_MINUTES = 600.0

# Their key -> our key, where the meaning transfers unchanged.
_DIRECT: dict[str, str] = {
    "total_meetings": "meeting_count",
    "total_meeting_minutes": "total_meeting_minutes",
    "back_to_back_count": "back_to_back_blocks",
    "after_hours_count": "after_hours_meetings",
    "avg_attendee_count": "avg_attendee_count",
    "longest_continuous_meeting_stretch_min": "longest_meeting_stretch_min",
}

_AGGREGATE_MARKERS = (
    "total_meetings", "back_to_back_count", "no_agenda_pct",
    "no_lunch_buffer", "after_hours_count",
)


def _as_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default  # reject NaN


def _as_date(value: Any) -> Date | None:
    try:
        return Date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _records(raw: Any) -> list[dict[str, Any]]:
    """Accept a bare list, or a dict wrapping one under a few likely keys."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        for key in ("events", "meeting_features", "days", "records", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def is_day_aggregate_format(raw: Any) -> bool:
    """True when records look like per-day aggregates rather than per-event rows."""
    records = _records(raw)
    if not records:
        return False
    sample = records[0]
    if "start" in sample and "end" in sample:
        return False  # per-event: has clock times
    return any(marker in sample for marker in _AGGREGATE_MARKERS)


def _features_from_aggregate(record: dict[str, Any]) -> dict[str, float]:
    """Map one day-aggregate record onto the full feature vocabulary."""
    features: dict[str, float] = {name: 0.0 for name in FEATURE_VOCABULARY}

    for their_key, our_key in _DIRECT.items():
        if their_key in record:
            features[our_key] = _as_float(record[their_key])

    total = features["meeting_count"]

    # no_agenda_pct is a proportion; we count meetings.
    if "no_agenda_pct" in record:
        pct = _as_float(record["no_agenda_pct"])
        if pct > 1.0:  # tolerate 0-100 as well as 0-1
            pct /= 100.0
        features["no_agenda_meetings"] = round(pct * total)

    # Their flag is the negation of ours.
    if "no_lunch_buffer" in record:
        features["has_lunch_buffer"] = 0.0 if _as_float(record["no_lunch_buffer"]) else 1.0
    else:
        features["has_lunch_buffer"] = 1.0

    # Not carried directly, but derivable: time not spent in meetings.
    features["focus_time_minutes"] = max(
        0.0, _WORKDAY_MINUTES - features["total_meeting_minutes"]
    )

    # A run of back-to-back meetings implies a chain one longer than the gaps.
    if features["back_to_back_blocks"] > 0:
        features["longest_back_to_back_run"] = features["back_to_back_blocks"] + 1.0

    # large_meetings is a count we cannot recover from a mean; approximate only
    # when the mean itself clears the threshold, otherwise leave it absent
    # rather than invent a number.
    if features["avg_attendee_count"] > 8.0:
        features["large_meetings"] = total

    return features


def load_day_aggregates(path: Path) -> dict[tuple[str, Date], dict[str, float]]:
    """Read Part 2's day-aggregate file into {(person_id, date): features}."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    out: dict[tuple[str, Date], dict[str, float]] = {}
    for record in _records(raw):
        person = str(record.get("person_id") or record.get("employee_id") or "").strip()
        day = _as_date(record.get("date"))
        if not person or day is None:
            continue
        out[(person, day)] = _features_from_aggregate(record)
    return out


def load_meetings_any(path: Path):
    """Return ('aggregate', mapping) or ('events', [MeetingEvent...])."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None

    if raw is not None and is_day_aggregate_format(raw):
        return "aggregate", load_day_aggregates(path)

    from .loaders import load_meeting_events
    return "events", load_meeting_events(path)


def infer_scale_max(stress: list[StressRecord]) -> float:
    """Upper bound of the stress scale actually present in the data.

    Our fixtures run 0-100; Part 1's generator emits roughly 1-43. Severity
    bands are proportions of this, so a 6-point lift is not called "low" on one
    scale and "moderate" on the other. Rounded up to a sensible ceiling rather
    than pinned to the observed max, which would drift run to run.
    """
    if not stress:
        return 100.0
    observed = max(r.stress_score for r in stress)
    for ceiling in (10.0, 25.0, 50.0, 100.0):
        if observed <= ceiling:
            return ceiling
    return float(observed)


def build_timelines_any(
    stress: list[StressRecord], meetings_path: Path
) -> tuple[dict[str, PersonTimeline], str]:
    """Build timelines from whichever Part 2 format is on disk.

    Returns (timelines, format_name) so the caller can report which path ran.
    """
    kind, payload = load_meetings_any(meetings_path)

    if kind == "events":
        from .loaders import build_timelines
        return build_timelines(stress, payload), "events"

    by_key: dict[tuple[str, Date], dict[str, float]] = payload
    stress_index = {(r.person_id, r.date): r for r in stress}

    people = {p for p, _ in by_key} | {p for p, _ in stress_index}
    timelines: dict[str, PersonTimeline] = {}

    for person in sorted(people):
        days_for_person = sorted(
            {d for p, d in by_key if p == person}
            | {d for p, d in stress_index if p == person}
        )
        days: list[DayFeatures] = []
        for day in days_for_person:
            record = stress_index.get((person, day))
            days.append(DayFeatures(
                person_id=person,
                date=day,
                stress_score=record.stress_score if record else None,
                features=by_key.get((person, day), {n: 0.0 for n in FEATURE_VOCABULARY}),
                contributing_factors=list(record.contributing_factors) if record else [],
            ))
        timelines[person] = PersonTimeline(person_id=person, days=days)

    return timelines, "day_aggregate"
