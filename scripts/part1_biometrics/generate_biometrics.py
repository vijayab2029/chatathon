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

THE SECOND DESIGN POINT - ACUTE *AND* CHRONIC (new in the 28-day window):
  A 7-day snapshot only ever shows the acute lag above. Over four weeks the thing
  that actually distinguishes burnout from a bad Tuesday is ACCUMULATION: a trailing
  7-day load average that keeps suppressing HRV and keeps elevating RHR even on the
  person's lighter days. Both terms are modeled explicitly:

      acute   = yesterday's meeting load          -> the day-over-day drop
      chronic = trailing 7-day mean meeting load  -> the week-over-week slide

  That is why user_101's weekend rebounds get weaker as the month goes on: the acute
  term releases on a Sunday, the chronic term does not. Nobody's physiology is
  special - user_101 just accumulates four weeks of a worsening calendar.

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

START_DATE = "2026-08-23"   # Sunday
END_DATE = "2026-09-19"     # Saturday - 28 days, exactly 4 Sun-Sat weeks
WEEK_STARTS = ["2026-08-23", "2026-08-30", "2026-09-06", "2026-09-13"]

COHORT = ["user_101", "user_102", "user_103", "user_104", "user_105", "user_106"]

SLEEP_NEED_HOURS = 8.0

# How many days of history feed the chronic (accumulation) term.
TRAILING_WINDOW_DAYS = 7

# Calendar-load sensitivity, split into ACUTE (yesterday) and CHRONIC (trailing 7-day
# mean). Tuned so that: a control's light week holds green/yellow recovery (60-85%),
# an ordinary enterprise week reads yellow (42-65%), and four weeks of escalating
# fragmentation drive recovery into WHOOP's red band (22-35%) with sleep under 5.5h.
# Acute and chronic are weighted equally: a brutal yesterday and a brutal trailing week
# move the needle about the same amount. That is what makes week 4 worse than week 2 on
# an identical Wednesday - the acute term repeats, the chronic term has climbed.
HRV_ACUTE_SENSITIVITY = 0.40
HRV_CHRONIC_SENSITIVITY = 0.40
RHR_ACUTE_SENSITIVITY = 0.18
RHR_CHRONIC_SENSITIVITY = 0.18

SLEEP_HOURS_DISRUPTION_COEF = 2.2   # after-hours meetings eat the front of the night
SLEEP_HOURS_ACUTE_COEF = 1.7
SLEEP_HOURS_CHRONIC_COEF = 0.9

SLEEP_EFF_BASE = 90.0
SLEEP_EFF_DISRUPTION_COEF = 20.0
SLEEP_EFF_ACUTE_COEF = 12.0
SLEEP_EFF_CHRONIC_COEF = 12.0

STRAIN_FLOOR = 8.5          # a rest day still carries ordinary daily life
STRAIN_LOAD_COEF = 8.0

# WHOOP scores recovery as a position within YOUR OWN range, not as a raw ratio - which
# is why 100% is rare and why a bad stretch reads red rather than "slightly below
# average". These four constants define that range. Without them a zero-meeting Sunday
# scores 98%, which no real WHOOP user sees week after week.
HRV_SCORE_FLOOR_RATIO = 0.30    # HRV at 30% of your baseline scores 0 on that axis
HRV_SCORE_CEIL_RATIO = 1.20     # ...and at 120% of baseline scores 100
RHR_SCORE_BEST_RATIO = 0.95     # RHR at 95% of baseline scores 100
RHR_SCORE_WORST_RATIO = 1.35    # ...and at 135% of baseline scores 0

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


def trailing_load(features: dict[tuple[str, str], dict], person_id: str,
                   dates: list[str], i: int) -> float:
    """Mean meeting load over the previous TRAILING_WINDOW_DAYS days (today excluded).

    This is the chronic/accumulation term. Weekends count as the zeros they are, so a
    normal week decays the average and a crunch week keeps it pinned high.
    """
    window = dates[max(0, i - TRAILING_WINDOW_DAYS):i]
    if not window:
        return 0.0
    return sum(cognitive_load(features.get((person_id, d))) for d in window) / len(window)


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

            prior_cog = cognitive_load(prior_f)              # acute
            chronic = trailing_load(features, person_id, dates, i)
            prior_sleep_dis = sleep_disruption(prior_f)
            today_cog = cognitive_load(today_f)

            hrv = base["hrv"] * (1 - HRV_ACUTE_SENSITIVITY * prior_cog
                                 - HRV_CHRONIC_SENSITIVITY * chronic) + random.gauss(0, 2.2)
            hrv = clamp(hrv, 20.0, 120.0)

            rhr = base["rhr"] * (1 + RHR_ACUTE_SENSITIVITY * prior_cog
                                 + RHR_CHRONIC_SENSITIVITY * chronic) + random.gauss(0, 1.2)
            rhr = clamp(rhr, 45.0, 95.0)

            sleep_hours = (SLEEP_NEED_HOURS
                           - SLEEP_HOURS_DISRUPTION_COEF * prior_sleep_dis
                           - SLEEP_HOURS_ACUTE_COEF * prior_cog
                           - SLEEP_HOURS_CHRONIC_COEF * chronic + random.gauss(0, 0.25))
            sleep_hours = clamp(sleep_hours, 3.5, 9.0)

            sleep_efficiency = (SLEEP_EFF_BASE
                                - SLEEP_EFF_DISRUPTION_COEF * prior_sleep_dis
                                - SLEEP_EFF_ACUTE_COEF * prior_cog
                                - SLEEP_EFF_CHRONIC_COEF * chronic + random.gauss(0, 1.8))
            sleep_efficiency = clamp(sleep_efficiency, 40.0, 98.0)

            sleep_performance = clamp(
                100.0 * (sleep_hours / SLEEP_NEED_HOURS) * (sleep_efficiency / 95.0), 0.0, 100.0)

            respiratory_rate = clamp(15.5 + 0.3 * prior_sleep_dis + random.gauss(0, 0.6), 11.0, 20.0)

            hrv_ratio = hrv / base["hrv"]
            hrv_score = clamp(100.0 * (hrv_ratio - HRV_SCORE_FLOOR_RATIO)
                              / (HRV_SCORE_CEIL_RATIO - HRV_SCORE_FLOOR_RATIO), 0.0, 100.0)
            rhr_ratio = rhr / base["rhr"]
            rhr_score = clamp(100.0 * (RHR_SCORE_WORST_RATIO - rhr_ratio)
                              / (RHR_SCORE_WORST_RATIO - RHR_SCORE_BEST_RATIO), 0.0, 100.0)
            recovery_score = round(clamp(
                0.45 * hrv_score + 0.30 * rhr_score + 0.25 * sleep_efficiency, 0.0, 100.0))

            strain = clamp(STRAIN_FLOOR + STRAIN_LOAD_COEF * today_cog + random.gauss(0, 0.8),
                           0.0, 21.0)

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
                "_acute_load": prior_cog,
                "_chronic_load": chronic,
            })
    return rows


# --------------------------------------------------------------------------------------
# Explainable stress score
# --------------------------------------------------------------------------------------


def compute_stress(row: dict) -> tuple[int, str]:
    """The agreed stress model - three terms, each one a thing a person can point at.

        stress = (100 - recovery_pct) * 0.55        recovery deficit  (max 55)
               + (day_strain / 21.0)  * 35          exertion          (max 35)
               + ((8 - min(sleep, 8)) / 8) * 10     sleep debt        (max 10)

    Bounded 0-100 by construction. `contributing_factors` names the terms that
    actually drove the number, largest first, so the score is never a black box -
    and HRV/RHR are reported alongside as corroborating context, since they are what
    moved recovery in the first place.
    """
    recovery_term = (100 - row["recovery_score"]) * 0.55
    strain_term = (row["strain"] / 21.0) * 35
    sleep_term = ((SLEEP_NEED_HOURS - min(row["total_sleep_hours"], SLEEP_NEED_HOURS))
                  / SLEEP_NEED_HOURS) * 10

    score = round(min(100.0, max(0.0, recovery_term + strain_term + sleep_term)))

    hrv_delta_pct = (row["hrv_rmssd_milli"] - row["_baseline_hrv"]) / row["_baseline_hrv"] * 100
    rhr_delta_pct = (row["resting_heart_rate"] - row["_baseline_rhr"]) / row["_baseline_rhr"] * 100

    contributions = {
        f"low recovery ({row['recovery_score']}%, HRV {hrv_delta_pct:+.0f}% / "
        f"RHR {rhr_delta_pct:+.0f}% vs personal baseline)": recovery_term,
        f"elevated day strain ({row['strain']:.1f} of 21)": strain_term,
        f"shortened sleep ({row['total_sleep_hours']:.1f}h vs {SLEEP_NEED_HOURS:.0f}h need)": sleep_term,
    }

    ranked = sorted(contributions.items(), key=lambda kv: kv[1], reverse=True)
    factors_str = "; ".join(label for label, points in ranked if points > 5.0)
    if not factors_str:
        factors_str = "no significant stress drivers"

    return score, factors_str


# --------------------------------------------------------------------------------------
# Self-verification of the encoded design
# --------------------------------------------------------------------------------------


def week_of(date_str: str) -> int:
    """0-3: which of the four Sun-Sat weeks this date falls in."""
    return (parse_date(date_str) - parse_date(START_DATE)).days // 7


def is_weekend(date_str: str) -> bool:
    return parse_date(date_str).weekday() > 4


def verify_design(biometrics: list[dict], stress: list[dict]) -> list[tuple[bool, str]]:
    """Assert the 4-week physiological arc the demo's trend charts depend on."""
    checks: list[tuple[bool, str]] = []
    features = load_meeting_features()
    dates = date_range(START_DATE, END_DATE)
    by_key = {(b["person_id"], b["date"]): b for b in biometrics}

    def avg(vals):
        return sum(vals) / len(vals) if vals else 0.0

    def rows_for(pid, week=None, workdays_only=False, weekends_only=False):
        out = [b for b in biometrics if b["person_id"] == pid]
        if week is not None:
            out = [b for b in out if week_of(b["date"]) == week]
        if workdays_only:
            out = [b for b in out if not is_weekend(b["date"])]
        if weekends_only:
            out = [b for b in out if is_weekend(b["date"])]
        return out

    def band(vals, lo, hi, label):
        m = avg(vals)
        return (lo <= m <= hi,
                f"{label}: mean {m:.1f} in [{lo}-{hi}]  (min {min(vals):.1f}, max {max(vals):.1f})")

    # --- window shape --------------------------------------------------------------
    checks.append((len(biometrics) == len(COHORT) * 28 and len(stress) == len(COHORT) * 28,
                   f"28 days x {len(COHORT)} people = {len(COHORT) * 28} rows in each artifact "
                   f"(biometrics {len(biometrics)}, stress {len(stress)})"))

    # --- user_101: the chronic burnout trajectory ----------------------------------
    w1 = [b["recovery_score"] for b in rows_for("user_101", week=0, workdays_only=True)]
    w2 = [b["recovery_score"] for b in rows_for("user_101", week=1, workdays_only=True)]
    checks.append(band(w1, 50, 65, "user_101 week 1 recovery"))
    checks.append(band(w2, 40, 50, "user_101 week 2 recovery"))

    # Weeks 3-4 red zone, measured on the mornings AFTER the Tue/Wed/Thu overload days.
    crunch_mornings = [b["recovery_score"] for b in biometrics
                       if b["person_id"] == "user_101" and week_of(b["date"]) >= 2
                       and parse_date(b["date"]).strftime("%A") in ("Wednesday", "Thursday", "Friday")]
    checks.append(band(crunch_mornings, 22, 35, "user_101 weeks 3-4 recovery (red zone)"))

    crunch_sleep = [b["total_sleep_hours"] for b in biometrics
                    if b["person_id"] == "user_101" and week_of(b["date"]) >= 2
                    and parse_date(b["date"]).strftime("%A") in ("Wednesday", "Thursday", "Friday")]
    checks.append((avg(crunch_sleep) < 5.5,
                   f"user_101 weeks 3-4 sleep falls below 5.5h (mean {avg(crunch_sleep):.2f}h, "
                   f"max {max(crunch_sleep):.2f}h)"))

    crunch_strain = [b["strain"] for b in biometrics
                     if b["person_id"] == "user_101" and week_of(b["date"]) >= 2
                     and parse_date(b["date"]).strftime("%A") in ("Tuesday", "Wednesday", "Thursday")]
    checks.append((min(crunch_strain) > 14.5,
                   f"user_101 weeks 3-4 day strain stays above 14.5 on every Tue-Thu "
                   f"(min {min(crunch_strain):.1f}, mean {avg(crunch_strain):.1f})"))

    weekly = [avg([b["recovery_score"] for b in rows_for("user_101", week=w, workdays_only=True)])
              for w in range(4)]
    checks.append((weekly[0] > weekly[1] > weekly[2],
                   "user_101 recovery declines monotonically across weeks 1->3 "
                   f"({' -> '.join(f'{v:.1f}' for v in weekly)})"))

    # --- the lag effect: the morning after heavy fragmentation ----------------------
    # Measured day-over-day: recovery on D+1 against recovery on D, for every day D
    # whose calendar carried more than 4 back-to-back transitions.
    drops = []
    for pid in COHORT:
        for i in range(len(dates) - 1):
            f = features.get((pid, dates[i]))
            if not f or f["back_to_back_count"] <= 4:
                continue
            today = by_key.get((pid, dates[i]))
            tomorrow = by_key.get((pid, dates[i + 1]))
            if today and tomorrow and today["recovery_score"] > 0:
                drops.append(100 * (today["recovery_score"] - tomorrow["recovery_score"])
                             / today["recovery_score"])
    mean_drop = avg(drops)
    checks.append((18 <= mean_drop <= 30,
                   f"day after >4 back-to-backs drops recovery {mean_drop:.1f}% "
                   f"(target 18-30%, n={len(drops)} instances)"))

    # --- controls ------------------------------------------------------------------
    for pid in ("user_102", "user_104"):
        checks.append(band([b["recovery_score"] for b in rows_for(pid)], 60, 85,
                           f"{pid} recovery (green/yellow all month)"))
        checks.append(band([b["hrv_rmssd_milli"] for b in rows_for(pid)], 55, 75,
                           f"{pid} HRV steady"))

    # --- baseline cohort -----------------------------------------------------------
    baseline_recovery = [b["recovery_score"] for pid in ("user_103", "user_105", "user_106")
                         for b in rows_for(pid, workdays_only=True)]
    checks.append(band(baseline_recovery, 42, 65, "baseline cohort workday recovery (yellow)"))

    # --- weekends rebound, but less so under accumulated debt -----------------------
    for pid in ("user_103", "user_105", "user_106"):
        wk = avg([b["recovery_score"] for b in rows_for(pid, weekends_only=True)])
        wd = avg([b["recovery_score"] for b in rows_for(pid, workdays_only=True)])
        checks.append((wk > wd, f"{pid} rebounds on weekends ({wd:.1f}% -> {wk:.1f}%)"))

    hs_rebound_w1 = avg([b["recovery_score"] for b in rows_for("user_101", week=0, weekends_only=True)])
    hs_rebound_w4 = avg([b["recovery_score"] for b in rows_for("user_101", week=3, weekends_only=True)])
    checks.append((hs_rebound_w4 < hs_rebound_w1 - 10,
                   "user_101's weekend rebound weakens as sleep debt accumulates "
                   f"(week 1 {hs_rebound_w1:.1f}% -> week 4 {hs_rebound_w4:.1f}%)"))

    # --- stress separation and the lag-1 correlation Part 3 keys on -----------------
    def stress_for(pid):
        return [s["stress_score"] for s in stress if s["person_id"] == pid]

    hs_avg = avg(stress_for("user_101"))
    bal_avg = avg(stress_for("user_102") + stress_for("user_104"))
    checks.append((hs_avg > bal_avg + 20,
                   f"user_101 avg stress ({hs_avg:.1f}) well above balanced cohort ({bal_avg:.1f})"))

    loads, recoveries = [], []
    for pid in COHORT:
        for i in range(1, len(dates)):
            row = by_key.get((pid, dates[i]))
            if row is not None:
                loads.append(cognitive_load(features.get((pid, dates[i - 1]))))
                recoveries.append(row["recovery_score"])
    corr = pearson(loads, recoveries)
    checks.append((corr < -0.3,
                   f"lag-1 correlation (prior-day load -> next-day recovery) = {corr:.2f}"))

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


# The agreed Part 1 -> Part 3 contract. The five biometric columns are carried here
# (not just in whoop_biometrics.csv) so every input to the stress formula sits on the
# same row as its output and the score can be re-derived from the file alone.
STRESS_CSV_FIELDS = ["person_id", "date", "hrv_ms", "rhr_bpm", "sleep_hours",
                     "recovery_pct", "day_strain", "stress_score", "contributing_factors"]


def write_stress_csv(rows: list[dict]) -> None:
    with open(STRESS_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=STRESS_CSV_FIELDS)
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
            "hrv_ms": row["hrv_rmssd_milli"],
            "rhr_bpm": row["resting_heart_rate"],
            "sleep_hours": row["total_sleep_hours"],
            "recovery_pct": row["recovery_score"],
            "day_strain": row["strain"],
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
    print("user_101 WEEK-BY-WEEK TRAJECTORY (workdays only)")
    print(f"  {'week':<22}{'recovery':>9}{'HRV':>8}{'RHR':>7}{'sleep':>8}{'strain':>8}{'stress':>8}")
    for w, label in enumerate(("1  Aug 23-29", "2  Aug 30-Sep 5", "3  Sep 6-12", "4  Sep 13-19")):
        week_rows = [b for b in biometrics
                     if b["person_id"] == "user_101" and week_of(b["date"]) == w
                     and not is_weekend(b["date"])]
        week_stress = [s["stress_score"] for s in stress_rows
                       if s["person_id"] == "user_101" and week_of(s["date"]) == w
                       and not is_weekend(s["date"])]
        n = len(week_rows)
        print(f"  {label:<22}"
              f"{sum(b['recovery_score'] for b in week_rows) / n:>8.1f}%"
              f"{sum(b['hrv_rmssd_milli'] for b in week_rows) / n:>8.1f}"
              f"{sum(b['resting_heart_rate'] for b in week_rows) / n:>7.0f}"
              f"{sum(b['total_sleep_hours'] for b in week_rows) / n:>8.2f}"
              f"{sum(b['strain'] for b in week_rows) / n:>8.1f}"
              f"{sum(week_stress) / len(week_stress):>8.1f}")
    print()
    print("SAMPLE - user_101, deepest crunch day")
    worst = min((s for s in stress_rows if s["person_id"] == "user_101"),
                key=lambda s: s["recovery_pct"])
    print(f"  {worst['date']}  recovery={worst['recovery_pct']}%  strain={worst['day_strain']}  "
          f"sleep={worst['sleep_hours']}h  stress={worst['stress_score']}")
    print(f"    drivers: {worst['contributing_factors']}")
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
