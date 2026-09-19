"""Hypothesis generation and defensive parsing.

Two jobs:

1. ``baseline_hypotheses()`` -- a deterministic, hand-authored candidate set so
   the pipeline produces real, validated findings with the LLM switched off
   entirely. No demo path depends on a model being reachable.
2. ``validate_hypothesis_shape()`` -- the firewall between a language model and
   the analytical core. An LLM may *suggest* what to test; it may never define
   a feature, an operator, or a number that reaches the validator unchecked.

Nothing in this module computes a statistic. It only decides what is worth
testing. All arithmetic lives in ``validator.py``.
"""

from __future__ import annotations

from typing import Any, Iterable, cast

from .models import FEATURE_VOCABULARY, OPERATORS, Hypothesis, Operator

__all__ = [
    "baseline_hypotheses",
    "validate_hypothesis_shape",
    "dedupe",
    "THRESHOLD_LADDER",
    "UNBINNED_FEATURES",
    "snap_threshold",
]

# Lag values the validator knows how to build pairs for.
ALLOWED_LAGS: tuple[int, ...] = (0, 1, 2)


# --------------------------------------------------------------------------
# Threshold ladder
# --------------------------------------------------------------------------
#
# See the employer-view privacy redesign spec, section 2.1.
#
# `adaptive_hypotheses` used to cut at the midpoint between two of ONE person's
# observed values. That produced thresholds like `focus_time_minutes >= 407.5`
# -- a number that could only have come from a single person's data, and which
# a manager who sits in those meetings can work backwards from.
#
# The subtler damage was to the counts. `emit.aggregate_team_patterns` groups
# on (feature, operator, threshold, lag_days), so two people with the SAME
# underlying problem and different bespoke cut points landed in different
# buckets. `n_people_affected` measured threshold coincidence rather than
# prevalence, and the k-anonymity gate then suppressed genuine aggregates as
# though they were individuals: on a 12-person run it withheld 10 of 12
# patterns. No value of k fixes that, because the damage happens upstream of
# the gate.
#
# Snapping happens BEFORE validation, never after. A threshold rewritten after
# the fact would no longer be the number the lift, Pearson r and permutation
# p-value were computed against, and the employer would be shown a claim that
# nothing had tested. Binding at birth keeps every reported figure attached to
# the test that produced it.
#
# Rungs sit inside each feature's observed range, measured from the 12-person
# fixture set. They are whole numbers on purpose: a shared rung with a
# fractional part still reads as a fingerprint.
THRESHOLD_LADDER: dict[str, tuple[float, ...]] = {
    "meeting_count": (2.0, 4.0, 6.0, 8.0),
    "total_meeting_minutes": (60.0, 120.0, 180.0, 240.0, 360.0),
    "back_to_back_blocks": (1.0, 2.0, 3.0, 4.0),
    "longest_back_to_back_run": (2.0, 3.0, 4.0),
    # Starts at 2, not 1: "days that switch between 1+ meeting topics" is true
    # of every day with a meeting and says nothing.
    "context_switches": (2.0, 3.0, 4.0),
    "focus_time_minutes": (60.0, 120.0, 240.0, 360.0, 480.0),
    "after_hours_meetings": (1.0, 2.0),
    "no_agenda_meetings": (1.0, 2.0, 3.0, 4.0),
    "large_meetings": (1.0, 2.0, 3.0),
    "negative_sentiment_meetings": (1.0, 2.0, 3.0),
    "recurring_meetings": (1.0, 2.0, 3.0, 4.0),
    # Present only in Part 2's day-aggregate format (see adapters.py).
    "avg_attendee_count": (3.0, 5.0, 8.0, 12.0),
    "longest_meeting_stretch_min": (60.0, 120.0, 180.0),
}

# Binary features have no meaningful ladder -- 0 and 1 are already the only
# cut points, and both are shared by construction, so neither can identify.
UNBINNED_FEATURES: frozenset[str] = frozenset({"has_lunch_buffer"})


def snap_threshold(feature: str, operator: str, value: float) -> float:
    """Return the nearest ladder rung for ``value``, in the widening direction.

    For ``>=`` / ``>`` we snap DOWN and for ``<=`` / ``<`` we snap UP, because
    both admit MORE days into the exposed group. Widening is what merges people
    onto a shared rung; snapping toward the narrower side would keep splitting
    them, which is the behaviour this function exists to remove.

    A cut outside the ladder's range clamps to the nearest end rather than
    being dropped -- a hypothesis is still worth testing at the closest shared
    cut point we have.

    Unknown or unbinned features pass through unchanged.
    """
    rungs = THRESHOLD_LADDER.get(feature)
    if not rungs or feature in UNBINNED_FEATURES:
        return float(value)

    value = float(value)
    if operator in (">=", ">"):
        below = [r for r in rungs if r <= value]
        return below[-1] if below else rungs[0]
    if operator in ("<=", "<"):
        above = [r for r in rungs if r >= value]
        return above[0] if above else rungs[-1]
    # "==" on a laddered feature: nearest rung, ties going low.
    return min(rungs, key=lambda r: (abs(r - value), r))


# --------------------------------------------------------------------------
# Baseline candidate set
# --------------------------------------------------------------------------

def _baseline_id(feature: str, operator: str, threshold: float, lag_days: int) -> str:
    """Stable, human-readable id. Stability matters: the validator seeds its
    permutation test from the id, so a stable id means reproducible p-values."""
    op_slug = {
        ">=": "ge",
        ">": "gt",
        "<=": "le",
        "<": "lt",
        "==": "eq",
    }[operator]
    return f"baseline::{feature}::{op_slug}::{threshold:g}::lag{lag_days}"


# (feature, operator, threshold, lag_days, rationale)
_BASELINE_SPECS: tuple[tuple[str, str, float, int, str], ...] = (
    (
        "back_to_back_blocks", ">=", 2, 1,
        "Two or more meetings with no gap leaves no room to reset, and the cost "
        "often lands the following day.",
    ),
    (
        "after_hours_meetings", ">=", 1, 1,
        "A meeting before 08:00 or after 18:00 eats into recovery time outside "
        "working hours.",
    ),
    (
        "no_agenda_meetings", ">=", 2, 1,
        "Meetings without an agenda are harder to prepare for and more likely to "
        "overrun or need a follow-up.",
    ),
    (
        "meeting_count", ">=", 5, 0,
        "A heavily booked day leaves little time for the work the meetings are "
        "about.",
    ),
    (
        "meeting_count", ">=", 5, 1,
        "A heavily booked day can push its unfinished work into the next day.",
    ),
    (
        "large_meetings", ">=", 1, 1,
        "Meetings with more than eight attendees tend to be broadcast-style and "
        "low-agency for most participants.",
    ),
    (
        "negative_sentiment_meetings", ">=", 1, 1,
        "A meeting framed in negative language (escalation, incident, postmortem) "
        "tends to carry over.",
    ),
    (
        "has_lunch_buffer", "==", 0, 0,
        "No 30-minute break between 11:00 and 14:00 means a day with no genuine "
        "pause in it.",
    ),
    (
        "focus_time_minutes", "<", 60, 0,
        "Without a single uninterrupted hour, deep work has to be squeezed into "
        "the edges of the day.",
    ),
    (
        "context_switches", ">=", 4, 1,
        "Jumping between four or more distinct meeting topics carries a switching "
        "cost that accumulates.",
    ),
    (
        "longest_back_to_back_run", ">=", 3, 1,
        "A chain of three or more consecutive meetings is a long stretch with no "
        "chance to step away.",
    ),
    (
        "total_meeting_minutes", ">=", 240, 1,
        "Four or more hours in meetings leaves under half a normal day for "
        "everything else.",
    ),
    (
        "recurring_meetings", ">=", 4, 1,
        "A calendar dominated by standing meetings offers little discretion over "
        "how the day is spent.",
    ),
    (
        "total_meeting_minutes", ">=", 240, 0,
        "A four-hour meeting load shows up in how the day itself feels.",
    ),
)


def baseline_hypotheses() -> list[Hypothesis]:
    """The deterministic candidate set. Same list, same order, every run.

    These exist so that Part 3 can answer "how do you know?" even if no
    language model is available. Every one references a feature that is in
    ``FEATURE_VOCABULARY``; that invariant is asserted here rather than
    discovered at validation time.
    """
    out: list[Hypothesis] = []
    for feature, operator, threshold, lag_days, rationale in _BASELINE_SPECS:
        # Fail loudly at import/authoring time, not silently in the demo.
        assert feature in FEATURE_VOCABULARY, f"unknown baseline feature {feature!r}"
        assert operator in OPERATORS, f"unknown baseline operator {operator!r}"
        assert lag_days in ALLOWED_LAGS, f"unsupported baseline lag {lag_days!r}"
        # Curated thresholds are already round and are shared by every person,
        # so they do not identify anyone. They are snapped anyway so that ONE
        # rule governs every threshold in the system: a curated "5+ meetings"
        # sitting one rung away from an adaptive "4+" would split the very
        # people the ladder exists to merge. meeting_count 5 -> 4 is the only
        # baseline this moves.
        threshold = snap_threshold(feature, operator, float(threshold))
        out.append(
            Hypothesis(
                id=_baseline_id(feature, operator, float(threshold), lag_days),
                feature=feature,
                operator=cast(Operator, operator),
                threshold=float(threshold),
                lag_days=lag_days,
                rationale=rationale,
                source="baseline",
            )
        )
    return out


# --------------------------------------------------------------------------
# Defensive parsing of LLM output
# --------------------------------------------------------------------------

def _coerce_number(value: Any) -> float | None:
    """Return a float, or None if ``value`` is not a plain number.

    Booleans are rejected explicitly: ``True`` is an int in Python, but a model
    emitting ``true`` as a threshold has misunderstood the schema.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        # NaN / inf would poison every comparison downstream.
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    if isinstance(value, str):
        try:
            f = float(value.strip())
        except (ValueError, AttributeError):
            return None
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    return None


_LLM_COUNTER = {"n": 0}


def validate_hypothesis_shape(obj: dict) -> Hypothesis | None:
    """Parse one LLM-proposed hypothesis. Never raises; returns None on reject.

    Rejects when:
      * ``obj`` is not a dict
      * ``feature`` is not a key of ``FEATURE_VOCABULARY``
      * ``operator`` is not in ``OPERATORS``
      * ``threshold`` is not numeric
      * ``lag_days`` is not one of 0, 1, 2

    On accept, ``source`` is forced to ``"llm"`` and an id is assigned if the
    model did not supply a usable one.
    """
    try:
        if not isinstance(obj, dict):
            return None

        feature = obj.get("feature")
        if not isinstance(feature, str) or feature not in FEATURE_VOCABULARY:
            return None

        operator = obj.get("operator")
        if not isinstance(operator, str) or operator not in OPERATORS:
            return None

        threshold = _coerce_number(obj.get("threshold"))
        if threshold is None:
            return None
        # A model asked to propose a threshold will happily invent a precise
        # one, and a precise threshold is a fingerprint whether it was derived
        # from a person's data or guessed. This is the same firewall as the
        # feature and operator checks above, applied to the number.
        threshold = snap_threshold(feature, operator, threshold)

        lag_raw = _coerce_number(obj.get("lag_days"))
        if lag_raw is None or lag_raw != int(lag_raw):
            return None
        lag_days = int(lag_raw)
        if lag_days not in ALLOWED_LAGS:
            return None

        rationale = obj.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            rationale = "(no rationale supplied)"
        else:
            rationale = rationale.strip()

        raw_id = obj.get("id")
        if isinstance(raw_id, str) and raw_id.strip():
            hyp_id = f"llm::{raw_id.strip()}"
        else:
            _LLM_COUNTER["n"] += 1
            hyp_id = (
                f"llm::{feature}::{operator}::{threshold:g}::lag{lag_days}"
                f"::{_LLM_COUNTER['n']}"
            )

        return Hypothesis(
            id=hyp_id,
            feature=feature,
            operator=cast(Operator, operator),
            threshold=threshold,
            lag_days=lag_days,
            rationale=rationale,
            source="llm",
        )
    except Exception:
        # A malformed payload must degrade to "no hypothesis", never to a crash
        # in the middle of a demo.
        return None


# --------------------------------------------------------------------------
# Dedupe
# --------------------------------------------------------------------------

def _dedupe_key(h: Hypothesis) -> tuple[str, str, float, int]:
    return (h.feature, str(h.operator), float(h.threshold), int(h.lag_days))


def dedupe(hyps: Iterable[Hypothesis]) -> list[Hypothesis]:
    """Drop duplicate claims, keeping the baseline copy when one exists.

    Two hypotheses are the same claim if they share (feature, operator,
    threshold, lag_days) -- the rationale text is irrelevant to what gets
    tested. First-seen order is preserved.
    """
    order: list[tuple[str, str, float, int]] = []
    chosen: dict[tuple[str, str, float, int], Hypothesis] = {}

    for h in hyps:
        if h is None:  # tolerate a None slipping in from a parse step
            continue
        key = _dedupe_key(h)
        existing = chosen.get(key)
        if existing is None:
            order.append(key)
            chosen[key] = h
        elif existing.source != "baseline" and h.source == "baseline":
            # Prefer the curated copy; keep its position in the ordering.
            chosen[key] = h

    return [chosen[k] for k in order]


# --------------------------------------------------------------------------
# Adaptive, data-driven hypotheses
# --------------------------------------------------------------------------
#
# The curated baseline set uses absolute thresholds ("back_to_back_blocks >= 2").
# Those are readable, but they are dead for anyone whose feature never reaches
# the threshold: emp_007's back_to_back_blocks peaks at 1, so the >= 2 test had
# zero exposed days and could never fire, and the person's real planted driver
# went undetected. A threshold has to come from the person's own distribution.
#
# For each feature we take the observed values and keep the cut points that
# actually split the person's days into two usable groups. That guarantees every
# hypothesis is answerable for THAT person rather than for an imagined average one.

_MIN_GROUP = 3          # matches the validator's support floor
_MAX_CUTS_PER_FEATURE = 2


def adaptive_hypotheses(
    timeline: "PersonTimeline",
    lags: tuple[int, ...] = (0, 1),
) -> list[Hypothesis]:
    """Per-person hypotheses whose thresholds come from that person's own data.

    Only emits a hypothesis when the cut point leaves at least ``_MIN_GROUP``
    days on each side, so nothing is proposed that the validator must then
    reject for insufficient support.
    """
    out: list[Hypothesis] = []
    days = getattr(timeline, "days", None) or []
    if len(days) < _MIN_GROUP * 2:
        return out

    for feature in FEATURE_VOCABULARY:
        try:
            series = [float(d.features.get(feature, 0.0)) for d in days]
        except Exception:
            continue
        distinct = sorted({v for v in series})
        if len(distinct) < 2:
            continue  # constant feature: no cut point can split it

        # Candidate cuts sit BETWEEN observed values, so ">=" is unambiguous.
        # Each midpoint is then snapped onto the shared ladder: the raw value
        # is a fingerprint of this person's distribution and must not survive
        # into a Hypothesis. See snap_threshold().
        cuts: list[float] = []
        seen_rungs: set[float] = set()
        for lower, upper in zip(distinct, distinct[1:]):
            cut = snap_threshold(feature, ">=", (lower + upper) / 2.0)
            if cut in seen_rungs:
                continue  # two midpoints landing on one rung is one hypothesis
            # Support is re-checked AFTER snapping. The raw midpoint may have
            # split the days evenly while the rung it snaps to does not, and
            # proposing a hypothesis the validator will only reject wastes a
            # permutation run.
            n_exposed = sum(1 for v in series if v >= cut)
            if n_exposed >= _MIN_GROUP and (len(series) - n_exposed) >= _MIN_GROUP:
                seen_rungs.add(cut)
                cuts.append(cut)
        if not cuts:
            continue

        # Prefer the most balanced splits -- they have the most statistical power.
        cuts.sort(key=lambda c: abs(sum(1 for v in series if v >= c) - len(series) / 2))
        for cut in cuts[:_MAX_CUTS_PER_FEATURE]:
            for lag in lags:
                out.append(Hypothesis(
                    id=f"adaptive::{feature}::ge::{cut:g}::lag{lag}",
                    feature=feature,
                    operator=">=",
                    threshold=cut,
                    lag_days=lag,
                    rationale=(
                        f"shared cut point at {cut:g}, selected because it splits "
                        f"this timeline into two groups large enough to test"
                    ),
                    source="baseline",
                ))
    return out
