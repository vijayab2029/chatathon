"""Shared data contracts for the Team Stress Insight Tool.

ALL DATA IN THIS SYSTEM IS SYNTHETIC. These dataclasses are the integration
contract between Parts 1-5. Do not change a field name without telling the
other part owners.

Interfaces (per the project design plan, section 5):
    Part 1 -> Part 3 : stress_scores.csv
    Part 2 -> Part 3 : meeting_features.json
    Part 3 -> Part 5 : employee_insight.json
    Part 3 -> Part 4 : team_correlations.json
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date as Date
from typing import Any, Literal

# --------------------------------------------------------------------------
# Feature vocabulary. The hypothesis agent may ONLY reference these names,
# and the validator will reject any hypothesis naming something else.
# --------------------------------------------------------------------------

FEATURE_VOCABULARY: dict[str, str] = {
    "meeting_count": "number of meetings that day",
    "total_meeting_minutes": "total minutes spent in meetings that day",
    "back_to_back_blocks": "count of meetings starting <=5 min after the previous one ends",
    "longest_back_to_back_run": "longest chain of consecutive back-to-back meetings",
    "after_hours_meetings": "meetings starting before 08:00 or ending after 18:00",
    "no_agenda_meetings": "meetings with no agenda or description",
    "large_meetings": "meetings with more than 8 attendees",
    "negative_sentiment_meetings": "meetings whose title/description scores negative",
    "recurring_meetings": "meetings that are part of a recurring series",
    "has_lunch_buffer": "1 if a >=30 min gap exists between 11:00-14:00, else 0",
    "focus_time_minutes": "largest uninterrupted gap during work hours, in minutes",
    "context_switches": "number of distinct meeting topics/categories that day",
}

OPERATORS = (">=", ">", "<=", "<", "==")
Operator = Literal[">=", ">", "<=", "<", "=="]

SEVERITY_BANDS = ("minimal", "low", "moderate", "elevated", "high")


# --------------------------------------------------------------------------
# Part 1 input
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class StressRecord:
    """One person-day from Part 1's stress_scores.csv."""

    person_id: str
    date: Date
    stress_score: float  # 0-100, higher = more stressed
    contributing_factors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Part 2 input
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MeetingEvent:
    """One calendar event from Part 2's meeting_features.json."""

    event_id: str
    person_id: str
    date: Date
    start: str  # "HH:MM" local
    end: str    # "HH:MM" local
    title: str
    attendee_count: int
    has_agenda: bool
    is_recurring: bool
    is_after_hours: bool
    is_back_to_back: bool
    sentiment: float  # -1.0 .. 1.0
    category: str     # e.g. "status", "1:1", "review", "external"


# --------------------------------------------------------------------------
# Joined internal representation
# --------------------------------------------------------------------------

@dataclass
class DayFeatures:
    """Derived per-person, per-day feature vector joined to that day's stress."""

    person_id: str
    date: Date
    stress_score: float | None
    features: dict[str, float]
    contributing_factors: list[str] = field(default_factory=list)


@dataclass
class PersonTimeline:
    """A single person's full analysable history."""

    person_id: str
    days: list[DayFeatures]

    def feature_series(self, feature: str) -> list[float]:
        return [d.features.get(feature, 0.0) for d in self.days]

    def stress_series(self) -> list[float | None]:
        return [d.stress_score for d in self.days]


# --------------------------------------------------------------------------
# Hypothesis DSL -- the only thing the hypothesis agent may emit
# --------------------------------------------------------------------------

@dataclass
class Hypothesis:
    """An executable claim: 'when <feature> <op> <threshold> on day D-lag,
    stress on day D differs'."""

    id: str
    feature: str
    operator: Operator
    threshold: float
    lag_days: int  # 0 = same day, 1 = next day
    rationale: str
    source: Literal["baseline", "llm"] = "baseline"

    def describe(self) -> str:
        when = "that day" if self.lag_days == 0 else f"{self.lag_days} day(s) earlier"
        return f"{self.feature} {self.operator} {self.threshold:g} ({when})"


@dataclass
class ValidatedPattern:
    """A hypothesis after deterministic testing. Every number here was computed
    by validator.py -- never by a language model."""

    hypothesis: Hypothesis
    n_exposed: int
    n_unexposed: int
    mean_exposed: float
    mean_unexposed: float
    lift: float          # mean_exposed - mean_unexposed
    pearson_r: float
    p_value: float
    passed: bool
    rejection_reason: str | None = None

    @property
    def severity(self) -> str:
        """Coarse band derived from absolute lift in stress points."""
        a = abs(self.lift)
        if a < 3:
            return "minimal"
        if a < 7:
            return "low"
        if a < 12:
            return "moderate"
        if a < 20:
            return "elevated"
        return "high"

    def evidence_dict(self) -> dict[str, Any]:
        """The ONLY numbers a narrator agent is permitted to cite."""
        return {
            "pattern": self.hypothesis.describe(),
            "feature": self.hypothesis.feature,
            "lift_points": round(self.lift, 1),
            "mean_stress_when_present": round(self.mean_exposed, 1),
            "mean_stress_when_absent": round(self.mean_unexposed, 1),
            "days_observed": self.n_exposed,
            "days_compared": self.n_unexposed,
            "correlation_r": round(self.pearson_r, 2),
            "p_value": round(self.p_value, 3),
            "severity": self.severity,
        }


# --------------------------------------------------------------------------
# Part 3 -> Part 5 output (PRIVATE, per-person)
# --------------------------------------------------------------------------

@dataclass
class CriticVerdict:
    """Record of the privacy critic's review. Retained so the demo can show a
    rejected narration beside its approved replacement."""

    original_text: str
    approved: bool
    issues: list[str]
    revised_text: str


@dataclass
class EmployeeInsight:
    person_id: str
    date_range: tuple[str, str]
    stress_trend: list[dict[str, Any]]      # [{date, stress_score}]
    average_stress: float
    top_patterns: list[dict[str, Any]]      # ValidatedPattern.evidence_dict()
    insight_text: str
    suggested_action: str
    critic_log: list[dict[str, Any]] = field(default_factory=list)
    llm_used: bool = False
    data_provenance: str = "SIMULATED"
    synthetic: bool = True


# --------------------------------------------------------------------------
# Part 3 -> Part 4 output (NON-PERSONAL, pattern-level)
# --------------------------------------------------------------------------

@dataclass
class TeamPattern:
    """One pattern aggregated across people.

    CRITICAL: this dataclass has no person_id field and must never gain one.
    It is built by aggregating across people, never by concatenating
    individual insights -- concatenation would allow re-identification from
    calendar specifics.
    """

    feature: str
    pattern_description: str
    operator: str
    threshold: float
    lag_days: int
    n_people_affected: int
    n_people_analysed: int
    mean_lift_points: float
    max_lift_points: float
    severity_band: str
    calendar_fact: str  # employer-safe framing: attaches cause to the schedule

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TeamCorrelations:
    """Ungated by design. Part 4 owns the k-anonymity policy; Part 3 hands it
    honest counts, including counts below the floor."""

    n_people_analysed: int
    date_range: tuple[str, str]
    patterns: list[dict[str, Any]]
    gating_applied: bool = False
    gating_note: str = (
        "Part 3 applies no k-anonymity gate. Part 4 must enforce the k>=5 "
        "floor before any of this reaches an employer view."
    )
    data_provenance: str = "SIMULATED"
    synthetic: bool = True
