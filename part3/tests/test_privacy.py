"""Privacy guarantees for Part 3.

THIS IS THE MOST IMPORTANT TEST FILE IN PART 3. It guards the central design
commitment: the team-facing file carries pattern-level signal and NOTHING that
identifies a person, and Part 3 deliberately does NOT gate -- Part 4 owns the
k-anonymity policy.

ALL DATA USED HERE IS SYNTHETIC.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# --- make part3/src importable regardless of cwd ---------------------------
PART3_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PART3_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
# ---------------------------------------------------------------------------

from insight.emit import aggregate_team_patterns  # noqa: E402
from insight.models import Hypothesis, ValidatedPattern  # noqa: E402
from insight.pipeline import run_pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _walk(node, path="$"):
    """Yield (json_path, key_or_None, value) for every node in a JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            yield here, key, value
            yield from _walk(value, here)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            here = f"{path}[{index}]"
            yield here, None, value
            yield from _walk(value, here)


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory):
    """One offline pipeline run shared by every test in this module."""
    out_dir = tmp_path_factory.mktemp("privacy_out")
    summary = run_pipeline(out_dir=out_dir, offline=True, limit=6)
    team_path = out_dir / "team_correlations.json"
    employee_path = out_dir / "employee_insight.json"
    return {
        "summary": summary,
        "out_dir": out_dir,
        "team_text": team_path.read_text(encoding="utf-8"),
        "team": json.loads(team_path.read_text(encoding="utf-8")),
        "employee": json.loads(employee_path.read_text(encoding="utf-8")),
    }


# ---------------------------------------------------------------------------
# 1. team_correlations.json contains no person anywhere
# ---------------------------------------------------------------------------

def test_team_file_text_contains_no_person_id(offline_run):
    """The serialized bytes must not mention a person, at any depth."""
    raw = offline_run["team_text"]
    assert "emp_" not in raw, "team_correlations.json leaks a person id (found 'emp_')"
    assert "person_id" not in raw, "team_correlations.json carries a person_id key"


def test_team_file_structure_contains_no_person_id(offline_run):
    """Walk the parsed structure: no person_id key, no person id value."""
    offenders_keys: list[str] = []
    offenders_values: list[str] = []

    for path, key, value in _walk(offline_run["team"]):
        if key is not None and "person_id" in str(key).lower():
            offenders_keys.append(path)
        if isinstance(value, str) and "emp_" in value:
            offenders_values.append(f"{path} = {value!r}")

    assert not offenders_keys, f"person_id key present at: {offenders_keys}"
    assert not offenders_values, f"person id leaked in a value at: {offenders_values}"


def test_team_file_is_not_a_concatenation_of_employee_insights(offline_run):
    """The team file must be an aggregate, not the per-person narratives glued
    together -- concatenation would let a manager re-identify people from
    calendar specifics."""
    team_text = offline_run["team_text"]
    for record in offline_run["employee"]["people"].values():
        assert record["insight_text"] not in team_text
        assert record["suggested_action"] not in team_text


# ---------------------------------------------------------------------------
# 2. Part 3 does not gate -- Part 4 owns k-anonymity
# ---------------------------------------------------------------------------

def test_part3_applies_no_k_anonymity_gate(offline_run):
    """Contract boundary: Part 3 hands Part 4 honest counts, INCLUDING counts
    below the k>=5 floor. Part 4 owns the policy. If this flips to true,
    analytics has started making policy decisions and the split is broken.
    """
    team = offline_run["team"]
    assert team["gating_applied"] is False
    assert "gating_note" in team and "Part 4" in team["gating_note"]


def test_counts_below_the_k_floor_are_still_reported(offline_run):
    """Nothing may be suppressed here. Every pattern is emitted with its real
    count, even when n_people_affected < 5."""
    team = offline_run["team"]
    assert team["patterns"], "expected at least one aggregated pattern"
    for pattern in team["patterns"]:
        assert pattern["n_people_affected"] >= 1
        assert pattern["n_people_affected"] <= pattern["n_people_analysed"]


# ---------------------------------------------------------------------------
# 3. honesty: every record is labelled simulated
# ---------------------------------------------------------------------------

def test_every_person_is_labelled_simulated(offline_run):
    doc = offline_run["employee"]
    assert doc["synthetic"] is True
    assert doc["data_provenance"] == "SIMULATED"

    people = doc["people"]
    assert people, "expected at least one person in employee_insight.json"
    for person_id, record in people.items():
        assert record["data_provenance"] == "SIMULATED", person_id
        assert record["synthetic"] is True, person_id


def test_team_file_is_labelled_simulated(offline_run):
    team = offline_run["team"]
    assert team["data_provenance"] == "SIMULATED"
    assert team["synthetic"] is True


# ---------------------------------------------------------------------------
# 4. aggregation counts DISTINCT people
# ---------------------------------------------------------------------------

def _pattern(feature: str, lift: float, *, threshold: float = 2, lag: int = 1,
             passed: bool = True) -> ValidatedPattern:
    """A fabricated ValidatedPattern, for aggregation arithmetic only."""
    return ValidatedPattern(
        hypothesis=Hypothesis(
            id=f"h_{feature}_{threshold:g}_{lag}",
            feature=feature,
            operator=">=",
            threshold=threshold,
            lag_days=lag,
            rationale="fabricated for a unit test",
        ),
        n_exposed=5,
        n_unexposed=10,
        mean_exposed=50.0 + lift,
        mean_unexposed=50.0,
        lift=lift,
        pearson_r=0.4,
        p_value=0.01,
        passed=passed,
    )


def test_n_people_affected_counts_distinct_people_not_rows():
    """The same pattern twice for one person is ONE affected person.

    Counting rows instead of people would inflate every employer-facing number
    and, worse, make a single heavily-scheduled person look like a team trend.
    """
    per_person = {
        # emp_001 hits the same pattern twice (e.g. two surviving variants)
        "emp_001": [
            _pattern("back_to_back_blocks", 10.0),
            _pattern("back_to_back_blocks", 14.0),
        ],
        "emp_002": [_pattern("back_to_back_blocks", 6.0)],
    }

    team = aggregate_team_patterns(per_person, n_people_analysed=2,
                                   date_range=("2026-08-24", "2026-09-18"))

    assert len(team.patterns) == 1
    pattern = team.patterns[0]
    assert pattern["n_people_affected"] == 2, "counted rows (3) instead of people (2)"
    # strongest lift per person is kept: mean over {14.0, 6.0}
    assert pattern["mean_lift_points"] == pytest.approx(10.0)
    assert pattern["max_lift_points"] == pytest.approx(14.0)


def test_failed_patterns_never_reach_the_team_file():
    per_person = {
        "emp_001": [_pattern("meeting_count", 9.0, passed=False)],
        "emp_002": [_pattern("meeting_count", 9.0, passed=True)],
    }
    team = aggregate_team_patterns(per_person, n_people_analysed=2, date_range=("", ""))
    assert len(team.patterns) == 1
    assert team.patterns[0]["n_people_affected"] == 1


def test_aggregate_output_has_no_person_id_even_when_input_does():
    """Person ids go IN (as dict keys) and must not come out."""
    per_person = {
        "emp_001": [_pattern("after_hours_meetings", 8.0, threshold=1)],
        "emp_002": [_pattern("after_hours_meetings", 4.0, threshold=1)],
        "emp_003": [_pattern("no_agenda_meetings", 5.0)],
    }
    team = aggregate_team_patterns(per_person, n_people_analysed=3, date_range=("", ""))

    from dataclasses import asdict

    raw = json.dumps(asdict(team))
    assert "emp_" not in raw
    assert "person_id" not in raw
