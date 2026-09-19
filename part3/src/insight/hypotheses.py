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
]

# Lag values the validator knows how to build pairs for.
ALLOWED_LAGS: tuple[int, ...] = (0, 1, 2)


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
