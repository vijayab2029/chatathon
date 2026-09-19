"""
Part 4 — Privacy-Gated Employer Aggregation Layer
==================================================

Design principles this module enforces:
  1. Employer view is NEVER a per-person score or name.
  2. k-anonymity floor: any signal touching fewer than MIN_TEAM_SIZE people
     is suppressed — both at the whole-team level and per individual pattern.
  3. Causes attach to the calendar/schedule structure, not to the person.
  4. Output recommends a PROCESS (e.g. "run a workload check-in"),
     never a diagnosis of a specific person.
  5. Part 4 NEVER reads any per-person file. Per Part 3's explicit privacy
     contract, `employee_insight.json` is private to Part 5 and must never
     be touched here — no opt-in escalation exists in this pipeline.

INPUTS THIS MODULE READS:

  ../data/team_structural_summary.json   (Part 2, real, de-identified)
      -> { team_size, metrics: { avg_daily_meetings_per_person, ... } }

  ../part3/data/out/team_correlations.json   (Part 3, real, UNGATED)
      -> { n_people_analysed, patterns: [ { feature, n_people_affected,
           mean_lift_points, severity_band, calendar_fact, ... } ],
           gating_applied: false, ... }
      Part 3 deliberately ships ungated data (including patterns below the
      k-floor) and hands the suppression job to Part 4. This module is
      where `gating_applied` actually becomes true.

OUTPUT CONTRACT (-> Part 5, the UI):
  employer_view.json : {
    "gate_passed": bool,
    "team_size": int,
    "min_team_size": int,
    "categories": [ { "category": str, "severity": "low"|"moderate"|"high" } ],
    "recommended_actions": [ str ],
    "validated_patterns": [
      { "calendar_fact": str, "severity_band": str,
        "n_people_affected": int, "mean_lift_points": float }
    ],
    "gating_applied": true
  }

NOTE ON `team_correlations.json`: Part 3's default output reflects THEIR
synthetic fixtures (12 people), not this team's real 6-person cohort, unless
someone has run:
  cd part3/src && python -m insight.pipeline --offline \
    --stress ../../data/stress_scores.csv --meetings ../../data/meeting_features.json \
    --out ../data/out
Point TEAM_CORRELATIONS_PATH at whatever that command produces.
"""

import json
import os

_HERE = os.path.dirname(__file__)
DATA_DIR = os.path.join(_HERE, "..", "data")                      # Part 2's real output
PART3_OUT_DIR = os.path.join(_HERE, "..", "part3", "data", "out")  # Part 3's real output
OUTPUT_PATH = os.path.join(_HERE, "employer_view.json")

MIN_TEAM_SIZE = 5  # k-anonymity floor — applies to the whole team AND to each individual pattern


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_team_structural_summary(path=None):
    path = path or os.path.join(DATA_DIR, "team_structural_summary.json")
    with open(path) as f:
        return json.load(f)


def load_team_correlations(path=None):
    """Reads Part 3's real, UNGATED team-level correlation output.
    This file may contain patterns below the k-floor by design — Part 3
    hands us honest counts and expects Part 4 to suppress, not Part 3.
    """
    path = path or os.path.join(PART3_OUT_DIR, "team_correlations.json")
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# k-anonymity gate
# ---------------------------------------------------------------------------

def k_anonymity_gate(team_size, min_team_size=MIN_TEAM_SIZE):
    """Whole-team gate: below this, the employer sees nothing at all."""
    return team_size >= min_team_size


def filter_patterns_by_k_anonymity(patterns, min_team_size=MIN_TEAM_SIZE):
    """Per-pattern gate: even in a large-enough team, a pattern that only
    describes a handful of people must be suppressed individually, since
    Part 3 ships patterns with n_people_affected below the floor on purpose.
    """
    return [p for p in patterns if p["n_people_affected"] >= min_team_size]


# ---------------------------------------------------------------------------
# Part 2-derived categories (de-identified schedule-health signals,
# independent of any stress correlation).
# ---------------------------------------------------------------------------

def _band(value, low_max, moderate_max):
    if value <= low_max:
        return "low"
    if value <= moderate_max:
        return "moderate"
    return "high"


def aggregate_team_categories(team_summary):
    m = team_summary["metrics"]
    return [
        {"category": "meeting_density", "severity": _band(m["avg_daily_meetings_per_person"], 3, 5)},
        {"category": "back_to_back_density", "severity": _band(m["pct_meetings_back_to_back"], 20, 40)},
        {"category": "no_agenda_rate", "severity": _band(m["pct_meetings_without_agenda"], 20, 40)},
        {"category": "after_hours_load", "severity": _band(m["pct_meetings_after_hours"], 5, 15)},
        {"category": "lunch_buffer_risk", "severity": _band(m["pct_days_without_lunch_buffer"], 15, 35)},
    ]


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
}


def recommend_actions_from_categories(categories):
    actions = []
    for c in categories:
        if c["severity"] in ("moderate", "high"):
            action = _ACTION_LIBRARY.get(c["category"], {}).get(c["severity"])
            if action:
                actions.append(action)
    return actions


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_employer_view(team_summary_path=None, team_correlations_path=None):
    team_summary = load_team_structural_summary(team_summary_path)
    correlations = load_team_correlations(team_correlations_path)

    team_size = correlations["n_people_analysed"]
    gate_passed = k_anonymity_gate(team_size)

    if not gate_passed:
        return {
            "gate_passed": False,
            "team_size": team_size,
            "min_team_size": MIN_TEAM_SIZE,
            "categories": [],
            "recommended_actions": [],
            "validated_patterns": [],
            "gating_applied": True,
        }

    categories = aggregate_team_categories(team_summary)
    actions = recommend_actions_from_categories(categories)

    surviving_patterns = filter_patterns_by_k_anonymity(correlations["patterns"])
    validated_patterns = [
        {
            "calendar_fact": p["calendar_fact"],
            "severity_band": p["severity_band"],
            "n_people_affected": p["n_people_affected"],
            "mean_lift_points": p["mean_lift_points"],
        }
        for p in surviving_patterns
    ]

    return {
        "gate_passed": True,
        "team_size": team_size,
        "min_team_size": MIN_TEAM_SIZE,
        "categories": categories,
        "recommended_actions": actions,
        "validated_patterns": validated_patterns,
        "gating_applied": True,  # this is where Part 3's ungated data actually gets gated
    }


if __name__ == "__main__":
    view = build_employer_view()
    with open(OUTPUT_PATH, "w") as f:
        json.dump(view, f, indent=2)
    print(json.dumps(view, indent=2))
    print(f"\nWrote {OUTPUT_PATH}")