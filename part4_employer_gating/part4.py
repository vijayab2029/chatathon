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

INTERFACE CONTRACT (from the team's shared interfaces table — do not change
field names without updating every module):

  stress_scores.csv        <- Part 1   : person_id, date, stress_score, contributing_factors
  meeting_features.json    <- Part 2   : { person_id: { date, meetings: [...] } }
  team_structural_summary.json <- Part 2 : team-level, non-personal aggregate stats
  employee_insight.json    <- Part 3   : { person_id: "private insight text" }
  employees.json           <- roster   : [{ person_id, opted_in }]

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
  Everything in this file reads from ./mock_inputs/*. Once Part 1 and Part 2
  produce real files with the SAME field names, point INPUT_DIR at their
  output folder and nothing else in this file needs to change.
"""

import csv
import json
import os

INPUT_DIR = os.path.join(os.path.dirname(__file__), "mock_inputs")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "employer_view.json")

MIN_TEAM_SIZE = 5  # k-anonymity floor — hardcoded on purpose, do not make configurable for the demo


# ---------------------------------------------------------------------------
# Loading (Parts 1-3 + roster). Swap INPUT_DIR to point at real outputs later.
# ---------------------------------------------------------------------------

def load_stress_scores(path=None):
    path = path or os.path.join(INPUT_DIR, "stress_scores.csv")
    scores = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            scores[row["person_id"]] = {
                "stress_score": int(row["stress_score"]),
                "contributing_factors": row["contributing_factors"].split(";"),
            }
    return scores


def load_meeting_features(path=None):
    path = path or os.path.join(INPUT_DIR, "meeting_features.json")
    with open(path) as f:
        return json.load(f)


def load_team_structural_summary(path=None):
    path = path or os.path.join(INPUT_DIR, "team_structural_summary.json")
    with open(path) as f:
        return json.load(f)


def load_employee_insight(path=None):
    path = path or os.path.join(INPUT_DIR, "employee_insight.json")
    with open(path) as f:
        return json.load(f)


def load_employees(path=None):
    path = path or os.path.join(INPUT_DIR, "employees.json")
    with open(path) as f:
        return json.load(f)["employees"]


# ---------------------------------------------------------------------------
# k-anonymity gate
# ---------------------------------------------------------------------------

def k_anonymity_gate(team_size, min_team_size=MIN_TEAM_SIZE):
    """Returns True if the employer view is allowed to render at all."""
    return team_size >= min_team_size


# ---------------------------------------------------------------------------
# Per-person derived meeting features -> team-level severity bands.
# This is the heart of "causes attach to the calendar, not the person":
# we compute per-person numbers ONLY as an intermediate step, then immediately
# collapse them into a team-wide band. No per-person number ever leaves this
# function.
# ---------------------------------------------------------------------------

def _band(value, low_max, moderate_max):
    """Generic 3-band bucketer: low / moderate / high."""
    if value <= low_max:
        return "low"
    if value <= moderate_max:
        return "moderate"
    return "high"


def _back_to_back_count(meetings):
    """Count meetings that start within 5 min of the previous one ending."""
    sorted_meetings = sorted(meetings, key=lambda m: m["start"])
    count = 0
    prev_end = None
    for m in sorted_meetings:
        h, mm = map(int, m["start"].split(":"))
        start_min = h * 60 + mm
        if prev_end is not None and start_min - prev_end <= 5:
            count += 1
        prev_end = start_min + m["duration_min"]
    return count


def aggregate_team_categories(meeting_features, stress_scores):
    """
    Produces team-level causal categories + severity bands.
    Deliberately never returns a per-person score or name.
    """
    n = len(meeting_features)
    total_meetings = 0
    total_no_agenda = 0
    total_after_hours = 0
    total_back_to_back = 0
    high_stress_people = 0

    for person_id, data in meeting_features.items():
        meetings = data["meetings"]
        total_meetings += len(meetings)
        total_no_agenda += sum(1 for m in meetings if not m["has_agenda"])
        total_after_hours += sum(
            1 for m in meetings if int(m["start"].split(":")[0]) >= 18
        )
        total_back_to_back += _back_to_back_count(meetings)
        if stress_scores.get(person_id, {}).get("stress_score", 0) >= 70:
            high_stress_people += 1

    avg_meetings = total_meetings / n
    no_agenda_rate = total_no_agenda / total_meetings if total_meetings else 0
    after_hours_rate = total_after_hours / total_meetings if total_meetings else 0
    avg_back_to_back = total_back_to_back / n
    high_stress_rate = high_stress_people / n  # fraction, never a headcount below the k-floor

    categories = [
        {"category": "meeting_density", "severity": _band(avg_meetings, 3, 5)},
        {"category": "no_agenda_rate", "severity": _band(no_agenda_rate, 0.3, 0.6)},
        {"category": "after_hours_load", "severity": _band(after_hours_rate, 0.05, 0.15)},
        {"category": "back_to_back_density", "severity": _band(avg_back_to_back, 1, 2)},
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
    "no_agenda_rate": {
        "moderate": "Encourage agenda-required norms for recurring meetings.",
        "high": "Require agendas for recurring meetings before next sprint — a majority currently lack one.",
    },
    "after_hours_load": {
        "moderate": "Check whether after-hours meetings are avoidable for most attendees.",
        "high": "Flag after-hours meeting load with team leads and consider a cutoff policy.",
    },
    "back_to_back_density": {
        "moderate": "Suggest adding buffer time between back-to-back blocks where possible.",
        "high": "Run a workload check-in this week — back-to-back scheduling is elevated team-wide.",
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
# Consent is never inferred from the aggregate existing.
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

def build_employer_view(input_dir=None):
    dir_ = input_dir or INPUT_DIR
    stress_scores = load_stress_scores(os.path.join(dir_, "stress_scores.csv"))
    meeting_features = load_meeting_features(os.path.join(dir_, "meeting_features.json"))
    employees = load_employees(os.path.join(dir_, "employees.json"))
    employee_insight = load_employee_insight(os.path.join(dir_, "employee_insight.json"))

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

    categories = aggregate_team_categories(meeting_features, stress_scores)
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