"""Generate mock Part 1 + Part 2 inputs so Part 3 can be built before they land.

ALL OUTPUT IS SYNTHETIC. This is not real WHOOP data and not a real calendar.

Each simulated person is assigned a hidden "sensitivity profile" -- a real,
planted correlation between a calendar feature and next-day stress. The
correlation engine is supposed to rediscover these from the data alone, which
also gives us a ground truth to test the validator against.

Usage:
    python part3/contracts/make_fixtures.py --people 12 --days 28
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"

# Planted correlations. driver -> how strongly it raises NEXT-day stress.
# The engine must rediscover these without being told.
SENSITIVITY_PROFILES = [
    ("back_to_back_blocks", 7.0, "drained by packed consecutive meetings"),
    ("after_hours_meetings", 9.0, "evening meetings disrupt wind-down"),
    ("no_agenda_meetings", 5.5, "unprepared meetings create ambiguity load"),
    ("large_meetings", 4.5, "large audiences raise performance pressure"),
    ("meeting_count", 3.0, "sheer volume"),
    ("negative_sentiment_meetings", 8.0, "difficult conversations linger"),
]

TITLES = {
    "status": ["Daily standup", "Sprint sync", "Weekly status", "Team check-in"],
    "1:1": ["1:1", "Manager 1:1", "Skip-level", "Career chat"],
    "review": ["Design review", "Code review", "Perf review", "Quarterly review"],
    "external": ["Client call", "Vendor sync", "Partner intro", "Customer escalation"],
    "planning": ["Roadmap planning", "Sprint planning", "Backlog grooming", "OKR planning"],
    "incident": ["Incident postmortem", "Outage bridge", "Escalation review", "Urgent triage"],
}
NEGATIVE_CATEGORIES = {"incident", "review"}


def _sentiment_for(category: str, title: str, rng: random.Random) -> float:
    base = -0.45 if category in NEGATIVE_CATEGORIES else 0.15
    if any(w in title.lower() for w in ("urgent", "escalation", "outage", "postmortem")):
        base -= 0.3
    return round(max(-1.0, min(1.0, base + rng.uniform(-0.2, 0.2))), 2)


def _gen_day_meetings(person_id: str, day: date, rng: random.Random,
                      intensity: float) -> list[dict]:
    """Build one workday of meetings. `intensity` scales how packed the day is."""
    if day.weekday() >= 5:  # weekend
        n = 1 if rng.random() < 0.08 else 0
    else:
        # Wednesdays are deliberately the heavy day -- gives Part 4 a concrete,
        # non-personal structural fact to report.
        bump = 1.6 if day.weekday() == 2 else 0.0
        n = max(0, int(rng.gauss(3.2 * intensity + bump, 1.4)))
        n = min(n, 9)

    meetings: list[dict] = []
    cursor = 8 * 60 + rng.choice([0, 30, 60])  # minutes from midnight
    for i in range(n):
        gap = rng.choice([0, 0, 5, 15, 30, 45, 60, 90])
        cursor += gap
        duration = rng.choice([15, 25, 30, 30, 45, 60, 60, 90])
        start, end = cursor, cursor + duration
        cursor = end
        if start > 20 * 60:
            break

        category = rng.choice(list(TITLES))
        title = rng.choice(TITLES[category])
        is_after_hours = start < 8 * 60 or end > 18 * 60
        meetings.append({
            "event_id": f"{person_id}-{day.isoformat()}-{i}",
            "person_id": person_id,
            "date": day.isoformat(),
            "start": f"{start // 60:02d}:{start % 60:02d}",
            "end": f"{end // 60:02d}:{end % 60:02d}",
            "title": title,
            "attendee_count": max(2, int(rng.gauss(6, 4))),
            "has_agenda": rng.random() > 0.42,
            "is_recurring": category in ("status", "1:1") and rng.random() > 0.25,
            "is_after_hours": is_after_hours,
            "is_back_to_back": gap <= 5 and i > 0,
            "sentiment": _sentiment_for(category, title, rng),
            "category": category,
        })
    return meetings


def _day_feature_snapshot(meetings: list[dict]) -> dict[str, float]:
    """Minimal recomputation used only to drive the planted correlation."""
    return {
        "meeting_count": float(len(meetings)),
        "back_to_back_blocks": float(sum(m["is_back_to_back"] for m in meetings)),
        "after_hours_meetings": float(sum(m["is_after_hours"] for m in meetings)),
        "no_agenda_meetings": float(sum(not m["has_agenda"] for m in meetings)),
        "large_meetings": float(sum(m["attendee_count"] > 8 for m in meetings)),
        "negative_sentiment_meetings": float(sum(m["sentiment"] < -0.3 for m in meetings)),
    }


def generate(n_people: int, n_days: int, seed: int) -> tuple[list[dict], list[dict], dict]:
    rng = random.Random(seed)
    start_day = date(2026, 8, 24)  # a Monday

    stress_rows: list[dict] = []
    all_meetings: list[dict] = []
    ground_truth: dict[str, dict] = {}

    for p in range(n_people):
        person_id = f"emp_{p + 1:03d}"
        driver, strength, why = SENSITIVITY_PROFILES[p % len(SENSITIVITY_PROFILES)]
        baseline = rng.uniform(32, 48)
        intensity = rng.uniform(0.7, 1.35)
        ground_truth[person_id] = {
            "driver_feature": driver,
            "effect_points": strength,
            "narrative": why,
            "baseline_stress": round(baseline, 1),
        }

        by_day: dict[date, list[dict]] = {}
        for d in range(n_days):
            day = start_day + timedelta(days=d)
            m = _gen_day_meetings(person_id, day, rng, intensity)
            by_day[day] = m
            all_meetings.extend(m)

        for d in range(n_days):
            day = start_day + timedelta(days=d)
            prev = by_day.get(day - timedelta(days=1), [])
            snap = _day_feature_snapshot(prev)

            # The planted signal: yesterday's driver raises today's stress.
            effect = strength * min(snap.get(driver, 0.0), 4.0) / 2.0
            weekend_relief = -8.0 if day.weekday() >= 5 else 0.0
            noise = rng.gauss(0, 5.5)
            score = baseline + effect + weekend_relief + noise
            score = max(5.0, min(98.0, score))

            factors = []
            if snap.get("back_to_back_blocks", 0) >= 2:
                factors.append("low_recovery")
            if snap.get("after_hours_meetings", 0) >= 1:
                factors.append("short_sleep")
            if score > 60:
                factors.append("elevated_strain")
            if score < 35:
                factors.append("high_hrv")

            stress_rows.append({
                "person_id": person_id,
                "date": day.isoformat(),
                "stress_score": round(score, 1),
                "contributing_factors": "|".join(factors),
            })

    return stress_rows, all_meetings, ground_truth


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate SYNTHETIC Part 1 + Part 2 fixtures")
    ap.add_argument("--people", type=int, default=12)
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--seed", type=int, default=20260919)
    ap.add_argument("--out", type=Path, default=FIXTURES)
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    stress, meetings, truth = generate(args.people, args.days, args.seed)

    csv_path = args.out / "stress_scores.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        f.write("# SYNTHETIC DATA - generated by part3/contracts/make_fixtures.py\n")
        w = csv.DictWriter(
            f, fieldnames=["person_id", "date", "stress_score", "contributing_factors"]
        )
        w.writeheader()
        w.writerows(stress)

    mtg_path = args.out / "meeting_features.json"
    mtg_path.write_text(json.dumps({
        "synthetic": True,
        "data_provenance": "SIMULATED",
        "note": "Mock Part 2 output. Replace with the real file when it lands.",
        "events": meetings,
    }, indent=2), encoding="utf-8")

    truth_path = args.out / "_ground_truth.json"
    truth_path.write_text(json.dumps({
        "synthetic": True,
        "note": "Planted correlations. Test-only; the engine must not read this.",
        "people": truth,
    }, indent=2), encoding="utf-8")

    print(f"SYNTHETIC fixtures written to {args.out}")
    print(f"  stress_scores.csv      {len(stress)} rows")
    print(f"  meeting_features.json  {len(meetings)} events")
    print(f"  _ground_truth.json     {len(truth)} people (test-only)")


if __name__ == "__main__":
    main()
