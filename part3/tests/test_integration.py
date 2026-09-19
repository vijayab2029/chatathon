"""End-to-end tests for the Part 3 pipeline.

The headline guarantee here is test_no_invented_numbers: in offline mode the
narration is templated from validated evidence, so EVERY number in a person's
insight_text must be traceable to that person's own evidence. That is the
mechanical form of "LLM agents propose and explain; Python proves".

ALL DATA USED HERE IS SYNTHETIC.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# --- make part3/src importable regardless of cwd ---------------------------
PART3_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PART3_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
# ---------------------------------------------------------------------------

from insight.loaders import build_timelines, load_meeting_events, load_stress_scores  # noqa: E402
from insight.pipeline import run_pipeline  # noqa: E402

FIXTURES = PART3_DIR / "data" / "fixtures"
STRESS_CSV = FIXTURES / "stress_scores.csv"
MEETINGS_JSON = FIXTURES / "meeting_features.json"

# Same shape as the critic's number scanner: standalone numbers only, so
# "1:1", "b2b" and "08:00" style fragments inside words do not trip us up.
_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z_.])(\d+(?:\.\d+)?)(?![0-9A-Za-z_])")
_TOLERANCE = 0.051


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("integration_out")
    summary = run_pipeline(out_dir=out_dir, offline=True, limit=6)
    return {
        "summary": summary,
        "out_dir": out_dir,
        "employee": json.loads((out_dir / "employee_insight.json").read_text(encoding="utf-8")),
        "team": json.loads((out_dir / "team_correlations.json").read_text(encoding="utf-8")),
    }


# ---------------------------------------------------------------------------
# 1. the run produces both files, valid and non-empty
# ---------------------------------------------------------------------------

def test_pipeline_writes_both_files(offline_run):
    out_dir = offline_run["out_dir"]
    employee_path = out_dir / "employee_insight.json"
    team_path = out_dir / "team_correlations.json"

    for path in (employee_path, team_path):
        assert path.is_file(), f"{path.name} was not written"
        assert path.stat().st_size > 0, f"{path.name} is empty"
        json.loads(path.read_text(encoding="utf-8"))  # valid JSON or raises

    employee = offline_run["employee"]
    assert len(employee["people"]) == 6
    for person_id, record in employee["people"].items():
        assert record["person_id"] == person_id
        assert record["stress_trend"], f"{person_id} has no stress trend"
        assert record["insight_text"].strip(), f"{person_id} has no insight text"
        assert record["suggested_action"].strip(), f"{person_id} has no suggested action"
        assert record["average_stress"] > 0

    team = offline_run["team"]
    assert team["patterns"], "team_correlations.json has no patterns"
    assert team["n_people_analysed"] == 6

    summary = offline_run["summary"]
    assert summary["n_people"] == 6
    assert summary["n_patterns_found"] == len(team["patterns"])
    assert summary["data_provenance"] == "SIMULATED"


# ---------------------------------------------------------------------------
# 2. the LLM cannot invent numbers
# ---------------------------------------------------------------------------

def _numbers_in(text: str) -> list[float]:
    return [float(match) for match in _NUMBER_RE.findall(text)]


def _allowed_numbers_for(record: dict) -> set[float]:
    """Every number this person's narration is permitted to cite.

    Numeric leaves AND numbers embedded in evidence strings (the pattern
    description carries the threshold and the lag), plus their own average
    stress in both its rounded forms.
    """
    allowed: set[float] = set()

    def collect(node) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, (int, float)):
            allowed.add(float(node))
            allowed.add(abs(float(node)))
        elif isinstance(node, str):
            allowed.update(_numbers_in(node))
        elif isinstance(node, dict):
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(record["top_patterns"])

    average = float(record["average_stress"])
    allowed.update({average, round(average, 1), float(round(average))})
    return allowed


def _is_supported(value: float, allowed: set[float]) -> bool:
    for candidate in allowed:
        if abs(candidate - value) <= _TOLERANCE:
            return True
        if round(candidate, 1) == round(value, 1) or float(round(candidate)) == value:
            return True
    return False


def test_no_invented_numbers_in_insight_text(offline_run):
    """Every number cited to a person must come from that person's evidence."""
    failures: list[str] = []

    for person_id, record in offline_run["employee"]["people"].items():
        allowed = _allowed_numbers_for(record)
        for value in _numbers_in(record["insight_text"]):
            if not _is_supported(value, allowed):
                failures.append(
                    f"{person_id}: '{value:g}' in insight_text is not in "
                    f"top_patterns evidence nor the average stress "
                    f"({record['average_stress']})"
                )

    assert not failures, "invented numbers reached the employee insight:\n" + "\n".join(failures)


def test_insight_text_actually_cites_evidence(offline_run):
    """Guard against the test above passing vacuously on number-free text."""
    with_numbers = [
        pid for pid, rec in offline_run["employee"]["people"].items()
        if _numbers_in(rec["insight_text"])
    ]
    assert with_numbers, "no insight_text contained any number -- the check was vacuous"


def test_critic_never_flags_an_unsupported_number_offline(offline_run):
    """A4 runs deterministically with no API key. If it flags an unsupported
    number in our OWN templates, the "Python proves" chain is broken.

    Note: A4 also currently raises NO_DIAGNOSIS on the template's own
    disclaimer ("...not a diagnosis") because 'diagnos' is a substring match.
    That is a known false positive in agent_critic.py, not a numbers problem,
    so this test asserts only on the numbers rule.
    """
    for person_id, record in offline_run["employee"]["people"].items():
        for verdict in record["critic_log"]:
            unsupported = [
                issue for issue in verdict["issues"]
                if "NO_UNSUPPORTED_NUMBERS" in issue
            ]
            assert not unsupported, f"{person_id}: {unsupported}"


# ---------------------------------------------------------------------------
# 3. offline means zero LLM calls
# ---------------------------------------------------------------------------

def test_offline_run_makes_zero_llm_calls(offline_run):
    summary = offline_run["summary"]
    assert summary["llm_calls"] == 0
    assert summary["llm_enabled"] is False
    # and the insight is still real, not a placeholder
    assert all(not rec["llm_used"] for rec in offline_run["employee"]["people"].values())


# ---------------------------------------------------------------------------
# 4. malformed upstream input degrades, never explodes
# ---------------------------------------------------------------------------

MALFORMED_PAYLOADS = {
    "bare_list": [
        {"person_id": "emp_001", "date": "2026-08-24"},          # missing everything else
        {"person_id": "emp_001"},                                 # no date
        {"date": "2026-08-25"},                                   # no person
        "not_an_object",                                          # not a dict at all
        12345,
        None,
    ],
    "empty_list": [],
    "dict_without_events_key": {"synthetic": True, "note": "Part 2 changed the shape"},
    "events_is_not_a_list": {"events": {"emp_001": ["oops"]}},
    "events_with_junk_dates": {"events": [
        {"person_id": "emp_001", "date": "not-a-date", "start": "09:00"},
        {"person_id": "emp_001", "date": "2026-08-24", "start": None, "end": None,
         "attendee_count": "many", "sentiment": "very bad"},
    ]},
    "alternate_key_names": {"meetings": [
        {"employee_id": "emp_001", "start_date": "2026-08-24",
         "start_time": "09:00", "end_time": "09:30", "summary": "Standup",
         "attendees": 4, "topic": "status"},
    ]},
}


@pytest.mark.parametrize("name", sorted(MALFORMED_PAYLOADS))
def test_malformed_meeting_features_does_not_raise(tmp_path, name):
    path = tmp_path / "meeting_features.json"
    path.write_text(json.dumps(MALFORMED_PAYLOADS[name]), encoding="utf-8")

    events = load_meeting_events(path)  # must not raise
    assert isinstance(events, list)

    # the join must still work, falling back to stress-only timelines
    timelines = build_timelines(load_stress_scores(STRESS_CSV), events)
    assert timelines, f"{name}: lost every timeline"


def test_pipeline_survives_a_malformed_meetings_file(tmp_path):
    """Part 2 shipping a broken file must not take the demo down."""
    meetings = tmp_path / "meeting_features.json"
    meetings.write_text(json.dumps(MALFORMED_PAYLOADS["bare_list"]), encoding="utf-8")
    out_dir = tmp_path / "out"

    summary = run_pipeline(
        stress_csv=STRESS_CSV,
        meetings_json=meetings,
        out_dir=out_dir,
        offline=True,
        limit=3,
    )

    assert summary["n_people"] == 3
    assert summary["llm_calls"] == 0
    employee = json.loads((out_dir / "employee_insight.json").read_text(encoding="utf-8"))
    assert len(employee["people"]) == 3
    # no calendar features -> nothing provable, but still an honest record
    for record in employee["people"].values():
        assert record["insight_text"].strip()
        assert record["data_provenance"] == "SIMULATED"


def test_real_fixtures_load(tmp_path):
    """Sanity check that the committed fixtures still match the contract."""
    stress = load_stress_scores(STRESS_CSV)
    events = load_meeting_events(MEETINGS_JSON)
    assert stress and events
    timelines = build_timelines(stress, events)
    assert timelines
    some = next(iter(timelines.values()))
    assert some.days and some.days[0].features
