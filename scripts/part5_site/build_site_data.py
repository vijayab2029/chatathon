#!/usr/bin/env python3
"""Connect the services to the website.

The site (`part5/ui/`) reads exactly two files and nothing else:

    data/employee_insight.json   private, one person, full detail
    data/employer_view.json      aggregate, k-gated, no person dimension

This script is the only thing that writes them. It runs the real pipeline
end to end so the site is wired to Part 1/Part 2's actual output rather
than to Part 3's test fixtures:

    Part 1/2 data  ->  Part 3 pipeline  ->  Part 4 gate  ->  data/*.json  ->  part5/ui/

    python scripts/part5_site/build_site_data.py

ALL DATA IS SIMULATED. No real person is represented anywhere in this repo.

Only `data/*.json` is written. Nothing under `part5/ui/` is touched -- the site is
another owner's deliverable and we connect to it, we do not edit it. Pass
`--fallback` to additionally run the site owner's own inlining generator
(`node part5/ui/build-fallback.mjs`), which refreshes its `fallback-data.js` so the
demo also works when index.html is opened over file://.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
from datetime import date as Date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
PART3_SRC = REPO / "part3" / "src"
PART3_OUT = REPO / "part3" / "data" / "out"
UI_DIR = REPO / "part5" / "ui"

STRESS_CSV = DATA / "stress_scores.csv"
MEETINGS_JSON = DATA / "meeting_features.json"
EMPLOYEE_OUT = DATA / "employee_insight.json"
EMPLOYER_OUT = DATA / "employer_view.json"

# The site plots one person and has no picker, so the payload carries one.
# user_101 is the trajectory case: an escalating calendar walks recovery from
# 65% to 34.6% across four weeks while the protected calendars hold at 78%.
DEFAULT_PERSON = "user_101"

# part5/ui/index.html hardcodes "What your 14 days look like" and app.js prints
# "vs your 14-day average", so the trend ships the last 14 days. Part 3 still
# detects patterns across all 28, so the chronic term is not lost -- it is
# just not the thing being plotted.
TREND_DAYS = 14

# What the site renders, straight off stress_scores.csv.
# (label, column, unit, direction, decimals)
BIOMETRIC_TILES = [
    ("Recovery",   "recovery_pct",  "%",   "lower_is_worse",  0),
    ("HRV",        "hrv_ms",        "ms",  "lower_is_worse",  0),
    ("Sleep",      "sleep_hours",   "h",   "lower_is_worse",  1),
    ("Resting HR", "rhr_bpm",       "bpm", "higher_is_worse", 0),
    ("Day strain", "day_strain",    "",    "higher_is_worse", 1),
]

# Raw feature names -> something a person would recognise on their calendar.
FEATURE_PHRASES = {
    "total_meeting_minutes": "a heavy block of booked meeting time",
    "meeting_count": "a high meeting count",
    "back_to_back_blocks": "meetings stacked back-to-back",
    "longest_back_to_back_run": "a long unbroken run of back-to-back meetings",
    "focus_time_minutes": "a large block of uninterrupted focus time",
    "no_agenda_meetings": "meetings booked without an agenda",
    "after_hours_meetings": "meetings outside working hours",
    "midday_break_minutes": "a protected midday break",
    "avg_attendee_count": "large-attendance meetings",
    "longest_meeting_stretch_min": "one long unbroken meeting stretch",
}

NEVER_SHARES = [
    "Your stress score, today's or any other day's",
    "Your recovery, HRV, sleep or strain numbers",
    "Any individual day of your calendar",
]


# ---------------------------------------------------------------------------
# Step 1 -- Part 3, over the real cohort
# ---------------------------------------------------------------------------

def run_part3(offline: bool = True) -> dict:
    """Run the correlation & insight engine over data/, not over its fixtures."""
    if str(PART3_SRC) not in sys.path:
        sys.path.insert(0, str(PART3_SRC))
    from insight.pipeline import run_pipeline

    return run_pipeline(
        stress_csv=STRESS_CSV,
        meetings_json=MEETINGS_JSON,
        out_dir=PART3_OUT,
        offline=offline,
    )


# ---------------------------------------------------------------------------
# Step 2 -- Part 4, which already owns the employer payload
# ---------------------------------------------------------------------------

def run_part4() -> dict:
    """Call Part 4's own Part-5 adapter. We do not reimplement the gate."""
    path = REPO / "part4" / "part4.py"
    spec = importlib.util.spec_from_file_location("part4", path)
    part4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(part4)

    view = part4.build_part5_view()
    EMPLOYER_OUT.write_text(json.dumps(view, indent=2), encoding="utf-8")
    return view


# ---------------------------------------------------------------------------
# Step 3 -- the employee payload, which nothing built until now
# ---------------------------------------------------------------------------

def _load_stress_rows(person_id: str) -> list[dict]:
    with STRESS_CSV.open(encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["person_id"] == person_id]
    if not rows:
        raise SystemExit(f"No rows for {person_id} in {STRESS_CSV}")
    rows.sort(key=lambda r: r["date"])
    return rows


def _meetings_by_date(person_id: str) -> dict[str, int]:
    records = json.loads(MEETINGS_JSON.read_text(encoding="utf-8"))
    return {
        r["date"]: int(r.get("total_meetings", 0))
        for r in records
        if r["person_id"] == person_id
    }


def _band(score: float) -> str:
    """Bands use the site's own vocabulary (see SEVERITY in part5/ui/app.js)."""
    if score < 35:
        return "Low"
    if score < 55:
        return "Moderate"
    if score < 75:
        return "Elevated"
    return "High"


def _num(value: float, decimals: int) -> float | int:
    return int(round(value)) if decimals == 0 else round(value, decimals)


def _contributing_factors(today: dict) -> list[dict]:
    """The three terms of the documented stress formula, as shares of the score.

    See data/README.md -- the weights are computed from the same arithmetic
    that produced stress_score, so this chart is a decomposition of today's
    number rather than a second opinion about it.
    """
    recovery = float(today["recovery_pct"])
    strain = float(today["day_strain"])
    sleep = float(today["sleep_hours"])

    terms = [
        ("Recovery deficit", (100 - recovery) * 0.55,
         f"Recovery came in at {recovery:.0f}%, a {100 - recovery:.0f}-point deficit."),
        ("Physical exertion", (strain / 21.0) * 35,
         f"Day strain {strain:.1f} of a possible 21.0."),
        ("Sleep debt", ((8 - min(sleep, 8)) / 8) * 10,
         f"{sleep:.1f} h slept, {max(0.0, 8 - sleep):.1f} h short of an 8 h night."),
    ]
    total = sum(v for _, v, _ in terms) or 1.0

    factors = [
        {"factor": name, "weight_pct": int(round(v / total * 100)), "note": note}
        for name, v, note in terms
    ]
    factors.sort(key=lambda f: -f["weight_pct"])
    # Rounding three shares independently need not land on 100; absorb the
    # remainder into the largest so the chart reads as a whole.
    factors[0]["weight_pct"] += 100 - sum(f["weight_pct"] for f in factors)
    return factors


def _phrase(pattern: dict) -> str:
    return FEATURE_PHRASES.get(pattern.get("feature"), pattern.get("pattern", ""))


def _insight(record: dict) -> dict:
    patterns = record.get("top_patterns") or []
    if not patterns:
        return {
            "headline": "No pattern cleared validation in your data this period.",
            "detail": "The engine found nothing it could prove, so it is reporting nothing.",
            "confidence": "None",
            "confidence_note": "Simulated data. Not a clinical finding.",
            "evidence": [],
        }

    top = patterns[0]
    lift = abs(top["lift_points"])
    direction = "lower" if top["lift_points"] < 0 else "higher"
    headline = f"Your stress runs {lift:.0f} points {direction} the day after {_phrase(top)}."

    detail = record.get("insight_text", "")
    action = record.get("suggested_action")
    if action:
        detail = f"{detail} Suggested change: {action}"

    evidence = [
        f"{_phrase(p).capitalize()}: {p['mean_stress_when_present']:.1f} average the next day "
        f"versus {p['mean_stress_when_absent']:.1f} otherwise, across {p['days_observed']} such "
        f"days (r = {p['correlation_r']})."
        for p in patterns
    ]

    days = top.get("days_observed", 0)
    confidence = "Moderate" if top.get("p_value", 1) <= 0.01 and days >= 10 else "Low"
    return {
        "headline": headline,
        "detail": detail,
        "confidence": confidence,
        "confidence_note": (
            f"n = {len(record.get('stress_trend', []))} days of simulated data for one person. "
            "A correlation in your own data, not a diagnosis."
        ),
        "evidence": evidence,
    }


def build_employee_view(person_id: str = DEFAULT_PERSON, as_of: str | None = None) -> dict:
    """Build the private view. `as_of` picks which day is "today".

    Defaults to the last day of data, which in this dataset is a Saturday --
    an honest but very quiet hero card (0 meetings, score below baseline).
    Pass a weekday to show the page on a working day instead.
    """
    part3 = json.loads((PART3_OUT / "employee_insight.json").read_text(encoding="utf-8"))
    record = (part3.get("people") or {}).get(person_id)
    if record is None:
        raise SystemExit(
            f"Part 3 produced no record for {person_id}. "
            f"Available: {sorted((part3.get('people') or {}))}"
        )

    rows = _load_stress_rows(person_id)
    if as_of:
        cut = [i for i, r in enumerate(rows) if r["date"] == as_of]
        if not cut:
            raise SystemExit(
                f"{person_id} has no row for {as_of}. "
                f"Data runs {rows[0]['date']} to {rows[-1]['date']}."
            )
        rows = rows[: cut[0] + 1]
    if len(rows) < TREND_DAYS:
        raise SystemExit(
            f"Need {TREND_DAYS} days up to {rows[-1]['date']}, only {len(rows)} available."
        )

    window = rows[-TREND_DAYS:]
    today = rows[-1]
    meetings = _meetings_by_date(person_id)

    score = float(today["stress_score"])
    baseline = sum(float(r["stress_score"]) for r in window) / len(window)

    biometrics = []
    for label, column, unit, direction, decimals in BIOMETRIC_TILES:
        mean = sum(float(r[column]) for r in window) / len(window)
        biometrics.append({
            "label": label,
            "value": _num(float(today[column]), decimals),
            "unit": unit,
            "baseline": _num(mean, decimals),
            "direction": direction,
        })

    trend = [
        {
            "date": r["date"],
            "weekday": Date.fromisoformat(r["date"]).strftime("%a"),
            "stress_score": int(round(float(r["stress_score"]))),
            "meetings": meetings.get(r["date"], 0),
        }
        for r in window
    ]

    top = (record.get("top_patterns") or [{}])[0]
    share = _phrase(top) or "a schedule pattern"

    return {
        "schema_version": "1.0",
        "_comment": (
            "SIMULATED DATA. Built by scripts/part5_site/build_site_data.py from "
            "Part 1/2's data/ and Part 3's validated output. Private to Part 5's "
            "employee view -- Part 4 never reads this file."
        ),
        "synthetic": True,
        "person_id": person_id,
        "display_name": "You",
        "generated_at": today["date"],
        "current": {
            "stress_score": int(round(score)),
            "band": _band(score),
            "baseline_14d": int(round(baseline)),
            "delta_vs_baseline": int(round(score - baseline)),
        },
        "biometrics_today": biometrics,
        "trend": trend,
        "insight": _insight(record),
        "contributing_factors": _contributing_factors(today),
        "opt_in": {
            "enabled": False,
            "shares_if_enabled": [
                f"That your calendar shows {share}",
                "The recommended calendar change, with no numbers attached",
            ],
            "never_shares": NEVER_SHARES,
        },
    }


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--person", default=DEFAULT_PERSON,
                    help=f"whose private view to build (default {DEFAULT_PERSON})")
    ap.add_argument("--as-of", default=None, metavar="YYYY-MM-DD",
                    help="which day the page shows as 'today' (default: last day of data, "
                         "which is a Saturday and therefore a very quiet hero card)")
    ap.add_argument("--llm", action="store_true",
                    help="use the OpenAI agents; default is offline/deterministic")
    ap.add_argument("--skip-part3", action="store_true",
                    help="reuse part3/data/out/ instead of re-running the pipeline")
    ap.add_argument("--fallback", action="store_true",
                    help="also run `node part5/ui/build-fallback.mjs`")
    args = ap.parse_args()

    if args.skip_part3:
        print(f"Part 3: skipped, reusing {PART3_OUT}")
    else:
        summary = run_part3(offline=not args.llm)
        print(f"Part 3: {summary.get('n_people', '?')} people, "
              f"{summary.get('llm_calls', 0)} LLM call(s)")

    employer = run_part4()
    print(f"Part 4: team_size {employer['k_anonymity']['team_size']}, gate "
          f"{'passed' if employer['k_anonymity']['satisfied'] else 'BLOCKED'} -> {EMPLOYER_OUT}")

    employee = build_employee_view(args.person, args.as_of)
    EMPLOYEE_OUT.write_text(json.dumps(employee, indent=2), encoding="utf-8")
    print(f"Part 5: {args.person}, {len(employee['trend'])}-day trend, "
          f"score {employee['current']['stress_score']} -> {EMPLOYEE_OUT}")

    if args.fallback:
        subprocess.run(["node", str(UI_DIR / "build-fallback.mjs")], check=True)

    print("\nServe it:  ./serve.sh   then open http://localhost:8080/part5/ui/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
