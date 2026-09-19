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


# ---------------------------------------------------------------------------
# Part 5 adapter: Part 5's UI expects a different schema than the one above
# (dashboard tiles, a meeting-density heatmap, capitalized severity bands).
# This section transforms the real, already-gated output into that shape and
# writes it to ../data/employer_view.json, which is the exact path Part 5's
# UI fetches (ui/app.js: fetch('../data/employer_view.json')).
# No opt-in section exists in this pipeline (Part 3's privacy contract
# forbids Part 4 from reading any per-person file), so that part of Part 5's
# schema is always reported honestly as zero/not-reportable rather than
# faked to match their original mock.
# ---------------------------------------------------------------------------

PART5_OUTPUT_PATH = os.path.join(DATA_DIR, "employer_view.json")

_SEVERITY_MAP = {
    "minimal": "Low",
    "low": "Low",
    "moderate": "Moderate",
    "elevated": "Elevated",
    "high": "High",
    "no signal": "Low",
}


def load_team_themes(path=None):
    """Part 3's new employer-safe projection: exactly 5 fixed themes, banded
    severity/prevalence, deterministic actions. No thresholds, no counts, no
    magnitudes, no person_id at any depth. This is what Part 4 renders to
    Part 5 -- NOT the raw pattern list in team_correlations.json, which is
    Part 4's internal analytical input only (see the employer-view privacy
    redesign spec, section 4)."""
    path = path or os.path.join(PART3_OUT_DIR, "team_themes.json")
    with open(path) as f:
        return json.load(f)


def _theme_to_causal_category(theme):
    fact = theme["calendar_fact"]
    if theme.get("protective_fact"):
        fact = f"{fact} {theme['protective_fact']}"
    return {
        "category": theme["label"],
        "structural_fact": fact,
        "severity_band": _SEVERITY_MAP.get(theme["severity_band"], "Moderate"),
        "trend": "flat",
        "recommended_action": theme["action"],
    }


def load_raw_events(path=None):
    """Part 2's per-event calendar (data/synthetic_calendar.json) — used only
    to build an honest meeting-density heatmap; never used for gating."""
    path = path or os.path.join(DATA_DIR, "synthetic_calendar.json")
    with open(path) as f:
        return json.load(f)


def build_meeting_density_heatmap(events, team_size):
    """Real weekday x time-block average meeting count per person, computed
    from actual event start times. grid[block_index][day_index]."""
    from datetime import date as _date

    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    block_labels = ["08\u201310", "10\u201312", "12\u201314", "14\u201316", "16\u201318"]
    grid = [[0.0] * 5 for _ in range(5)]

    for e in events:
        d = _date.fromisoformat(e["date"])
        weekday = d.weekday()  # 0=Mon .. 6=Sun
        if weekday > 4:
            continue
        hour = int(e["start_time"][11:13])
        if hour < 8 or hour >= 18:
            continue
        block = (hour - 8) // 2
        grid[block][weekday] += 1

    if team_size:
        grid = [[round(v / team_size, 1) for v in row] for row in grid]

    return {
        "caption": "Team average meetings per person, by weekday and time block.",
        "days": day_labels,
        "blocks": block_labels,
        "grid": grid,
    }


def build_part5_view(team_summary_path=None, team_themes_path=None, events_path=None):
    team_summary = load_team_structural_summary(team_summary_path)
    m = team_summary["metrics"]
    themes_data = load_team_themes(team_themes_path)
    team_size = themes_data["n_people_analysed"]
    below_floor = themes_data.get("below_floor", False)

    base = {
        "schema_version": "1.0",
        "_comment": (
            "SYNTHETIC DATA. Employer view built from Part 3's team_themes.json "
            "(fixed 5-theme rollup, banded severity/prevalence, no thresholds, "
            "no counts, no per-person magnitudes -- see the employer-view "
            "privacy redesign spec). No opt-in data exists in this pipeline by "
            "design \u2014 see opt_in_shares below."
        ),
        "synthetic": True,
        "team_id": "TEAM-1",
        "team_label": "Demo Team",
        "generated_at": __import__("datetime").date.today().isoformat(),
        "k_anonymity": {
            "threshold": MIN_TEAM_SIZE,
            "team_size": team_size,
            "satisfied": not below_floor,
        },
        "opt_in_shares": {
            "count": 0,
            "min_reportable": 3,
            "reportable": False,
            "suppressed_message": (
                "Opt-in sharing is not part of this pipeline \u2014 Part 3's privacy "
                "contract keeps individual insights out of Part 4 entirely, so "
                "nothing is ever escalated here."
            ),
        },
        "never_shown": [
            "Any individual's stress score",
            "Any name or employee identifier",
            "Any per-person biometric or calendar record",
            "Counts of affected people below the k-anonymity threshold",
            "Any ranking or comparison between team members",
        ],
        "structural_summary": {
            "hero": {
                "label": "Meetings per person per day",
                "value": round(m["avg_daily_meetings_per_person"], 1),
                "unit": "",
            },
            "tiles": [
                {"label": "Meetings without an agenda", "value": round(m["pct_meetings_without_agenda"], 1), "unit": "%"},
                {"label": "Meetings outside 9am\u20136pm", "value": round(m["pct_meetings_after_hours"], 1), "unit": "%"},
                {"label": "Days without a lunch buffer", "value": round(m["pct_days_without_lunch_buffer"], 1), "unit": "%"},
                {"label": "Meetings back-to-back", "value": round(m["pct_meetings_back_to_back"], 1), "unit": "%"},
            ],
        },
        # Fixed 5-row shape, always -- including when below the k floor, per
        # the redesign spec: "the five themes still render, all at no signal",
        # so an empty team and a healthy team never look identical by omission.
        "causal_categories": [_theme_to_causal_category(t) for t in themes_data["themes"]],
    }

    events = load_raw_events(events_path)
    base["meeting_density"] = build_meeting_density_heatmap(events, team_size)
    return base


if __name__ == "__main__":
    view = build_employer_view()
    with open(OUTPUT_PATH, "w") as f:
        json.dump(view, f, indent=2)
    print(json.dumps(view, indent=2))
    print(f"\nWrote {OUTPUT_PATH}")

    part5_view = build_part5_view()
    with open(PART5_OUTPUT_PATH, "w") as f:
        json.dump(part5_view, f, indent=2)
    print(f"\nWrote {PART5_OUTPUT_PATH} (Part 5's actual read path)")