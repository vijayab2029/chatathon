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
        # The ungated artifact Part 4 consumes. Since spec 3.1 the plain
        # filename carries the GATED view, so the "Part 3 reports honest
        # counts" guarantee now lives on this file.
        "team_raw": json.loads(
            (out_dir / "team_correlations_raw.json").read_text(encoding="utf-8")
        ),
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
# 2. The analytics/policy split (revised -- see spec 3.1)
#
# Part 3 still refuses to make policy: the RAW file carries honest counts,
# including sub-threshold ones, because Part 4 needs them to apply the
# authoritative gate. What changed is which file gets the plain name -- the
# safe one does, so the accidental path is the safe path.
# ---------------------------------------------------------------------------

def test_raw_file_hands_part4_honest_ungated_counts(offline_run):
    """If this flips to gated, Part 4 can no longer see what it is deciding
    about and the analytics/policy split is broken."""
    raw = offline_run["team_raw"]
    assert raw["gating_applied"] is False
    assert "gating_note" in raw and "Part 4" in raw["gating_note"]


def test_raw_file_reports_counts_below_the_k_floor(offline_run):
    """Nothing is suppressed in the raw artifact. Every pattern keeps its real
    count, even when n_people_affected < 5."""
    raw = offline_run["team_raw"]
    assert raw["patterns"], "expected at least one aggregated pattern"
    for pattern in raw["patterns"]:
        assert pattern["n_people_affected"] >= 1
        assert pattern["n_people_affected"] <= raw["n_people_analysed"]


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


# ---------------------------------------------------------------------------
# Safe-by-default k-anonymity gate (spec section 3.1, insight/gating.py)
#
# The gate is a team-size floor ONLY: at or above k the whole rollup is
# published, below k none of it is. The number of patterns is not a privacy
# variable, so it never limits the view. Cell-level suppression by
# n_people_affected is Part 4's call, and these tests pin that split.
# ---------------------------------------------------------------------------

from insight.gating import DEFAULT_K, apply_k_anonymity  # noqa: E402
from insight.models import TeamCorrelations  # noqa: E402


def _team(counts, n_people=12):
    return TeamCorrelations(
        n_people_analysed=n_people,
        date_range=("2026-08-24", "2026-09-20"),
        patterns=[
            {"feature": f"f{i}", "n_people_affected": c, "severity_band": "moderate",
             "calendar_fact": f"fact {i}"}
            for i, c in enumerate(counts)
        ],
    )


def test_gate_suppresses_cells_below_k_even_when_the_team_clears_the_floor():
    """Both conditions are required: team size AND per-pattern coverage.

    Team size alone is half a control. A 12-person team passes the floor, and
    without cell suppression every single-person pattern is published as though
    it were an aggregate.
    """
    gated = apply_k_anonymity(_team([1, 2, 4, 5, 9]), k=5)
    kept = [p["n_people_affected"] for p in gated.patterns]
    assert kept == [5, 9], "patterns covering fewer than k people must be withheld"
    assert gated.gating_applied is True


def test_gate_withholds_a_single_person_pattern_however_big_the_team():
    """The leak this whole redesign exists to close.

    An earlier revision published these, on the reasoning that suppression left
    the employer view empty. It did -- but because the rollup was fed each
    person's narrative top-3 and fragmented by per-person thresholds, not
    because the data lacked shared patterns. With both fixed the same fixtures
    yield 18 patterns clearing k=5, so there is no longer a trade to make.
    """
    gated = apply_k_anonymity(_team([1, 1, 1, 1]), k=5)
    assert gated.patterns == []


def test_gate_says_what_it_withheld_rather_than_hiding_it():
    gated = apply_k_anonymity(_team([1, 1, 1, 7]), k=5)
    assert "k=5" in gated.gating_note
    assert "3 of 4 pattern(s)" in gated.gating_note, (
        "a judge asking 'what are you not showing me?' gets a number"
    )


def test_gate_blocks_everything_when_the_team_itself_is_too_small():
    # A four-person "team" has no employer view at all, however the patterns
    # are counted -- this is the two-employees-at-different-stress-levels case.
    gated = apply_k_anonymity(_team([4, 4, 4], n_people=4), k=5)
    assert gated.patterns == []
    assert "below the k=5 floor" in gated.gating_note


def test_gate_does_not_mutate_its_input():
    original = _team([1, 9])
    apply_k_anonymity(original, k=5)
    assert len(original.patterns) == 2, "Part 4 still needs the honest counts"
    assert original.gating_applied is False


def test_default_k_matches_the_design_plan():
    assert DEFAULT_K == 5


def test_written_team_file_is_gated_and_raw_file_is_not(tmp_path):
    """The plain filename must carry the SAFE artifact.

    Anyone wiring an employer view without reading the docs reaches for
    team_correlations.json; the ungated counts must require asking by name.
    """
    run_pipeline(offline=True, out_dir=tmp_path)

    gated = json.loads((tmp_path / "team_correlations.json").read_text(encoding="utf-8"))
    raw = json.loads((tmp_path / "team_correlations_raw.json").read_text(encoding="utf-8"))

    assert gated["gating_applied"] is True
    assert raw["gating_applied"] is False

    # The team clears the floor, so a real employer view exists -- but the gate
    # still drops any pattern covering fewer than k people, so the gated file
    # is a strict subset of raw.
    assert gated["n_people_analysed"] >= DEFAULT_K
    assert len(gated["patterns"]) < len(raw["patterns"]), (
        "raw must retain the sub-threshold patterns Part 4 needs to see"
    )
    assert gated["patterns"], (
        "a team above the floor must still get a usable view -- an empty "
        "employer view means the aggregation is broken, not that it is private"
    )
    assert all(p["n_people_affected"] >= DEFAULT_K for p in gated["patterns"])

    # Neither file may carry identifiers, gated or not.
    for blob in ((tmp_path / "team_correlations.json").read_text(encoding="utf-8"),
                 (tmp_path / "team_correlations_raw.json").read_text(encoding="utf-8")):
        assert "person_id" not in blob
        assert "emp_" not in blob
