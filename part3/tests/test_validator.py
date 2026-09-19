"""Tests for the analytical core.

These are the tests that let the team answer a judge's "how do you know?".
They construct ``PersonTimeline`` / ``DayFeatures`` objects directly so they do
not depend on the loader or feature-extraction modules.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

# The repo has no installed package; put part3/src on the path.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from insight.hypotheses import (  # noqa: E402
    baseline_hypotheses,
    dedupe,
    validate_hypothesis_shape,
)
from insight.models import DayFeatures, Hypothesis, PersonTimeline  # noqa: E402
from insight.validator import top_patterns, validate, validate_all  # noqa: E402

START = date(2026, 1, 5)


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------

def make_timeline(
    feature_values: list[float],
    stress_values: list[float | None],
    feature: str = "back_to_back_blocks",
    person_id: str = "p-test",
) -> PersonTimeline:
    assert len(feature_values) == len(stress_values)
    days = [
        DayFeatures(
            person_id=person_id,
            date=START + timedelta(days=i),
            stress_score=stress_values[i],
            features={feature: float(feature_values[i])},
        )
        for i in range(len(feature_values))
    ]
    return PersonTimeline(person_id=person_id, days=days)


def hyp(
    feature: str = "back_to_back_blocks",
    operator: str = ">=",
    threshold: float = 2.0,
    lag_days: int = 0,
    hid: str = "h-test",
) -> Hypothesis:
    return Hypothesis(
        id=hid,
        feature=feature,
        operator=operator,  # type: ignore[arg-type]
        threshold=threshold,
        lag_days=lag_days,
        rationale="test hypothesis",
        source="baseline",
    )


# --------------------------------------------------------------------------
# 1. Strong planted signal
# --------------------------------------------------------------------------

def test_strong_planted_signal_passes_with_expected_lift():
    # stress = 40 + 15 * (feature >= 2), exactly.
    feature_values = [(i % 4) for i in range(40)]
    stress_values = [40.0 + 15.0 * (1 if f >= 2 else 0) for f in feature_values]

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=0))

    assert result.passed, result.rejection_reason
    assert result.rejection_reason is None
    assert abs(result.lift - 15.0) < 0.5
    assert abs(result.mean_exposed - 55.0) < 0.5
    assert abs(result.mean_unexposed - 40.0) < 0.5
    assert result.n_exposed == 20
    assert result.n_unexposed == 20
    assert result.p_value <= 0.01
    assert result.pearson_r > 0.8
    assert result.severity == "elevated"


def test_planted_signal_survives_a_lag():
    # Feature on day i-1 drives stress on day i.
    feature_values = [(i % 4) for i in range(40)]
    stress_values: list[float | None] = [50.0]  # day 0 has no predecessor
    for i in range(1, 40):
        stress_values.append(40.0 + 15.0 * (1 if feature_values[i - 1] >= 2 else 0))

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=1))

    assert result.passed, result.rejection_reason
    assert abs(result.lift - 15.0) < 1.0
    assert result.n_exposed + result.n_unexposed == 39


# --------------------------------------------------------------------------
# 2. No relationship
# --------------------------------------------------------------------------

def test_no_relationship_does_not_pass():
    feature_values = [(i % 5) for i in range(40)]
    stress_values: list[float | None] = [50.0] * 40  # constant stress

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=0))

    assert not result.passed
    assert result.rejection_reason is not None
    assert result.lift == 0.0
    assert result.p_value > 0.1
    # Constant stress has zero variance -> correlation is reported as 0.0.
    assert result.pearson_r == 0.0


def test_noise_only_relationship_does_not_pass():
    # Feature alternates; stress is drawn independently of it. Seeded, so this
    # is one fixed null dataset rather than a flaky random one.
    rnd = random.Random(0)
    feature_values = [0.0, 3.0] * 20
    stress_values: list[float | None] = [round(rnd.uniform(40, 60), 1) for _ in range(40)]

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=0, hid="h-noise"))

    assert result.n_exposed == 20 and result.n_unexposed == 20
    assert not result.passed
    assert result.rejection_reason is not None
    assert "distinguishable" in result.rejection_reason
    assert result.p_value > 0.1


# --------------------------------------------------------------------------
# 3. Insufficient support
# --------------------------------------------------------------------------

def test_insufficient_support_is_rejected_with_a_support_reason():
    # Exactly 2 exposed days out of 20.
    feature_values = [0.0] * 18 + [5.0, 5.0]
    stress_values: list[float | None] = [40.0] * 18 + [90.0, 92.0]

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=0, hid="h-support"))

    assert result.n_exposed == 2
    assert not result.passed
    assert result.rejection_reason is not None
    assert "support" in result.rejection_reason.lower()
    assert "2" in result.rejection_reason


def test_empty_timeline_returns_failed_pattern_not_an_exception():
    empty = PersonTimeline(person_id="p-empty", days=[])
    result = validate(empty, hyp(hid="h-empty"))

    assert not result.passed
    assert result.n_exposed == 0
    assert result.n_unexposed == 0
    assert result.lift == 0.0
    assert result.p_value == 1.0
    assert result.rejection_reason


def test_all_stress_missing_returns_failed_pattern():
    timeline = make_timeline([1.0, 2.0, 3.0, 4.0], [None, None, None, None])
    result = validate(timeline, hyp(hid="h-nostress"))

    assert not result.passed
    assert result.n_exposed == 0
    assert result.rejection_reason


def test_missing_stress_days_are_skipped_not_imputed():
    feature_values = [3.0, 0.0, 3.0, 0.0, 3.0, 0.0]
    stress_values: list[float | None] = [60.0, 40.0, None, None, 60.0, 40.0]

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=0, hid="h-skip"))

    assert result.n_exposed == 2
    assert result.n_unexposed == 2


# --------------------------------------------------------------------------
# 4. Shape validation (the LLM firewall)
# --------------------------------------------------------------------------

def test_shape_accepts_a_well_formed_dict():
    parsed = validate_hypothesis_shape(
        {
            "feature": "after_hours_meetings",
            "operator": ">=",
            "threshold": 1,
            "lag_days": 1,
            "rationale": "evening meetings eat into recovery time",
        }
    )

    assert parsed is not None
    assert parsed.feature == "after_hours_meetings"
    assert parsed.operator == ">="
    assert parsed.threshold == 1.0
    assert parsed.lag_days == 1
    assert parsed.source == "llm"
    assert parsed.id  # auto-assigned


def test_shape_rejects_unknown_feature():
    assert (
        validate_hypothesis_shape(
            {
                "feature": "vibes_per_hour",
                "operator": ">=",
                "threshold": 2,
                "lag_days": 1,
                "rationale": "made up",
            }
        )
        is None
    )


def test_shape_rejects_bad_operator():
    assert (
        validate_hypothesis_shape(
            {
                "feature": "meeting_count",
                "operator": "=~",
                "threshold": 2,
                "lag_days": 1,
                "rationale": "bad operator",
            }
        )
        is None
    )


def test_shape_rejects_non_numeric_threshold():
    assert (
        validate_hypothesis_shape(
            {
                "feature": "meeting_count",
                "operator": ">=",
                "threshold": "high",
                "lag_days": 1,
                "rationale": "not a number",
            }
        )
        is None
    )


def test_shape_rejects_bad_lag():
    assert (
        validate_hypothesis_shape(
            {
                "feature": "meeting_count",
                "operator": ">=",
                "threshold": 2,
                "lag_days": 9,
                "rationale": "lag out of range",
            }
        )
        is None
    )


def test_shape_never_raises_on_junk():
    for junk in (None, [], "not a dict", {}, {"feature": None}, 42):
        assert validate_hypothesis_shape(junk) is None  # type: ignore[arg-type]


def test_shape_preserves_id_when_supplied():
    parsed = validate_hypothesis_shape(
        {
            "id": "llm-7",
            "feature": "focus_time_minutes",
            "operator": "<",
            "threshold": 45.0,
            "lag_days": 0,
            "rationale": "no deep work window",
        }
    )
    assert parsed is not None
    assert "llm-7" in parsed.id


# --------------------------------------------------------------------------
# 5. Zero variance
# --------------------------------------------------------------------------

def test_zero_variance_feature_gives_zero_r_and_no_exception():
    feature_values = [2.0] * 20  # constant feature
    stress_values: list[float | None] = [40.0 + i for i in range(20)]  # varying stress

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=2.0, lag_days=0, hid="h-zerovar-f"))

    assert result.pearson_r == 0.0
    assert not result.passed  # every day is exposed, no comparison group
    assert result.n_unexposed == 0


def test_zero_variance_stress_gives_zero_r_and_no_exception():
    feature_values = [float(i % 6) for i in range(20)]
    stress_values: list[float | None] = [55.0] * 20  # constant stress

    timeline = make_timeline(feature_values, stress_values)
    result = validate(timeline, hyp(threshold=3.0, lag_days=0, hid="h-zerovar-s"))

    assert result.pearson_r == 0.0
    assert not result.passed


def test_single_pair_does_not_raise():
    timeline = make_timeline([4.0], [70.0])
    result = validate(timeline, hyp(threshold=2.0, lag_days=0, hid="h-single"))

    assert not result.passed
    assert result.pearson_r == 0.0


# --------------------------------------------------------------------------
# 6. Determinism
# --------------------------------------------------------------------------

def _weak_signal_timeline() -> PersonTimeline:
    """A modest effect buried in noise, so the p-value is strictly between 0
    and 1 -- a degenerate 0.0 would make the determinism check vacuous."""
    rnd = random.Random(100)
    feature_values = [float(i % 3) for i in range(30)]
    stress_values: list[float | None] = [
        45.0 + 3.0 * (1 if f >= 2 else 0) + rnd.uniform(-6, 6) for f in feature_values
    ]
    return make_timeline(feature_values, stress_values)


def test_p_value_is_reproducible_across_calls():
    timeline = _weak_signal_timeline()

    h = hyp(threshold=2.0, lag_days=0, hid="h-determinism")
    first = validate(timeline, h)
    second = validate(timeline, h)

    assert first.p_value == second.p_value
    assert first.lift == second.lift
    assert first.pearson_r == second.pearson_r
    assert 0.0 < first.p_value < 1.0


def test_p_value_depends_only_on_hypothesis_id_not_object_identity():
    timeline = _weak_signal_timeline()

    a = hyp(threshold=2.0, lag_days=0, hid="h-same-id")
    b = hyp(threshold=2.0, lag_days=0, hid="h-same-id")
    assert a is not b
    assert validate(timeline, a).p_value == validate(timeline, b).p_value


# --------------------------------------------------------------------------
# Baselines, dedupe, ranking
# --------------------------------------------------------------------------

def test_baseline_set_is_deterministic_and_covers_required_claims():
    first = baseline_hypotheses()
    second = baseline_hypotheses()

    assert [h.id for h in first] == [h.id for h in second]
    assert 10 <= len(first) <= 14
    assert all(h.source == "baseline" for h in first)
    assert all(h.rationale.strip() for h in first)

    claims = {(h.feature, h.operator, h.threshold, h.lag_days) for h in first}
    required = {
        ("back_to_back_blocks", ">=", 2.0, 1),
        ("after_hours_meetings", ">=", 1.0, 1),
        ("no_agenda_meetings", ">=", 2.0, 1),
        ("meeting_count", ">=", 5.0, 0),
        ("meeting_count", ">=", 5.0, 1),
        ("large_meetings", ">=", 1.0, 1),
        ("negative_sentiment_meetings", ">=", 1.0, 1),
        ("has_lunch_buffer", "==", 0.0, 0),
        ("focus_time_minutes", "<", 60.0, 0),
        ("context_switches", ">=", 4.0, 1),
        ("longest_back_to_back_run", ">=", 3.0, 1),
        ("total_meeting_minutes", ">=", 240.0, 1),
    }
    assert required <= claims

    # Ids must be unique -- the permutation seed is derived from them.
    assert len({h.id for h in first}) == len(first)


def test_dedupe_prefers_the_baseline_copy():
    baseline = hyp(hid="baseline-1")
    baseline.source = "baseline"
    llm_dupe = Hypothesis(
        id="llm-1",
        feature="back_to_back_blocks",
        operator=">=",
        threshold=2.0,
        lag_days=0,
        rationale="model restated the same claim",
        source="llm",
    )
    novel = Hypothesis(
        id="llm-2",
        feature="large_meetings",
        operator=">=",
        threshold=1.0,
        lag_days=1,
        rationale="genuinely new",
        source="llm",
    )

    # Baseline second: the duplicate should still resolve to the baseline copy.
    out = dedupe([llm_dupe, baseline, novel])
    assert len(out) == 2
    assert out[0].source == "baseline"
    assert out[1].id == "llm-2"


def test_validate_all_sorts_passed_first_then_by_absolute_lift():
    feature_values = [(i % 4) for i in range(40)]
    stress_values: list[float | None] = [
        40.0 + 15.0 * (1 if f >= 2 else 0) for f in feature_values
    ]
    timeline = make_timeline(feature_values, stress_values)

    hypotheses = [
        hyp(feature="meeting_count", threshold=1.0, hid="h-absent"),  # feature missing
        hyp(threshold=2.0, hid="h-strong"),
        hyp(feature="large_meetings", threshold=1.0, hid="h-absent-2"),
    ]
    results = validate_all(timeline, hypotheses)

    assert len(results) == 3
    assert results[0].hypothesis.id == "h-strong"
    assert results[0].passed
    passed_flags = [r.passed for r in results]
    assert passed_flags == sorted(passed_flags, reverse=True)
    lifts = [abs(r.lift) for r in results if r.passed]
    assert lifts == sorted(lifts, reverse=True)


def test_top_patterns_returns_only_passing_and_respects_n():
    feature_values = [(i % 4) for i in range(40)]
    stress_values: list[float | None] = [
        40.0 + 15.0 * (1 if f >= 2 else 0) for f in feature_values
    ]
    timeline = make_timeline(feature_values, stress_values)

    hypotheses = [
        hyp(threshold=2.0, hid="h-strong"),
        hyp(feature="meeting_count", threshold=1.0, hid="h-absent"),
    ]

    top = top_patterns(timeline, hypotheses, n=3)
    assert len(top) == 1
    assert top[0].passed
    assert top_patterns(timeline, hypotheses, n=0) == []


def test_baseline_suite_runs_on_a_realistic_timeline_without_raising():
    days = []
    for i in range(30):
        days.append(
            DayFeatures(
                person_id="p-real",
                date=START + timedelta(days=i),
                stress_score=None if i % 7 == 6 else 45.0 + (i % 9),
                features={
                    "meeting_count": float(i % 8),
                    "total_meeting_minutes": float(30 * (i % 8)),
                    "back_to_back_blocks": float(i % 3),
                    "longest_back_to_back_run": float(i % 4),
                    "after_hours_meetings": float(i % 2),
                    "no_agenda_meetings": float(i % 3),
                    "large_meetings": float(i % 2),
                    "negative_sentiment_meetings": float(i % 4 == 0),
                    "recurring_meetings": float(i % 5),
                    "has_lunch_buffer": float(i % 2),
                    "focus_time_minutes": float(20 * (i % 6)),
                    "context_switches": float(i % 5),
                },
            )
        )
    timeline = PersonTimeline(person_id="p-real", days=days)

    results = validate_all(timeline, baseline_hypotheses())
    assert len(results) == len(baseline_hypotheses())
    for r in results:
        assert 0.0 <= r.p_value <= 1.0
        assert -1.0 <= r.pearson_r <= 1.0
        assert r.passed == (r.rejection_reason is None)
        r.evidence_dict()  # must be serialisable without raising
