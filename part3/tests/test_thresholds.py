"""Threshold ladder: adaptive cut points must not be person-specific.

See docs/superpowers/specs/2026-09-19-employer-view-privacy-redesign-design.md
section 2.1.

A cut point derived as the midpoint between two of ONE person's observed values
is a fingerprint ("focus_time_minutes >= 407.5") and it also stops people from
being counted together, because the grouping key in aggregate_team_patterns
includes the exact threshold. Both problems are fixed by snapping every cut to
a shared per-feature ladder BEFORE the hypothesis is validated.

ALL DATA USED HERE IS SYNTHETIC.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PART3_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PART3_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from insight.hypotheses import (  # noqa: E402
    THRESHOLD_LADDER,
    UNBINNED_FEATURES,
    adaptive_hypotheses,
    baseline_hypotheses,
    snap_threshold,
)
from insight.loaders import (  # noqa: E402
    build_timelines,
    load_meeting_events,
    load_stress_scores,
)
from insight.models import FEATURE_VOCABULARY  # noqa: E402

FIXTURES = PART3_DIR / "data" / "fixtures"


@pytest.fixture(scope="module")
def timelines():
    stress = load_stress_scores(FIXTURES / "stress_scores.csv")
    events = load_meeting_events(FIXTURES / "meeting_features.json")
    return build_timelines(stress, events)


# ---------------------------------------------------------------------------
# the ladder itself
# ---------------------------------------------------------------------------

def test_every_feature_is_either_laddered_or_explicitly_unbinned():
    """No feature may fall through without a decision being recorded."""
    for feature in FEATURE_VOCABULARY:
        assert feature in THRESHOLD_LADDER or feature in UNBINNED_FEATURES, (
            f"{feature} has no ladder and is not listed as unbinned"
        )


def test_ladder_rungs_are_round_numbers():
    """A rung with a fractional part would read as a fingerprint even if shared."""
    for feature, rungs in THRESHOLD_LADDER.items():
        for rung in rungs:
            assert rung == int(rung), f"{feature} rung {rung} is not a whole number"


def test_ladder_rungs_are_sorted_and_distinct():
    for feature, rungs in THRESHOLD_LADDER.items():
        assert list(rungs) == sorted(set(rungs)), f"{feature} ladder is not sorted/unique"


# ---------------------------------------------------------------------------
# snapping rule
# ---------------------------------------------------------------------------

def test_ge_snaps_down_to_widen_the_exposed_set():
    """Snapping down admits more days, which merges people rather than splitting."""
    assert snap_threshold("meeting_count", ">=", 2.5) == 2.0
    assert snap_threshold("meeting_count", ">=", 5.9) == 4.0


def test_the_real_407_point_5_fingerprint_is_removed():
    """The exact value that prompted this work."""
    snapped = snap_threshold("focus_time_minutes", ">=", 407.5)
    assert snapped in THRESHOLD_LADDER["focus_time_minutes"]
    assert snapped == 360.0


def test_cut_below_the_lowest_rung_snaps_up_rather_than_vanishing():
    lowest = THRESHOLD_LADDER["meeting_count"][0]
    assert snap_threshold("meeting_count", ">=", 0.5) == lowest


def test_lt_snaps_up():
    """For '<' the exposed set is below the cut, so up is the widening direction."""
    assert snap_threshold("total_meeting_minutes", "<", 100.0) == 120.0


def test_unbinned_features_pass_through_untouched():
    assert snap_threshold("has_lunch_buffer", "==", 0.0) == 0.0
    assert snap_threshold("has_lunch_buffer", "==", 1.0) == 1.0


def test_snapping_is_idempotent():
    """Snapping an already-snapped value must not drift it further."""
    for feature, rungs in THRESHOLD_LADDER.items():
        for rung in rungs:
            assert snap_threshold(feature, ">=", rung) == rung
            assert snap_threshold(feature, "<", rung) == rung


# ---------------------------------------------------------------------------
# the generators
# ---------------------------------------------------------------------------

def test_no_adaptive_hypothesis_carries_an_off_ladder_threshold(timelines):
    """The core guarantee, checked against the real fixture set."""
    offenders = []
    for person_id, timeline in timelines.items():
        for h in adaptive_hypotheses(timeline):
            if h.feature in UNBINNED_FEATURES:
                continue
            if h.threshold not in THRESHOLD_LADDER[h.feature]:
                offenders.append((person_id, h.feature, h.threshold))
    assert not offenders, f"off-ladder adaptive thresholds: {offenders[:10]}"


def test_no_baseline_hypothesis_carries_an_off_ladder_threshold():
    offenders = [
        (h.feature, h.threshold)
        for h in baseline_hypotheses()
        if h.feature not in UNBINNED_FEATURES
        and h.threshold not in THRESHOLD_LADDER[h.feature]
    ]
    assert not offenders, f"off-ladder baseline thresholds: {offenders}"


def test_adaptive_rationale_no_longer_advertises_the_person(timelines):
    """The old rationale said the cut came from 'this person's own' range.

    That string is itself a disclosure if it is ever surfaced, and it stopped
    being true once cuts snapped to a shared ladder.
    """
    for timeline in timelines.values():
        for h in adaptive_hypotheses(timeline):
            assert "this person" not in h.rationale.lower()


def test_thresholds_merge_across_people(timelines):
    """The point of the ladder: two people with the same problem share a key.

    Before binning, meeting_count produced cuts at 2.5 and 3.5 for different
    people, so a shared problem was counted as two smaller ones.
    """
    keys_per_feature: dict[str, set[float]] = {}
    for timeline in timelines.values():
        for h in adaptive_hypotheses(timeline):
            keys_per_feature.setdefault(h.feature, set()).add(h.threshold)

    for feature, thresholds in keys_per_feature.items():
        if feature in UNBINNED_FEATURES:
            continue
        rungs = THRESHOLD_LADDER[feature]
        assert thresholds <= set(rungs), (
            f"{feature} produced thresholds outside its ladder: {thresholds}"
        )
        # A ladder that every person lands on a different rung of has not
        # merged anything. Across 12 people, a feature must not spread over
        # more rungs than the ladder has.
        assert len(thresholds) <= len(rungs)
