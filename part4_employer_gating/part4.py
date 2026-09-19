"""
Part 4 — Privacy-Gated Employer Aggregation Layer
==================================================

Design principles this module enforces (see project design plan, section 2):
  1. Employer view is NEVER a per-person score or name.
  2. k-anonymity floor: below MIN_TEAM_SIZE, employer sees nothing.
  3. Causes attach to the calendar/schedule structure, not to the person.
  4. Output recommends a PROCESS (e.g. "run a workload check-in"),
     never a diagnosis of a specific person.
  5. Employees can opt in to share their own specifics upward. The system
     never infers consent from the aggregate.

INPUTS THIS MODULE READS:

  Real Part 2 output (team-level, already privacy-audited, non-personal):
    ../data/team_structural_summary.json
      -> { team_size, metrics: { avg_daily_meetings_per_person,
           pct_meetings_back_to_back, pct_days_without_lunch_buffer,
           pct_meetings_without_agenda, pct_meetings_after_hours, ... } }

  Still-mocked upstream data (waiting on Part 1 / roster decision):
    mock_inputs/stress_scores.csv     : person_id, date, stress_score, contributing_factors
    mock_inputs/employees.json        : { employees: [{ person_id, opted_in }] }
    mock_inputs/employee_insight.json : { person_id: "private insight text" }  (Part 3 output)

OUTPUT CONTRACT (-> Part 5, the UI):
  employer_view.json : {
    "gate_passed": bool,
    "team_size": int,
    "min_team_size": int,
    "categories": [ { "category": str, "severity": "low"|"moderate"|"high" } ],
    "recommended_actions": [ str ],
    "opted_in_specifics": [ { "person_id": str, "insight": str } ]   # empty unless opted in
  }

SWAP-IN NOTE FOR TEAMMATES:
  Once Part 1 produces a real stress_scores.csv, and the team decides where
  opted_in roster data actually lives, point STRESS_SCORES_PATH / EMPLOYEES_PATH
  / EMPLOYEE_INSIGHT_PATH at the real files. Nothing else needs to change.
"""

import csv
import json
import os

_HERE = os.path.dirname(__file__)
MOCK_INPUT_DIR = os.path.join(_HERE, "mock_inputs")
DATA_DIR = os.path.join(_HERE, "..", "data")  # Part 2's real output lives here
OUTPUT_PATH = os.path.join(_HERE, "employer_view.json")

MIN_TEAM_SIZE = 5  # k-anonymity floor — hardcoded on purpose, do not make configurable for the demo


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_stress_scores(path=None):
    path = path or os.path.join(MOCK_INPUT_DIR, "stress_scores.csv")
    scores = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            scores[row["person_id"]] = {
                "stress_score": int(row["stress_score"]),
                "contributing_factors": row["contributing_factors"].split(";"),
            }
    return scores


def load_team_structural_summary(path=None):
    """Reads Part 2's real, already-aggregated, privacy-audited team summary."""
    path = path or os.path.join(DATA_DIR, "team_structural_summary.json")
    with open(path) as f:
        return json.load(f)


def load_employee_insight(path=None):
    path = path or os.path.join(MOCK_INPUT_DIR, "employee_insight.json")
    with open(path) as f:
        return json.load(f)


def load_employees(path=None):
    path = path or os.path.join(MOCK_INPUT_DIR, "employees.json")
    with open(path) as f:
        return json.load(f)["employees"]


# ---------------------------------------------------------------------------
# k-anonymity gate
# ---------------------------------------------------------------------------

def k_anonymity_gate(team_size, min_team_size=MIN_TEAM_SIZE):
    """Returns True if the employer view is allowed to render at all.

    NOTE: Part 2's team_structural_summary.json includes its own
    privacy_audit.k_anonymity_status claim. We deliberately do NOT trust
    that claim here — Part 4 is the enforcement layer, so it always
    re-checks against the actual employee roster.
    """
    return team_size >= min_team_size


# ---------------------------------------------------------------------------
# Category aggregation.
# Part 2 already did the hard part (aggregating raw calendar events into
# non-personal team-wide percentages). Part 4's job here is just banding
# those into low/moderate/high severity for the employer view.
# ---------------------------------------------------------------------------

def _band(value, low_max, moderate_max):
    """Generic 3-band bucketer: low / moderate / high."""
    if value <= low_max:
        return "low"
    if value <= moderate_max:
        return "moderate"
    return "high"


def aggregate_team_categories(team_summary, stress_scores):
    """
    Produces team-level causal categories + severity bands from Part 2's
    real aggregate metrics, plus a team-stress band from Part 1/mocked
    stress scores. Never returns a per-person score or name.
    """
    m = team_summary["metrics"]

    n_stressed = sum(1 for s in stress_scores.values() if s["stress_score"] >= 70)
    n_total = len(stress_scores) or 1
    high_stress_rate = n_stressed / n_total

    categories = [
        {"category": "meeting_density", "severity": _band(m["avg_daily_meetings_per_person"], 3, 5)},
        {"category": "back_to_back_density", "severity": _band(m["pct_meetings_back_to_back"], 20, 40)},
        {"category": "no_agenda_rate", "severity": _band(m["pct_meetings_without_agenda"], 20, 40)},
        {"category": "after_hours_load", "severity": _band(m["pct_meetings_after_hours"], 5, 15)},
        {"category": "lunch_buffer_risk", "severity": _band(m["pct_days_without_lunch_buffer"], 15, 35)},
        {"category": "team_stress_level", "severity": _band(high_stress_rate, 0.2, 0.4)},
    ]
    return categories


# ---------------------------------------------------------------------------
# Recommend PROCESS, not people.
# ---------------------------------------------------------------------------

_ACTION_LIBRARY = {
    "meeting_density": {
        "moderate": "Review whether recurring meetings this week can be shortened or made async.",
        "high": "Run a meeting-load audit this week — several recurring meetings may be reducible.",
    },
    "back_to_back_density": {
        "moderate": "Suggest adding buffer time between back-to-back blocks where possible.",
        "high": "Run a workload check-in this week — back-to-back scheduling is elevated team-wide.",
    },
    "no_agenda_rate": {
        "moderate": "Encourage agenda-required norms for recurring meetings.",
        "high": "Require agendas for recurring meetings before next sprint — a majority currently lack one.",
    },
    "after_hours_load": {
        "moderate": "Check whether after-hours meetings are avoidable for most attendees.",
        "high": "Flag after-hours meeting load with team leads and consider a cutoff policy.",
    },
    "lunch_buffer_risk": {
        "moderate": "Consider protecting a midday buffer window on the team calendar.",
        "high": "Protect the midday window (e.g. 11:30 AM-2 PM) team-wide — most days currently have no break.",
    },
    "team_stress_level": {
        "moderate": "Consider a general team check-in on workload this week.",
        "high": "Run a workload rebalancing check-in this week — do not target individuals; discuss as a team norm.",
    },
}


def recommend_actions(categories):
    actions = []
    for c in categories:
        if c["severity"] in ("moderate", "high"):
            action = _ACTION_LIBRARY.get(c["category"], {}).get(c["severity"])
            if action:
                actions.append(action)
    return actions


# ---------------------------------------------------------------------------
# Opt-in escalation: only surfaces specifics for people who explicitly opted in.
# Consent is never inferred from the aggregate.
# ---------------------------------------------------------------------------

def opted_in_specifics(employees, employee_insight):
    return [
        {"person_id": e["person_id"], "insight": employee_insight[e["person_id"]]}
        for e in employees
        if e.get("opted_in") and e["person_id"] in employee_insight
    ]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_employer_view(
    team_summary_path=None,
    stress_scores_path=None,
    employees_path=None,
    employee_insight_path=None,
):
    team_summary = load_team_structural_summary(team_summary_path)
    stress_scores = load_stress_scores(stress_scores_path)
    employees = load_employees(employees_path)
    employee_insight = load_employee_insight(employee_insight_path)

    team_size = len(employees)
    gate_passed = k_anonymity_gate(team_size)

    if not gate_passed:
        return {
            "gate_passed": False,
            "team_size": team_size,
            "min_team_size": MIN_TEAM_SIZE,
            "categories": [],
            "recommended_actions": [],
            "opted_in_specifics": [],
        }

    categories = aggregate_team_categories(team_summary, stress_scores)
    actions = recommend_actions(categories)
    specifics = opted_in_specifics(employees, employee_insight)

    return {
        "gate_passed": True,
        "team_size": team_size,
        "min_team_size": MIN_TEAM_SIZE,
        "categories": categories,
        "recommended_actions": actions,
        "opted_in_specifics": specifics,
    }


if __name__ == "__main__":
    view = build_employer_view()
    with open(OUTPUT_PATH, "w") as f:
        json.dump(view, f, indent=2)
    print(json.dumps(view, indent=2))
    print(f"\nWrote {OUTPUT_PATH}")