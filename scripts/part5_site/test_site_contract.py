"""Contract test: does data/*.json carry what ui/app.js actually dereferences?

The site and the pipeline are owned by different people and connected only by
two JSON files. Nothing else in the repo fails when they drift -- the page
just silently renders `undefined`. This test is the joint.

    python -m pytest scripts/part5_site/test_site_contract.py

Run `python scripts/part5_site/build_site_data.py` first; these assertions
read the committed artifacts, not a fresh build, because what ships is what
the demo serves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EMPLOYEE = REPO / "data" / "employee_insight.json"
EMPLOYER = REPO / "data" / "employer_view.json"


@pytest.fixture(scope="module")
def employee() -> dict:
    return json.loads(EMPLOYEE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def employer() -> dict:
    return json.loads(EMPLOYER.read_text(encoding="utf-8"))


# --- employee view: every field renderEmployee() touches -------------------

def test_employee_header_fields(employee):
    for key in ("display_name", "person_id", "generated_at"):
        assert employee[key], f"ui/app.js prints {key} in the hero card"


def test_employee_current_block(employee):
    cur = employee["current"]
    assert isinstance(cur["stress_score"], int)
    # SEVERITY in ui/app.js keys its icons and colours off exactly these.
    assert cur["band"] in {"Low", "Moderate", "Elevated", "High"}
    assert cur["delta_vs_baseline"] == cur["stress_score"] - cur["baseline_14d"]


def test_biometric_tiles_render(employee):
    tiles = employee["biometrics_today"]
    assert tiles, "the KPI row would be empty"
    for t in tiles:
        assert {"label", "value", "unit", "baseline", "direction"} <= t.keys()


def test_trend_matches_the_hardcoded_14_day_copy(employee):
    trend = employee["trend"]
    assert len(trend) == 14, "ui/index.html says 'What your 14 days look like'"
    for point in trend:
        # drawTrend() shades weekends off this exact spelling.
        assert point["weekday"] in {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
        assert isinstance(point["stress_score"], int)
        assert isinstance(point["meetings"], int)
    assert [p["date"] for p in trend] == sorted(p["date"] for p in trend)


def test_weekends_carry_no_meetings(employee):
    """A weekend with meetings would mean the calendar join slipped a day."""
    for p in employee["trend"]:
        if p["weekday"] in {"Sat", "Sun"}:
            assert p["meetings"] == 0


def test_factor_chart_is_a_whole(employee):
    factors = employee["contributing_factors"]
    assert factors
    assert sum(f["weight_pct"] for f in factors) == 100
    for f in factors:
        assert {"factor", "weight_pct", "note"} <= f.keys()


def test_insight_card_is_complete(employee):
    ins = employee["insight"]
    for key in ("headline", "detail", "confidence", "confidence_note"):
        assert ins[key], f"the insight card renders {key}"
    assert ins["evidence"], "the evidence list would render empty"


def test_optin_lists_both_sides(employee):
    opt = employee["opt_in"]
    assert opt["enabled"] is False, "consent is never on by default"
    assert opt["shares_if_enabled"] and opt["never_shares"]


def test_insight_headline_is_human_readable(employee):
    """Part 3's raw pattern strings must not reach the page."""
    headline = employee["insight"]["headline"]
    assert ">=" not in headline and "_" not in headline


# --- employer view: every field renderEmployer() touches -------------------

def test_employer_blocks_render(employer):
    assert {"threshold", "team_size", "satisfied"} <= employer["k_anonymity"].keys()
    assert {"hero", "tiles"} <= employer["structural_summary"].keys()
    assert {"days", "blocks", "grid"} <= employer["meeting_density"].keys()
    assert employer["causal_categories"]
    assert employer["never_shown"]
    assert {"count", "min_reportable", "reportable"} <= employer["opt_in_shares"].keys()


def test_heatmap_grid_is_rectangular(employer):
    d = employer["meeting_density"]
    assert len(d["grid"]) == len(d["blocks"])
    for row in d["grid"]:
        assert len(row) == len(d["days"])


# --- the wall between the two views ----------------------------------------

def test_employer_view_carries_no_person(employee, employer):
    """The whole argument of the demo: no per-person anything crosses over."""
    blob = json.dumps(employer)
    assert employee["person_id"] not in blob
    assert "person_id" not in blob
    assert str(employee["current"]["stress_score"]) not in [
        str(t["value"]) for t in employer["structural_summary"]["tiles"]
    ]


def test_both_views_declare_themselves_simulated(employee, employer):
    assert employee["synthetic"] is True
    assert employer["synthetic"] is True


def test_the_two_views_agree_on_the_cohort(employer):
    """Guards against the site being wired back to Part 3's 12-person fixtures."""
    people = {
        r["person_id"]
        for r in json.loads((REPO / "data" / "meeting_features.json").read_text(encoding="utf-8"))
    }
    assert employer["k_anonymity"]["team_size"] == len(people)
