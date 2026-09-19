"""The employer view must not be able to describe an individual.

See docs/superpowers/specs/2026-09-19-employer-view-privacy-redesign-design.md.

These tests guard a contract that is structural rather than textual: the
employer payload is safe because of what it CANNOT contain, not because
something scrubbed it on the way out.

ALL DATA USED HERE IS SYNTHETIC.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PART3_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PART3_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from insight.actions import ACTIONS, resolve_action  # noqa: E402
from insight.gating import DEFAULT_K, apply_k_anonymity  # noqa: E402
from insight.hypotheses import THRESHOLD_LADDER, UNBINNED_FEATURES  # noqa: E402
from insight.models import (  # noqa: E402
    FEATURE_VOCABULARY,
    NO_SIGNAL,
    PREVALENCE_BANDS,
    SEVERITY_BANDS,
    TeamCorrelations,
)
from insight.pipeline import run_pipeline  # noqa: E402
from insight.themes import (  # noqa: E402
    FEATURE_THEME,
    THEME_LABELS,
    THEME_ORDER,
    prevalence_band,
    roll_up_themes,
)


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    run_pipeline(offline=True, out_dir=out)
    return out


@pytest.fixture(scope="module")
def theme_view(written):
    return json.loads((written / "team_themes.json").read_text(encoding="utf-8"))


def _walk(node, path="$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key, value
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield f"{path}[{i}]", None, value
            yield from _walk(value, f"{path}[{i}]")


def _team(counts, n_people=12, lift=10.0, feature="meeting_count"):
    return TeamCorrelations(
        n_people_analysed=n_people,
        date_range=("2026-08-24", "2026-09-20"),
        patterns=[
            {
                "feature": feature,
                "pattern_description": f"{feature} >= 2 (1 day(s) earlier)",
                "operator": ">=",
                "threshold": 2.0,
                "lag_days": 1,
                "n_people_affected": c,
                "n_people_analysed": n_people,
                "mean_lift_points": lift,
                "max_lift_points": lift,
                "severity_band": "moderate",
                "calendar_fact": "Days holding 2+ meetings are followed by higher strain.",
            }
            for c in counts
        ],
    )


# ---------------------------------------------------------------------------
# the fixed-shape guarantee
# ---------------------------------------------------------------------------

def test_the_theme_vocabulary_is_total_over_the_feature_vocabulary():
    """An unmapped feature would vanish silently from the employer view."""
    unmapped = sorted(set(FEATURE_VOCABULARY) - set(FEATURE_THEME))
    assert not unmapped, f"features with no theme: {unmapped}"


def test_every_theme_has_a_label_and_an_action_row():
    for theme_id in THEME_ORDER:
        assert theme_id in THEME_LABELS
        for band in (*SEVERITY_BANDS, NO_SIGNAL):
            action, metric = resolve_action(theme_id, band)
            assert action.strip(), f"{theme_id}/{band} has no action"
            assert metric.strip(), f"{theme_id}/{band} has no verify_metric"


def test_all_five_themes_render_on_a_real_run(theme_view):
    assert [t["theme_id"] for t in theme_view["themes"]] == list(THEME_ORDER)


def test_all_five_themes_render_for_an_empty_team():
    """Set membership must carry no information, so the list never shortens."""
    view = roll_up_themes(_team([]))
    assert [t["theme_id"] for t in view.themes] == list(THEME_ORDER)
    assert all(t["severity_band"] == NO_SIGNAL for t in view.themes)


def test_all_five_themes_render_below_the_floor():
    view = roll_up_themes(_team([9], n_people=3), below_floor=True)
    assert len(view.themes) == len(THEME_ORDER)
    assert view.below_floor is True
    assert all(t["severity_band"] == NO_SIGNAL for t in view.themes)


def test_below_floor_is_distinguishable_from_nothing_to_report():
    """Emitting nothing in both cases would conflate them."""
    small = roll_up_themes(_team([9], n_people=3), below_floor=True)
    quiet = roll_up_themes(_team([]), below_floor=False)
    assert small.below_floor is True
    assert quiet.below_floor is False
    assert [t["severity_band"] for t in small.themes] == \
           [t["severity_band"] for t in quiet.themes]


def test_a_no_signal_theme_still_carries_an_action(theme_view):
    """An empty cell would let a manager infer signal from its absence."""
    for theme in theme_view["themes"]:
        assert theme["action"].strip()
        assert theme["verify_metric"].strip()


# ---------------------------------------------------------------------------
# what the payload may not contain
# ---------------------------------------------------------------------------

_BANNED_KEYS = {
    "person_id",
    "max_lift_points",
    "mean_lift_points",
    "n_people_affected",
    "lift_points",
    "p_value",
    "pearson_r",
    "stress_trend",
    "stress_score",
}


def test_no_identifying_key_appears_at_any_depth(theme_view):
    offenders = [
        (path, key) for path, key, _ in _walk(theme_view)
        if key in _BANNED_KEYS
    ]
    assert not offenders, f"identifying keys in the employer view: {offenders}"


def test_no_person_id_survives_in_the_raw_text(written):
    blob = (written / "team_themes.json").read_text(encoding="utf-8")
    assert "person_id" not in blob
    assert "emp_" not in blob


def test_bands_come_from_closed_vocabularies(theme_view):
    """A UI given a band cannot plot it. That is the point."""
    for theme in theme_view["themes"]:
        assert theme["severity_band"] in (*SEVERITY_BANDS, NO_SIGNAL)
        assert theme["prevalence_band"] in PREVALENCE_BANDS


def test_the_employer_view_carries_no_per_day_axis(theme_view):
    """Part 5 cannot draw a stress timeline from a payload with no timeline."""
    for path, key, value in _walk(theme_view):
        if key == "date_range":
            continue  # the team-level range is retained by explicit decision
        assert not isinstance(value, dict) or "date" not in value, path
        if key and "date" in key:
            pytest.fail(f"per-day field {path} reached the employer view")


def test_any_number_in_employer_text_is_a_shared_ladder_rung(theme_view):
    """Prose is the back door. A fingerprint reads the same in a sentence.

    Numbers survive in calendar_fact ("Days with 2+ meetings...") because that
    is the actionable substance of the finding. They are only safe because
    every one of them is a shared rung rather than a per-person cut point.
    """
    allowed = {r for rungs in THRESHOLD_LADDER.values() for r in rungs}
    allowed |= {0.0, 1.0, 8.0, 30.0}  # binary features and fixed prose figures
    for theme in theme_view["themes"]:
        # Only the DATA-DERIVED fields. action/verify_metric are hand-written
        # constants ("25- and 50-minute meetings"); their numbers cannot be
        # fingerprints because they never came from anyone's calendar.
        for field in ("calendar_fact", "protective_fact"):
            text = theme.get(field) or ""
            for raw in re.findall(r"\d+(?:\.\d+)?", text):
                if ":" in text[max(0, text.find(raw) - 3):text.find(raw) + 6]:
                    continue  # clock times like 08:00-18:00
                assert float(raw) in allowed, (
                    f"{theme['theme_id']}.{field} contains off-ladder number "
                    f"{raw!r}: {text!r}"
                )


# ---------------------------------------------------------------------------
# actions recommend process, not people
# ---------------------------------------------------------------------------

# Targeting is the hazard, not the word "person". "booked hours per person per
# day" is a RATE, and exactly the kind of team-level metric we want; "check in
# with the person who is struggling" is the failure mode. So match the phrases
# that single somebody out, not every noun that can refer to a human.
_PERSON_TARGETING = re.compile(
    r"\bwho(ever|m)?\b"
    r"|\bthose\s+(who|with)\b"
    r"|\bteam\s+members?\b"
    r"|\b(individual|employee)s?\b"
    r"|\b(identify|flag|single\s+out|monitor|track)\b"
    r"|\bcheck\s+in\s+with\b"
    r"|\breach\s+out\s+to\b"
    r"|\bspeak\s+(to|with)\b"
    r"|\bstruggling\b|\bat\s+risk\b|\bburn(ed|t)\s+out\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("sentence", [
    "Check in with whoever is struggling.",
    "Identify the team members with the most fragmented schedules.",
    "Reach out to individuals who are at risk.",
    "Monitor those with the heaviest load.",
])
def test_the_detector_catches_the_sentences_it_exists_to_catch(sentence):
    """A guard that never fires is not a guard.

    Every sentence here is plausible management advice a language model would
    produce unprompted, which is why actions.py resolves from a table instead.
    """
    assert _PERSON_TARGETING.search(sentence), f"detector missed: {sentence!r}"


def test_a_team_level_rate_is_not_mistaken_for_targeting():
    """'per person per day' is a denominator, not a name."""
    assert not _PERSON_TARGETING.search("booked hours per person per day")
    assert not _PERSON_TARGETING.search("share of meetings that carry an agenda")


def test_no_action_targets_a_person():
    """'Check in with whoever is struggling' is the failure mode in one line."""
    offenders = []
    for (theme_id, band), (action, _metric) in ACTIONS.items():
        match = _PERSON_TARGETING.search(action)
        if match:
            offenders.append((theme_id, band, match.group(0), action))
    assert not offenders, f"person-targeting actions: {offenders}"


def test_no_verify_metric_targets_a_person():
    offenders = [
        (theme_id, band, metric)
        for (theme_id, band), (_action, metric) in ACTIONS.items()
        if _PERSON_TARGETING.search(metric)
    ]
    assert not offenders, f"person-targeting verify metrics: {offenders}"


def test_actions_are_deterministic_for_the_same_band():
    """No model in this path: the same input yields the same sentence."""
    for theme_id in THEME_ORDER:
        first = resolve_action(theme_id, "elevated")
        for _ in range(5):
            assert resolve_action(theme_id, "elevated") == first


def test_unknown_band_falls_back_to_maintain_rather_than_raising():
    """A missing table entry must not break an employer view mid-demo."""
    action, metric = resolve_action("meeting_load", "not-a-band")
    assert action.strip() and metric.strip()


# ---------------------------------------------------------------------------
# banding behaviour
# ---------------------------------------------------------------------------

def test_prevalence_never_reveals_a_count():
    assert prevalence_band(0, 12) == "none"
    assert prevalence_band(1, 12) == "some"
    assert prevalence_band(5, 12) == "about half"
    assert prevalence_band(11, 12) == "most"


def test_severity_is_weighted_by_reach():
    """A pattern clearing k by one person must not outweigh a team-wide one."""
    team = TeamCorrelations(
        n_people_analysed=12,
        date_range=("2026-08-24", "2026-09-20"),
        patterns=[
            {**p, "feature": "meeting_count"}
            for p in (
                {"n_people_affected": 11, "mean_lift_points": 4.0,
                 "calendar_fact": "wide but mild", "severity_band": "low"},
                {"n_people_affected": 5, "mean_lift_points": 25.0,
                 "calendar_fact": "narrow but severe", "severity_band": "high"},
            )
        ],
    )
    view = roll_up_themes(team)
    load = next(t for t in view.themes if t["theme_id"] == "meeting_load")
    # Unweighted mean would be 14.5 ("elevated"); weighted is ~10.6.
    assert load["severity_band"] == "moderate"
    assert load["calendar_fact"] == "wide but mild"


def test_a_protective_pattern_is_not_reported_as_a_severity():
    """focus_time_minutes has a NEGATIVE lift: more focus, less strain.

    Banding on abs(lift) would tell a manager to intervene against the thing
    that is helping.
    """
    team = TeamCorrelations(
        n_people_analysed=12,
        date_range=("2026-08-24", "2026-09-20"),
        patterns=[{
            "feature": "focus_time_minutes",
            "n_people_affected": 9,
            "mean_lift_points": -15.0,
            "calendar_fact": "Days leaving 360+ minutes of focus time see lower strain.",
            "severity_band": "elevated",
        }],
    )
    view = roll_up_themes(team)
    frag = next(t for t in view.themes if t["theme_id"] == "fragmentation")
    assert frag["severity_band"] == NO_SIGNAL
    assert frag["protective_fact"] is not None
    assert "lower strain" in frag["protective_fact"]


# ---------------------------------------------------------------------------
# indistinguishability
# ---------------------------------------------------------------------------

def test_two_teams_that_band_identically_produce_identical_views():
    """The test that would have caught the original bug.

    If any per-person value finds a path into the employer view, two teams
    whose bands agree will stop agreeing and this fails.
    """
    a = _team([11], lift=9.0)
    b = _team([12], lift=11.0)   # different counts, different lifts, same bands
    view_a = roll_up_themes(a)
    view_b = roll_up_themes(b)
    assert [t for t in view_a.themes] == [t for t in view_b.themes]


def test_the_gate_runs_before_the_rollup():
    """A theme must never be built from a sub-k pattern."""
    ungated = _team([1, 1, 1], lift=30.0)
    gated = apply_k_anonymity(ungated, k=DEFAULT_K)
    view = roll_up_themes(gated)
    assert all(t["severity_band"] == NO_SIGNAL for t in view.themes), (
        "single-person patterns reached the employer view"
    )
