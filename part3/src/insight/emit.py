"""Output assembly for Part 3 -- turns validated patterns into the two
deliverable JSON files.

ALL DATA IN THIS SYSTEM IS SYNTHETIC.

PRIVACY RULE FOR THIS MODULE
----------------------------
`aggregate_team_patterns` is privacy-critical. **NO person_id may appear
anywhere in its output**, and no per-person value may be carried through it.
The team view is built by AGGREGATING ACROSS PEOPLE, never by concatenating
individual insights: a concatenated view would let a manager re-identify
people from calendar specifics ("the one with the 07:30 external call on
Tuesday"), which turns an anonymised team signal back into surveillance of a
named employee. Person ids are used inside this module only as set members
for counting distinct people, and are discarded before anything is emitted.

`build_employee_insight` is the opposite case: employee_insight.json is the
PRIVATE, per-person artifact and is keyed by person_id by design. Part 5 must
only ever show a person their own entry.

Stdlib only, by design.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date as Date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import (
    FEATURE_VOCABULARY,
    CriticVerdict,
    EmployeeInsight,
    TeamCorrelations,
    TeamPattern,
    ValidatedPattern,
)

__all__ = [
    "build_employee_insight",
    "aggregate_team_patterns",
    "calendar_fact",
    "write_outputs",
]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _iso(value: Any) -> str:
    """Render a date/datetime/str as an ISO date string."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, Date):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def _jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, dates and tuples into JSON types."""
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, (datetime, Date)):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _severity_band(lift: float) -> str:
    """Same thresholds as ValidatedPattern.severity, applied to a mean lift."""
    a = abs(lift)
    if a < 3:
        return "minimal"
    if a < 7:
        return "low"
    if a < 12:
        return "moderate"
    if a < 20:
        return "elevated"
    return "high"


def _describe(feature: str, operator: str, threshold: float, lag_days: int) -> str:
    """Mirror of Hypothesis.describe(), built from a bare grouping key."""
    when = "that day" if lag_days == 0 else f"{lag_days} day(s) earlier"
    return f"{feature} {operator} {threshold:g} ({when})"


def _qty(operator: str, threshold: float) -> str:
    """Human phrasing of a threshold test, e.g. '2+' or 'fewer than 60'."""
    n = f"{threshold:g}"
    if operator == ">=":
        return f"{n}+"
    if operator == ">":
        return f"more than {n}"
    if operator == "<=":
        return f"{n} or fewer"
    if operator == "<":
        return f"fewer than {n}"
    if operator == "==":
        return f"exactly {n}"
    return f"{operator} {n}"


def _matches(operator: str, threshold: float, value: float) -> bool:
    if operator == ">=":
        return value >= threshold
    if operator == ">":
        return value > threshold
    if operator == "<=":
        return value <= threshold
    if operator == "<":
        return value < threshold
    if operator == "==":
        return value == threshold
    return False


# Subject templates: one hand-written line per feature in FEATURE_VOCABULARY.
# Every sentence blames the SCHEDULE, never a person.
_SUBJECTS: dict[str, str] = {
    "meeting_count": "Days holding {q} meetings",
    "total_meeting_minutes": "Days with {q} minutes booked in meetings",
    "back_to_back_blocks": "Days with {q} back-to-back meeting blocks",
    "longest_back_to_back_run": "Days containing a run of {q} consecutive back-to-back meetings",
    "after_hours_meetings": "Days with {q} meetings scheduled outside 08:00-18:00",
    "no_agenda_meetings": "Days with {q} meetings booked without an agenda",
    "large_meetings": "Days with {q} meetings of more than 8 attendees",
    "negative_sentiment_meetings": "Days with {q} meetings whose title or description reads negatively",
    "recurring_meetings": "Days with {q} recurring-series meetings",
    "has_lunch_buffer": "Days scheduled with {q} protected midday break",
    "focus_time_minutes": "Days leaving {q} minutes of uninterrupted focus time",
    "context_switches": "Days that switch between {q} different meeting topics",
    # Present only in Part 2's day-aggregate format (see adapters.py). Without
    # entries here the generic fallback splices the raw vocabulary description
    # into the sentence and produces "Days where the longest unbroken run of
    # meeting time, in minutes is 82.5+ are followed by...".
    "avg_attendee_count": "Days whose meetings average {q} attendees",
    "longest_meeting_stretch_min": "Days containing an unbroken meeting stretch of {q} minutes",
}


def calendar_fact(
    feature: str,
    operator: str,
    threshold: float,
    lag_days: int,
) -> str:
    """An employer-safe sentence about a SCHEDULE SHAPE, not about a person.

    The grammatical subject is always the calendar; no individual is named,
    implied, or countable from the sentence.
    """
    q = _qty(operator, threshold)

    # Special cases that read badly from the generic template.
    if feature == "after_hours_meetings" and operator == ">=" and threshold == 1:
        subject = "Meetings scheduled outside 08:00-18:00"
    elif feature == "has_lunch_buffer":
        when_present = _matches(operator, threshold, 1.0)
        when_absent = _matches(operator, threshold, 0.0)
        if when_absent and not when_present:
            subject = "Days scheduled with no protected 30-minute midday break"
        elif when_present and not when_absent:
            subject = "Days that keep a 30-minute midday break free"
        else:
            subject = "Days grouped by whether a midday break stays free"
    elif feature in _SUBJECTS:
        subject = _SUBJECTS[feature].format(q=q)
    else:
        described = FEATURE_VOCABULARY.get(feature, feature.replace("_", " "))
        subject = f"Days where the {described} is {q}"

    if lag_days == 0:
        tail = "are associated with measurably higher strain the same day."
    elif lag_days == 1:
        tail = "are followed by measurably higher strain."
    else:
        tail = f"are followed by measurably higher strain {lag_days} days later."
    return f"{subject} {tail}"


# ---------------------------------------------------------------------------
# per-person output (PRIVATE)
# ---------------------------------------------------------------------------

def build_employee_insight(
    person_id: str,
    timeline_days: Sequence[Any],
    patterns: list[ValidatedPattern],
    insight_text: str,
    suggested_action: str,
    critic: CriticVerdict | None,
    llm_used: bool,
) -> EmployeeInsight:
    """Assemble one person's private insight record.

    `timeline_days` is a sequence of DayFeatures (anything exposing .date and
    .stress_score works). Days with a None stress_score are skipped in the
    trend and in the average, but still count towards the date range.
    """
    trend: list[dict[str, Any]] = []
    scores: list[float] = []
    all_dates: list[str] = []

    for day in timeline_days or []:
        iso = _iso(getattr(day, "date", None))
        if iso:
            all_dates.append(iso)
        score = getattr(day, "stress_score", None)
        if score is None:
            continue
        score = float(score)
        trend.append({"date": iso, "stress_score": round(score, 1)})
        scores.append(score)

    trend.sort(key=lambda row: row["date"])
    average = round(sum(scores) / len(scores), 1) if scores else 0.0
    date_range = (min(all_dates), max(all_dates)) if all_dates else ("", "")

    passing = [p for p in (patterns or []) if getattr(p, "passed", False)]
    chosen = passing if passing else list(patterns or [])
    chosen = sorted(chosen, key=lambda p: abs(p.lift), reverse=True)
    top_patterns = [p.evidence_dict() for p in chosen]

    critic_log: list[dict[str, Any]] = []
    if critic is not None:
        critic_log.append(_jsonable(critic))

    return EmployeeInsight(
        person_id=person_id,
        date_range=date_range,
        stress_trend=trend,
        average_stress=average,
        top_patterns=top_patterns,
        insight_text=insight_text,
        suggested_action=suggested_action,
        critic_log=critic_log,
        llm_used=llm_used,
    )


# ---------------------------------------------------------------------------
# team output (NON-PERSONAL) -- privacy critical
# ---------------------------------------------------------------------------

def aggregate_team_patterns(
    per_person: dict[str, list[ValidatedPattern]],
    n_people_analysed: int,
    date_range: Any,
) -> TeamCorrelations:
    """Aggregate PASSING patterns across people into person-free team signal.

    NO person_id reaches the output. Person ids are used only as dict keys
    inside this function so that `n_people_affected` counts DISTINCT people;
    they are dropped before any TeamPattern is constructed. No per-person
    lift, date, meeting title or other individual value is carried through --
    only the mean and max across the group.
    """
    # key -> {"lifts": {person_id: lift}}   (the inner dict never escapes)
    groups: dict[tuple[str, str, float, int], dict[str, dict[str, float]]] = {}

    for person_id, patterns in (per_person or {}).items():
        for pattern in patterns or []:
            if not getattr(pattern, "passed", False):
                continue
            h = pattern.hypothesis
            key = (h.feature, str(h.operator), float(h.threshold), int(h.lag_days))
            lifts = groups.setdefault(key, {"lifts": {}})["lifts"]
            # If one person somehow has the same key twice, keep the strongest.
            prev = lifts.get(person_id)
            if prev is None or abs(pattern.lift) > abs(prev):
                lifts[person_id] = float(pattern.lift)

    team_patterns: list[TeamPattern] = []
    for (feature, operator, threshold, lag_days), bucket in groups.items():
        by_person = bucket["lifts"]
        n_affected = len(by_person)              # DISTINCT people
        lift_values = list(by_person.values())   # person ids dropped here
        mean_lift = sum(lift_values) / len(lift_values)
        max_lift = max(lift_values, key=abs)
        team_patterns.append(
            TeamPattern(
                feature=feature,
                pattern_description=_describe(feature, operator, threshold, lag_days),
                operator=operator,
                threshold=threshold,
                lag_days=lag_days,
                n_people_affected=n_affected,
                n_people_analysed=n_people_analysed,
                mean_lift_points=round(mean_lift, 1),
                max_lift_points=round(max_lift, 1),
                severity_band=_severity_band(mean_lift),
                calendar_fact=calendar_fact(feature, operator, threshold, lag_days),
            )
        )

    team_patterns.sort(
        key=lambda tp: (-tp.n_people_affected, -tp.mean_lift_points, tp.feature)
    )

    if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
        rng = (_iso(date_range[0]), _iso(date_range[1]))
    else:
        rng = ("", "")

    return TeamCorrelations(
        n_people_analysed=n_people_analysed,
        date_range=rng,
        patterns=[_jsonable(tp.to_dict()) for tp in team_patterns],
    )


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def write_outputs(
    employee_insights: Iterable[EmployeeInsight] | Mapping[str, EmployeeInsight],
    team_correlations: TeamCorrelations,
    out_dir: Path | str,
) -> tuple[Path, Path]:
    """Write employee_insight.json and team_correlations.json; return paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if isinstance(employee_insights, Mapping):
        insights = list(employee_insights.values())
    else:
        insights = list(employee_insights)

    people: dict[str, Any] = {}
    for insight in insights:
        payload = _jsonable(insight)
        people[str(payload.get("person_id", ""))] = payload

    employee_doc = {
        "synthetic": True,
        "data_provenance": "SIMULATED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "people": people,
    }

    employee_path = out / "employee_insight.json"
    team_path = out / "team_correlations.json"

    employee_path.write_text(
        json.dumps(employee_doc, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    team_path.write_text(
        json.dumps(_jsonable(team_correlations), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return employee_path, team_path
