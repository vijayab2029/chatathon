#!/usr/bin/env python3
"""
PART 1 - Synthetic WHOOP biometrics & explainable stress scoring.

Generates two artifacts in ./data/:

  A. whoop_biometrics.csv   per-person / per-day biometrics, field names matched
                             to WHOOP's real Developer API (Recovery, Sleep, Cycle
                             endpoints) so this reads as a plausible real integration.
  B. stress_scores.csv      person_id, date, stress_score (0-100), contributing_factors
                             -> feeds Part 3's correlation/insight engine.

WHOOP FIELD MAPPING (verified against developer.whoop.com, Sept 2026):
  Recovery : recovery_score, hrv_rmssd_milli, resting_heart_rate
  Sleep    : sleep_efficiency_percentage, sleep_performance_percentage, respiratory_rate
  Cycle    : strain (0-21 Borg scale)
  Omitted on purpose: spo2_percentage / skin_temp_celsius (WHOOP 4.0-only, not
  stress-diagnostic) and kilojoule / max_heart_rate (not used by the scoring model).

THE CORE DESIGN POINT - LAG, NOT SAME-DAY:
  Recovery/HRV/RHR are measured overnight and reported the *next* morning. So the
  causal chain modeled here is: day D's meeting load -> day D's sleep that night ->
  day D+1 morning's recovery/HRV/RHR. Same-day meeting load only drives that same
  day's strain (Cycle). This script computes both, on purpose, so Part 3 can find
  a real lag-1 correlation instead of a same-day one that WHOOP wouldn't produce.

  Every person shares the same baseline physiology distribution; only calendar
  exposure differs. Any stress divergence between people is schedule-driven, not
  "born stressed" - keeps the honesty principle intact for the demo.

ALL DATA IS SYNTHETIC. No real biometrics, no real person, no real WHOOP account.

Standard library only. Deterministic: random.seed(42).
Depends on: data/meeting_features.json (Part 2 output). Falls back to zero-load
days if that file is missing, so this script can still run standalone.
Run:  python scripts/part1_biometrics/generate_biometrics.py
"""

from __future__ import annotations

import csv
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------------------
# Configuration - COHORT and window must match Part 2 (scripts/part2_calendar) for the
# Part 3 join to work.
# --------------------------------------------------------------------------------------

RANDOM_SEED = 42

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"

FEATURES_PATH = DATA_DIR / "meeting_features.json"
BIOMETRICS_PATH = DATA_DIR / "whoop_biometrics.csv"
STRESS_PATH = DATA_DIR / "stress_scores.csv"

START_DATE = "2026-09-13"
END_DATE = "2026-09-19"

COHORT = ["user_101", "user_102", "user_103", "user_104", "user_105", "user_106"]

SLEEP_NEED_HOURS = 8.0

# Calendar-load sensitivity. Tuned so a near-worst-case day (load ~0.8-0.9, WHOOP's
# own scale) pushes next-day recovery into WHOOP's yellow/red bands (<66%), matching
# how a real bad week actually reads on the app - not a barely-visible dip.
HRV_LOAD_SENSITIVITY = 0.65
RHR_LOAD_SENSITIVITY = 0.55
SLEEP_HOURS_DISRUPTION_COEF = 2.5
SLEEP_HOURS_LOAD_COEF = 1.2
SLEEP_EFF_DISRUPTION_COEF = 25.0
SLEEP_EFF_LOAD_COEF = 20.0

# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------


def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d")


def date_range(start: str, end: str) -> list[str]:
    d, last = parse_date(start), parse_date(end)
    out = []
    while d <= last:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy) ** 0.5


# --------------------------------------------------------------------------------------
# Load Part 2's meeting features (per-person daily rollups). Missing day = rest day.
# --------------------------------------------------------------------------------------


def load_meeting_features() -> dict[tuple[str, str], dict]:
    if not FEATURES_PATH.exists():
        print(f"WARNING: {FEATURES_PATH} not found - generating with zero calendar load.")
        return {}
    with open(FEATURES_PATH, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    return {(r["person_id"], r["date"]): r for r in rows}


def cognitive_load(f: dict | None) -> float:
    """0-1 same-day load from meeting density/fragmentation (drives that day's strain)."""
    if f is None:
        return 0.0
    load = 0.0
    load += min(f["total_meetings"], 8) / 8 * 0.30
    load += min(f["back_to_back_count"], 4) / 4 * 0.30
    load += (1.0 if f["no_lunch_buffer"] else 0.0) * 0.25
    load += f["no_agenda_pct"] * 0.15
    return clamp(load, 0.0, 1.0)


def sleep_disruption(f: dict | None) -> float:
    """0-1 same-day load specifically from after-hours meetings (drives that night's sleep)."""
    if f is None:
        return 0.0
    return clamp(min(f["after_hours_count"], 3) / 3, 0.0, 1.0)


# --------------------------------------------------------------------------------------
# Per-person baseline physiology - same distribution for everyone; only calendar
# exposure (above) creates divergence.
# --------------------------------------------------------------------------------------


def build_baselines() -> dict[str, dict]:
    baselines = {}
    for person_id in COHORT:
        baselines[person_id] = {
            "hrv": random.uniform(55.0, 75.0),        # ms, RMSSD
            "rhr": random.uniform(56.0, 66.0),         # bpm
        }
    return baselines


# --------------------------------------------------------------------------------------
# Day-by-day biometric generation
# --------------------------------------------------------------------------------------


def generate_biometrics(features: dict[tuple[str, str], dict],
                         baselines: dict[str, dict]) -> list[dict]:
    dates = date_range(START_DATE, END_DATE)
    rows = []
    for person_id in COHORT:
        base = baselines[person_id]
        for i, date_str in enumerate(dates):
            prior_date = dates[i - 1] if i > 0 else None
            prior_f = features.get((person_id, prior_date)) if prior_date else None
            today_f = features.get((person_id, date_str))

            prior_cog = cognitive_load(prior_f)
            prior_sleep_dis = sleep_disruption(prior_f)
            today_cog = cognitive_load(today_f)

            hrv = base["hrv"] * (1 - HRV_LOAD_SENSITIVITY * prior_cog) + random.gauss(0, 3)
            hrv = clamp(hrv, 20.0, 120.0)

            rhr = base["rhr"] * (1 + RHR_LOAD_SENSITIVITY * prior_cog) + random.gauss(0, 1.5)
            rhr = clamp(rhr, 45.0, 95.0)

            sleep_hours = (SLEEP_NEED_HOURS - SLEEP_HOURS_DISRUPTION_COEF * prior_sleep_dis
                           - SLEEP_HOURS_LOAD_COEF * prior_cog + random.gauss(0, 0.3))
            sleep_hours = clamp(sleep_hours, 3.5, 9.0)

            sleep_efficiency = (92.0 - SLEEP_EFF_DISRUPTION_COEF * prior_sleep_dis
                                 - SLEEP_EFF_LOAD_COEF * prior_cog + random.gauss(0, 2.0))
            sleep_efficiency = clamp(sleep_efficiency, 40.0, 98.0)

            sleep_performance = clamp(
                100.0 * (sleep_hours / SLEEP_NEED_HOURS) * (sleep_efficiency / 95.0), 0.0, 100.0)

            respiratory_rate = clamp(15.5 + 0.3 * prior_sleep_dis + random.gauss(0, 0.6), 11.0, 20.0)

            hrv_score = clamp(hrv / base["hrv"] * 100, 0.0, 100.0)
            rhr_score = clamp((2 * base["rhr"] - rhr) / base["rhr"] * 100, 0.0, 100.0)
            recovery_score = round(clamp(
                0.45 * hrv_score + 0.30 * rhr_score + 0.25 * sleep_efficiency, 0.0, 100.0))

            strain = clamp(9.0 + 6.0 * today_cog + random.gauss(0, 1.0), 0.0, 21.0)

            rows.append({
                "person_id": person_id,
                "date": date_str,
                "recovery_score": recovery_score,
                "hrv_rmssd_milli": round(hrv, 1),
                "resting_heart_rate": round(rhr),
                "sleep_efficiency_percentage": round(sleep_efficiency, 1),
                "sleep_performance_percentage": round(sleep_performance, 1),
                "total_sleep_hours": round(sleep_hours, 2),
                "respiratory_rate": round(respiratory_rate, 1),
                "strain": round(strain, 1),
                "_baseline_hrv": base["hrv"],
                "_baseline_rhr": base["rhr"],
            })
    return rows


# --------------------------------------------------------------------------------------
# Explainable stress score
# --------------------------------------------------------------------------------------


def compute_stress(row: dict) -> tuple[int, str]:
    recovery_deficit = 100 - row["recovery_score"]
    hrv_deficit_pct = clamp(
        (row["_baseline_hrv"] - row["hrv_rmssd_milli"]) / row["_baseline_hrv"] * 100, 0.0, 100.0)
    rhr_excess_pct = clamp(
        (row["resting_heart_rate"] - row["_baseline_rhr"]) / row["_baseline_rhr"] * 100, 0.0, 100.0)
    sleep_deficit_pct = clamp(
        (SLEEP_NEED_HOURS - row["total_sleep_hours"]) / SLEEP_NEED_HOURS * 100, 0.0, 100.0)

    contributions = {
        f"low recovery ({row['recovery_score']}%)": 0.35 * recovery_deficit,
        f"reduced HRV ({-hrv_deficit_pct:.0f}% vs personal baseline)": 0.25 * hrv_deficit_pct,
        f"elevated resting heart rate (+{rhr_excess_pct:.0f}% vs personal baseline)": 0.20 * rhr_excess_pct,
        f"shortened sleep ({row['total_sleep_hours']:.1f}h vs {SLEEP_NEED_HOURS:.0f}h need)": 0.20 * sleep_deficit_pct,
    }

    score = sum(contributions.values())

    overreaching = row["strain"] > 14.0 and row["recovery_score"] < 50
    if overreaching:
        contributions["high strain without matching recovery (overreaching)"] = 10.0
        score += 10.0

    score = round(clamp(score, 0.0, 100.0))

    top_factors = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)[:3]
    factors_str = "; ".join(label for label, weight in top_factors if weight > 3.0)
    if not factors_str:
        factors_str = "no significant stress drivers"

    return score, factors_str


# --------------------------------------------------------------------------------------
# Self-verification of the encoded design
# --------------------------------------------------------------------------------------


def verify_design(biometrics: list[dict], stress: list[dict]) -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []

    def avg(vals):
        return sum(vals) / len(vals) if vals else 0.0

    def stress_for(pid):
        return [s["stress_score"] for s in stress if s["person_id"] == pid]

    def recovery_for(pid):
        return [b["recovery_score"] for b in biometrics if b["person_id"] == pid]

    hs_avg = avg(stress_for("user_101"))
    bal_avg = avg(stress_for("user_102") + stress_for("user_104"))
    checks.append((hs_avg > bal_avg + 20,
                   f"user_101 avg stress ({hs_avg:.1f}) well above balanced cohort ({bal_avg:.1f})"))

    hs_recovery_days = [r for r, f in zip(recovery_for("user_101"), date_range(START_DATE, END_DATE))
                         if f in ("2026-09-16", "2026-09-17", "2026-09-18")]
    checks.append((min(hs_recovery_days) < 66,
                   f"user_101 recovery drops into WHOOP's yellow/red band (<66%) after overload days "
                   f"(min={min(hs_recovery_days)}%)"))

    checks.append((max(recovery_for("user_102")) - min(recovery_for("user_102")) < 25,
                   "user_102 recovery stays relatively stable across the week"))

    # Lag-1 correlation: prior-day cognitive load vs next-day recovery, across everyone.
    features = load_meeting_features()
    dates = date_range(START_DATE, END_DATE)
    loads, recoveries = [], []
    for pid in COHORT:
        for i in range(1, len(dates)):
            prior_f = features.get((pid, dates[i - 1]))
            row = next((b for b in biometrics if b["person_id"] == pid and b["date"] == dates[i]), None)
            if row is not None:
                loads.append(cognitive_load(prior_f))
                recoveries.append(row["recovery_score"])
    corr = pearson(loads, recoveries)
    checks.append((corr < -0.3, f"lag-1 correlation (prior-day load -> next-day recovery) = {corr:.2f} (negative, as expected)"))

    return checks


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------


def write_biometrics_csv(rows: list[dict]) -> None:
    fieldnames = ["person_id", "date", "recovery_score", "hrv_rmssd_milli",
                  "resting_heart_rate", "sleep_efficiency_percentage",
                  "sleep_performance_percentage", "total_sleep_hours",
                  "respiratory_rate", "strain"]
    with open(BIOMETRICS_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fieldnames})


def write_stress_csv(rows: list[dict]) -> None:
    with open(STRESS_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["person_id", "date", "stress_score", "contributing_factors"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> int:
    random.seed(RANDOM_SEED)
    os.makedirs(DATA_DIR, exist_ok=True)

    features = load_meeting_features()
    baselines = build_baselines()
    biometrics = generate_biometrics(features, baselines)

    stress_rows = []
    for row in biometrics:
        score, factors = compute_stress(row)
        stress_rows.append({
            "person_id": row["person_id"],
            "date": row["date"],
            "stress_score": score,
            "contributing_factors": factors,
        })

    write_biometrics_csv(biometrics)
    write_stress_csv(stress_rows)

    bar = "=" * 78
    print(bar)
    print("PART 1 - SYNTHETIC WHOOP BIOMETRICS & STRESS SCORING")
    print("ALL DATA IS SIMULATED. random.seed(%d) -> deterministic output." % RANDOM_SEED)
    print(bar)
    print(f"Window  : {START_DATE} -> {END_DATE}  Cohort: {len(COHORT)} simulated people")
    print()
    print("ARTIFACTS WRITTEN")
    print(f"  [A] {BIOMETRICS_PATH.relative_to(PROJECT_ROOT)}  -> {len(biometrics)} person-day rows")
    print(f"  [B] {STRESS_PATH.relative_to(PROJECT_ROOT)}      -> {len(stress_rows)} person-day rows")
    print()
    print("SAMPLE - user_101, Tue-Thu overload window (Part 2 cohort)")
    for row in stress_rows:
        if row["person_id"] == "user_101" and row["date"] in ("2026-09-15", "2026-09-16", "2026-09-17"):
            print(f"  {row['date']}  stress={row['stress_score']:>3}  {row['contributing_factors']}")
    print()
    print("DESIGN VERIFICATION")
    checks = verify_design(biometrics, stress_rows)
    for ok, label in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(bar)

    failures = [label for ok, label in checks if not ok]
    if failures:
        print("RESULT: FAILED - see [FAIL] lines above.")
        return 1
    print("RESULT: OK - biometrics and stress scores generated and verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
