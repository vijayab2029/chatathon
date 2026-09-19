#!/usr/bin/env python3
"""
PART 2 - Calendar ingestion, semantic extraction & structural analyzer.

Generates three artifacts in ./data/:

  A. synthetic_calendar.json       raw simulated events (SIMULATED DATA - not real people)
  B. meeting_features.json         per-person / per-day stress-relevant rollups  -> Part 3
  C. team_structural_summary.json  de-identified, team-wide schedule architecture -> Part 4

PRIVACY BARRIER (enforced, not just documented):
  A and B are per-person operational data. They stay private to the individual and are
  only ever joined against that same person's own biometrics in Part 3.
  C is the ONLY artifact that may travel upward to an employer view. It carries no
  person_id, no names, no attendee lists, no timestamps - only schedule architecture.
  The privacy barrier is verified at the end of this script by scanning the serialized
  summary for identifier patterns; the script exits non-zero if the scan fails.

ALL DATA IS SYNTHETIC. No real calendar, no real person, no real meeting was used.

Standard library only. Deterministic: random.seed(42).
Run:  python scripts/part2_calendar/analyze_calendar.py
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------

RANDOM_SEED = 42

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
SCRIPT_DIR = PROJECT_ROOT / "scripts" / "part2_calendar"

CALENDAR_PATH = DATA_DIR / "synthetic_calendar.json"
FEATURES_PATH = DATA_DIR / "meeting_features.json"
SUMMARY_PATH = DATA_DIR / "team_structural_summary.json"

START_DATE = "2026-08-23"  # Sunday
END_DATE = "2026-09-19"    # Saturday
# 28 days = exactly 4 Sun-Sat weeks, so week boundaries fall on real week boundaries.
# Weekday names are always derived from the real calendar, never hardcoded.
#
# WEEK ARC (index 0-3), driving the longitudinal trajectory the demo charts:
#   Week 1  2026-08-23 .. 2026-08-29   baseline load
#   Week 2  2026-08-30 .. 2026-09-05   escalation
#   Week 3  2026-09-06 .. 2026-09-12   crunch
#   Week 4  2026-09-13 .. 2026-09-19   crunch (sustained)
WEEK_STARTS = ["2026-08-23", "2026-08-30", "2026-09-06", "2026-09-13"]

COHORT = ["user_101", "user_102", "user_103", "user_104", "user_105", "user_106"]

# Feature thresholds (single source of truth for both per-person and team math).
BACK_TO_BACK_GAP_MIN = 10       # gap < 10 min => the two meetings are back-to-back
LUNCH_WINDOW_START = (11, 30)   # 11:30 AM
LUNCH_WINDOW_END = (14, 0)      # 2:00 PM
LUNCH_BUFFER_MIN = 30           # need one contiguous free block >= 30 min in that window
AFTER_HOURS_EARLIEST_START = (8, 30)   # start before 08:30 => after hours
AFTER_HOURS_LATEST_END = (17, 30)      # end after 17:30 => after hours

# "Healthy schedule" reference targets, used only to decide which structural metric
# deviates most and therefore names the primary bottleneck. These are demo heuristics,
# not empirical benchmarks, and are reported as such.
HEALTHY_TARGETS = {
    "back_to_back": 20.0,   # % of meetings that are back-to-back
    "lunch": 15.0,          # % of working days with no protected lunch block
    "agenda": 25.0,         # % of meetings with no agenda
    "after_hours": 5.0,     # % of meetings outside 08:30-17:30
}

# --------------------------------------------------------------------------------------
# Cohort profiles
# --------------------------------------------------------------------------------------
# Each profile controls agenda discipline and which weekday schedule templates are used.
# Templates are (start "HH:MM", duration_minutes) and are hand-built so the controlled
# variations in the spec are guaranteed, not left to chance.

PROFILES = {
    "user_101": {"cohort": "high_stress", "agenda_rate": 0.45},
    "user_102": {"cohort": "balanced", "agenda_rate": 1.00},
    "user_103": {"cohort": "baseline", "agenda_rate": 0.65},
    "user_104": {"cohort": "balanced", "agenda_rate": 1.00},
    "user_105": {"cohort": "baseline", "agenda_rate": 0.65},
    "user_106": {"cohort": "baseline", "agenda_rate": 0.65},
}

# --- user_101: the chronic burnout trajectory ------------------------------------------
# The whole point of this person is that the schedule degrades WEEK OVER WEEK. Nothing
# about their physiology is special in Part 1 - only this escalating calendar exposure is.
#
# Week 3/4 crunch: 5-7 meetings Tue-Thu, 0-5 minute transitions, the 11:30-2:00 window
# fully colonized, and late/early sessions. Several days exceed 4 back-to-back
# transitions, which is the trigger Part 1's lag effect is verified against.
HS_CRUNCH_TUE = [("08:15", 40), ("09:00", 60), ("10:00", 30), ("10:30", 45),
                 ("11:20", 70), ("12:35", 75), ("17:40", 50)]
HS_CRUNCH_WED = [("08:00", 30), ("08:30", 45), ("09:15", 60), ("10:20", 40),
                 ("11:05", 85), ("12:35", 70), ("17:45", 45)]
HS_CRUNCH_THU = [("09:30", 60), ("10:30", 45), ("11:15", 75), ("12:30", 60),
                 ("13:35", 55), ("18:00", 45)]
HS_CRUNCH_MON = [("09:00", 60), ("10:00", 45), ("11:30", 60), ("12:35", 70), ("16:30", 75)]
HS_CRUNCH_FRI = [("08:15", 45), ("09:00", 60), ("10:05", 40), ("11:45", 75), ("13:00", 50)]

# Week 2 escalation: 4-5 meetings/day, at least 2 back-to-back transitions every day,
# first after-hours session appears, and - the part that actually drives Part 1's week-2
# physiology - the midday window goes on Tue/Wed/Thu. Lunch loss is the single heaviest
# term in Part 1's cognitive_load (0.25), so it has to climb monotonically across the arc:
# 2 of 5 workdays in week 1 (inherited from the baseline templates), 3 of 5 here, then all
# 5 in the crunch weeks. Tue and Thu keep their meeting COUNT and stay at 2-3 back-to-back
# transitions - only the midday block is colonized, so week 2 reads as erosion, not crunch.
HS_W2_MON = [("09:00", 60), ("10:00", 30), ("10:35", 45), ("13:30", 60)]
HS_W2_TUE = [("09:30", 45), ("10:15", 60), ("11:20", 55), ("12:20", 75), ("16:00", 60)]
HS_W2_WED = [("09:00", 30), ("09:30", 60), ("10:35", 45), ("11:45", 60), ("12:50", 55)]
HS_W2_THU = [("09:00", 60), ("10:00", 45), ("11:00", 60), ("12:05", 90), ("17:45", 40)]
HS_W2_FRI = [("09:15", 45), ("10:00", 60), ("11:05", 40), ("14:00", 45)]

# --- user_102 / user_104: the balanced cohort ------------------------------------------
# 2-3 meetings/day, every gap >= 15 min, lunch window never fully consumed, agendas always.
BAL_A = [("09:00", 45), ("10:15", 60), ("14:00", 45)]
BAL_B = [("09:30", 60), ("11:00", 30), ("13:15", 60)]
BAL_C = [("10:00", 30), ("10:45", 45), ("15:00", 60)]
BAL_D = [("09:00", 60), ("14:30", 45)]
BAL_E = [("09:45", 45), ("11:00", 30), ("13:30", 45)]

# --- user_103 / user_105 / user_106: the baseline cohort -------------------------------
# 3-4 meetings/day, one back-to-back chain on most days, lunch lost roughly 2 days in 5.
BASE_A = [("09:00", 60), ("10:00", 30), ("11:30", 30), ("15:00", 60)]
BASE_B = [("09:30", 45), ("11:15", 75), ("12:35", 60), ("15:30", 45)]
BASE_C = [("10:00", 60), ("13:00", 45), ("16:45", 60)]
BASE_D = [("08:45", 60), ("11:00", 45), ("11:45", 60), ("13:00", 45)]
BASE_E = [("09:15", 45), ("10:00", 30), ("14:00", 60)]

# Weekday index 0=Mon .. 4=Fri -> template. Balanced/baseline users rotate the same
# template set by a per-person offset AND by week index, so no two colleagues share an
# identical week and no one repeats the same week four times. Their load stays flat by
# design - only user_101's exposure escalates.
WEEK_TEMPLATES = {
    "balanced": [BAL_A, BAL_B, BAL_C, BAL_D, BAL_E],
    "baseline": [BASE_A, BASE_B, BASE_C, BASE_D, BASE_E],
}

# user_101's four-week arc, one row per week (index 0-3), each Mon..Fri.
# Week 1 deliberately reuses the ordinary baseline load: the burnout is something the
# calendar does to this person over a month, not a trait they start with.
HIGH_STRESS_WEEKS = [
    [BASE_A, BASE_B, BASE_C, BASE_D, BASE_E],
    [HS_W2_MON, HS_W2_TUE, HS_W2_WED, HS_W2_THU, HS_W2_FRI],
    [HS_CRUNCH_MON, HS_CRUNCH_TUE, HS_CRUNCH_WED, HS_CRUNCH_THU, HS_CRUNCH_FRI],
    # Week 4 sustains the crunch but resequences it, so the fourth week is a second
    # bad week rather than a copy-paste of the third.
    [HS_CRUNCH_MON, HS_CRUNCH_THU, HS_CRUNCH_TUE, HS_CRUNCH_WED, HS_CRUNCH_FRI],
]

# Rotation offset per person (0 = no rotation). Keeps constraints intact because every
# template inside a cohort satisfies that cohort's constraints on its own.
ROTATION = {"user_101": 0, "user_102": 0, "user_103": 0, "user_104": 3, "user_105": 1, "user_106": 2}

# Weekends carry zero scheduled work meetings for everyone, across all four weeks.

# --------------------------------------------------------------------------------------
# Meeting title catalog (semantic layer)
# --------------------------------------------------------------------------------------
# agenda_bias shifts the person's base agenda rate: urgent/ad-hoc work loses agendas,
# ceremonies and client-facing sessions keep them. "Catch-up (no agenda)" is always
# agenda-less by construction.

TITLE_CATALOG = [
    # slot: "short" (<=30m), "medium" (31-60m), "long" (>60m), "offhours"
    {"title": "Daily Standup", "slot": "short", "attendees": (5, 9), "recurring": 0.95, "agenda_bias": 0.25},
    {"title": "1:1 Career Sync", "slot": "short", "attendees": (2, 2), "recurring": 0.85, "agenda_bias": 0.10},
    {"title": "Quick Sync: Deploy Window", "slot": "short", "attendees": (3, 6), "recurring": 0.20, "agenda_bias": -0.20},
    # agenda_bias -1.00 makes this title agenda-less by construction, so it is excluded
    # from profiles with perfect agenda discipline rather than silently overriding them.
    {"title": "Catch-up (no agenda)", "slot": "short", "attendees": (2, 4), "recurring": 0.30, "agenda_bias": -1.00},
    {"title": "Bug Bash Check-in", "slot": "short", "attendees": (4, 8), "recurring": 0.25, "agenda_bias": -0.10},
    {"title": "Sprint Retrospective", "slot": "medium", "attendees": (6, 10), "recurring": 0.90, "agenda_bias": 0.20},
    {"title": "Cross-team API Sync", "slot": "medium", "attendees": (5, 11), "recurring": 0.60, "agenda_bias": -0.10},
    {"title": "Design Review: Ingestion Pipeline", "slot": "medium", "attendees": (4, 9), "recurring": 0.30, "agenda_bias": 0.15},
    {"title": "Product/Eng Prioritization", "slot": "medium", "attendees": (5, 9), "recurring": 0.70, "agenda_bias": 0.10},
    {"title": "Urgent: Prod Incident Triage", "slot": "medium", "attendees": (4, 12), "recurring": 0.05, "agenda_bias": -0.45},
    {"title": "Vendor Integration Walkthrough", "slot": "medium", "attendees": (3, 7), "recurring": 0.15, "agenda_bias": 0.05},
    {"title": "Hiring Debrief", "slot": "medium", "attendees": (4, 6), "recurring": 0.25, "agenda_bias": 0.20},
    {"title": "Q3 Architecture Review & Backlog Triage", "slot": "long", "attendees": (6, 14), "recurring": 0.50, "agenda_bias": -0.05},
    {"title": "Sprint Planning", "slot": "long", "attendees": (6, 12), "recurring": 0.90, "agenda_bias": 0.20},
    {"title": "Client Steering Committee", "slot": "long", "attendees": (7, 15), "recurring": 0.65, "agenda_bias": 0.30},
    {"title": "Roadmap Workshop", "slot": "long", "attendees": (6, 12), "recurring": 0.20, "agenda_bias": 0.05},
    {"title": "Migration Cutover Dry Run", "slot": "long", "attendees": (5, 10), "recurring": 0.15, "agenda_bias": 0.00},
    {"title": "APAC Partner Sync", "slot": "offhours", "attendees": (4, 9), "recurring": 0.70, "agenda_bias": 0.00},
    {"title": "Late Escalation Review", "slot": "offhours", "attendees": (3, 8), "recurring": 0.10, "agenda_bias": -0.30},
    {"title": "Urgent: Prod Incident Triage", "slot": "offhours", "attendees": (4, 12), "recurring": 0.05, "agenda_bias": -0.45},
    {"title": "Early Global Ops Handoff", "slot": "offhours", "attendees": (4, 10), "recurring": 0.60, "agenda_bias": 0.05},
]


# --------------------------------------------------------------------------------------
# Small time helpers
# --------------------------------------------------------------------------------------

def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y-%m-%d")


def at(date_str: str, hhmm: str) -> datetime:
    return datetime.strptime(f"{date_str} {hhmm}", "%Y-%m-%d %H:%M")


def clock(day: datetime, hm: tuple[int, int]) -> datetime:
    return day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


def date_range(start: str, end: str) -> list[str]:
    d, last = parse_date(start), parse_date(end)
    out = []
    while d <= last:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def minutes_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60.0


# --------------------------------------------------------------------------------------
# Output A: synthetic calendar generation
# --------------------------------------------------------------------------------------

def slot_for(duration: int, start: datetime, end: datetime) -> str:
    if start < clock(start, AFTER_HOURS_EARLIEST_START) or end > clock(end, AFTER_HOURS_LATEST_END):
        return "offhours"
    if duration <= 30:
        return "short"
    if duration <= 60:
        return "medium"
    return "long"


def pick_title(slot: str, person_id: str, used_today: set[str]) -> dict:
    candidates = [t for t in TITLE_CATALOG if t["slot"] == slot]
    if PROFILES[person_id]["agenda_rate"] >= 1.0:
        # Perfect agenda discipline: this person never books the ad-hoc / agenda-less
        # meeting types at all, so the trait shows up in the titles and not just a flag.
        candidates = [t for t in candidates if t["agenda_bias"] > -0.3]
    fresh = [t for t in candidates if t["title"] not in used_today]
    return random.choice(fresh if fresh else candidates)


def is_after_hours(start: datetime, end: datetime) -> bool:
    return start < clock(start, AFTER_HOURS_EARLIEST_START) or end > clock(end, AFTER_HOURS_LATEST_END)


def week_index(date_str: str) -> int:
    """0-3: which of the four Sun-Sat weeks this date falls in."""
    return (parse_date(date_str) - parse_date(START_DATE)).days // 7


def templates_for(person_id: str) -> dict[str, list[tuple[str, int]]]:
    """Return {date_str: template} for this person across the whole 28-day window.

    Weekend dates are simply absent - nobody has scheduled work meetings on a
    Saturday or Sunday. (They still get a zero-load rollup row in Output B.)
    """
    profile = PROFILES[person_id]
    offset = ROTATION[person_id]
    plan: dict[str, list[tuple[str, int]]] = {}

    for date_str in date_range(START_DATE, END_DATE):
        weekday = parse_date(date_str).weekday()  # 0=Mon .. 6=Sun
        if weekday > 4:
            continue
        wk = week_index(date_str)
        if profile["cohort"] == "high_stress":
            week = HIGH_STRESS_WEEKS[wk]
            plan[date_str] = week[weekday]
        else:
            # Flat load, but rotated by person and by week so no week is a literal repeat.
            week = WEEK_TEMPLATES[profile["cohort"]]
            plan[date_str] = week[(weekday + offset + wk) % len(week)]

    return plan


def build_event(person_id: str, date_str: str, hhmm: str, duration: int, index: int) -> dict:
    start = at(date_str, hhmm)
    end = start + timedelta(minutes=duration)
    meta = pick_title(slot_for(duration, start, end), person_id, build_event.used_today)
    build_event.used_today.add(meta["title"])

    base_rate = PROFILES[person_id]["agenda_rate"]
    agenda_rate = base_rate + meta["agenda_bias"]
    draw = random.random() < max(0.0, min(1.0, agenda_rate))
    # A profile with perfect agenda discipline keeps it: title bias modulates imperfect
    # compliance, it does not manufacture a missing agenda for someone who always writes one.
    has_agenda = True if base_rate >= 1.0 else draw

    return {
        "event_id": f"evt_{person_id.split('_')[1]}_{date_str.replace('-', '')}_{index:02d}",
        "person_id": person_id,
        "date": date_str,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "duration_minutes": duration,
        "title": meta["title"],
        "has_agenda": has_agenda,
        "attendee_count": random.randint(*meta["attendees"]),
        "is_recurring": random.random() < meta["recurring"],
        "is_after_hours": is_after_hours(start, end),
    }


build_event.used_today = set()


def generate_calendar() -> list[dict]:
    events: list[dict] = []
    for person_id in COHORT:
        for date_str, blocks in sorted(templates_for(person_id).items()):
            build_event.used_today = set()
            ordered = sorted(blocks, key=lambda b: b[0])
            for i, (hhmm, duration) in enumerate(ordered, start=1):
                events.append(build_event(person_id, date_str, hhmm, duration, i))
    events.sort(key=lambda e: (e["person_id"], e["start_time"]))
    return events


# --------------------------------------------------------------------------------------
# Output B: per-person daily feature extraction
# --------------------------------------------------------------------------------------

def back_to_back_pairs(day_events: list[dict]) -> int:
    """Consecutive meetings whose gap is < BACK_TO_BACK_GAP_MIN minutes."""
    count = 0
    for prev, nxt in zip(day_events, day_events[1:]):
        gap = minutes_between(datetime.fromisoformat(prev["end_time"]),
                              datetime.fromisoformat(nxt["start_time"]))
        if gap < BACK_TO_BACK_GAP_MIN:
            count += 1
    return count


def back_to_back_event_ids(day_events: list[dict]) -> set[str]:
    """Every meeting that sits on at least one side of a back-to-back transition.

    Used only for the team-level `pct_meetings_back_to_back`, which asks what share of
    meetings are back-to-back (a 3-meeting chain = 3 affected meetings, 2 transitions).
    """
    affected: set[str] = set()
    for prev, nxt in zip(day_events, day_events[1:]):
        gap = minutes_between(datetime.fromisoformat(prev["end_time"]),
                              datetime.fromisoformat(nxt["start_time"]))
        if gap < BACK_TO_BACK_GAP_MIN:
            affected.add(prev["event_id"])
            affected.add(nxt["event_id"])
    return affected


def has_lunch_buffer(day_events: list[dict], date_str: str) -> bool:
    """True if some contiguous free block >= 30 min exists inside 11:30-14:00."""
    day = parse_date(date_str)
    win_start, win_end = clock(day, LUNCH_WINDOW_START), clock(day, LUNCH_WINDOW_END)

    busy: list[tuple[datetime, datetime]] = []
    for e in day_events:
        s = max(datetime.fromisoformat(e["start_time"]), win_start)
        t = min(datetime.fromisoformat(e["end_time"]), win_end)
        if s < t:
            busy.append((s, t))
    busy.sort()

    cursor = win_start
    for s, t in busy:
        if minutes_between(cursor, s) >= LUNCH_BUFFER_MIN:
            return True
        cursor = max(cursor, t)
    return minutes_between(cursor, win_end) >= LUNCH_BUFFER_MIN


def longest_stretch(day_events: list[dict]) -> int:
    """Longest uninterrupted chain of meetings (gaps < 10 min), summed by duration."""
    best = current = 0
    for i, e in enumerate(day_events):
        if i == 0:
            current = e["duration_minutes"]
        else:
            gap = minutes_between(datetime.fromisoformat(day_events[i - 1]["end_time"]),
                                  datetime.fromisoformat(e["start_time"]))
            current = current + e["duration_minutes"] if gap < BACK_TO_BACK_GAP_MIN else e["duration_minutes"]
        best = max(best, current)
    return best


def extract_features(events: list[dict]) -> list[dict]:
    """Daily rollups grouped by (person_id, date), one row per person per calendar day.

    Every person-day in the window gets a row, including meeting-free weekends, which
    emit an explicit zero-load record rather than being absent. Part 1 and Part 3 join
    on (person_id, date), so a dense grid means a rest day reads as a measured zero
    instead of a missing key.
    """
    grouped: dict[tuple[str, str], list[dict]] = {
        (person_id, date_str): []
        for person_id in COHORT
        for date_str in date_range(START_DATE, END_DATE)
    }
    for e in events:
        grouped.setdefault((e["person_id"], e["date"]), []).append(e)

    features = []
    for (person_id, date_str), day_events in sorted(grouped.items()):
        day_events.sort(key=lambda e: e["start_time"])
        total = len(day_events)
        no_agenda = sum(1 for e in day_events if not e["has_agenda"])
        features.append({
            "person_id": person_id,
            "date": date_str,
            "day_of_week": parse_date(date_str).strftime("%A"),
            "total_meetings": total,
            "total_meeting_minutes": sum(e["duration_minutes"] for e in day_events),
            "back_to_back_count": back_to_back_pairs(day_events),
            "no_lunch_buffer": not has_lunch_buffer(day_events, date_str),
            "after_hours_count": sum(1 for e in day_events if e["is_after_hours"]),
            # A meeting-free day has no meetings to lack an agenda, so both ratios are
            # 0.0 rather than undefined.
            "no_agenda_pct": round(no_agenda / total, 2) if total else 0.0,
            "avg_attendee_count": (
                round(sum(e["attendee_count"] for e in day_events) / total, 2) if total else 0.0),
            "longest_continuous_meeting_stretch_min": longest_stretch(day_events),
        })
    return features


# --------------------------------------------------------------------------------------
# Output C: de-identified team structural summary
# --------------------------------------------------------------------------------------

def hot_transition_window(events: list[dict]) -> str:
    """The 3-hour window holding the most back-to-back transitions, as a label.

    Derived from the data, not hardcoded. Transitions are counted at the moment one
    meeting ends and the next begins - an hour-of-day histogram, never a timestamp.
    """
    grouped: dict[tuple[str, str], list[dict]] = {}
    for e in events:
        grouped.setdefault((e["person_id"], e["date"]), []).append(e)

    hours: dict[int, int] = {}
    for day_events in grouped.values():
        day_events.sort(key=lambda e: e["start_time"])
        for prev, nxt in zip(day_events, day_events[1:]):
            end = datetime.fromisoformat(prev["end_time"])
            if minutes_between(end, datetime.fromisoformat(nxt["start_time"])) < BACK_TO_BACK_GAP_MIN:
                hours[end.hour] = hours.get(end.hour, 0) + 1

    if not hours:
        return "the working day"

    best_start = max(range(0, 22), key=lambda h: (sum(hours.get(x, 0) for x in range(h, h + 3)), -h))

    def label(h: int) -> str:
        suffix = "AM" if h < 12 else "PM"
        hour12 = h % 12 or 12
        return f"{hour12}:00 {suffix}"

    return f"{label(best_start)} - {label(best_start + 3)}"


def build_summary(events: list[dict], features: list[dict]) -> dict:
    days = len(date_range(START_DATE, END_DATE))
    total_meetings = len(events)
    # Denominator is WORKING person-days (>= 1 meeting), not all 168 person-days. A
    # meeting-free Sunday trivially "has a lunch buffer" and would dilute every rate
    # here toward a flattering number; rates are about how working days are shaped.
    # This also keeps the metric comparable to the 7-day summary Part 4 saw first.
    working_days = [f for f in features if f["total_meetings"] > 0]
    person_days = len(working_days)

    b2b_meetings = 0
    grouped: dict[tuple[str, str], list[dict]] = {}
    for e in events:
        grouped.setdefault((e["person_id"], e["date"]), []).append(e)
    for day_events in grouped.values():
        day_events.sort(key=lambda e: e["start_time"])
        b2b_meetings += len(back_to_back_event_ids(day_events))

    pct_b2b = round(100 * b2b_meetings / total_meetings, 1)
    pct_no_lunch = round(100 * sum(1 for f in working_days if f["no_lunch_buffer"]) / person_days, 1)
    pct_no_agenda = round(100 * sum(1 for e in events if not e["has_agenda"]) / total_meetings, 1)
    pct_after_hours = round(100 * sum(1 for e in events if e["is_after_hours"]) / total_meetings, 1)

    # Structural evaluation. The two bands named in the spec are the load-bearing ones;
    # the lower bands exist so the rating stays meaningful on a healthier team.
    if pct_b2b > 35:
        buffer_rating = "Critical"
    elif pct_b2b > 25:
        buffer_rating = "At Risk"
    elif pct_b2b > 15:
        buffer_rating = "Moderate"
    else:
        buffer_rating = "Healthy"

    if pct_no_lunch > 30:
        fragmentation = "High"
    elif pct_no_lunch > 20:
        fragmentation = "Moderate"
    elif pct_no_lunch > 10:
        fragmentation = "Low"
    else:
        fragmentation = "Minimal"

    # Primary bottleneck = the metric furthest above its healthy reference target.
    deviations = {
        "back_to_back": pct_b2b / HEALTHY_TARGETS["back_to_back"],
        "lunch": pct_no_lunch / HEALTHY_TARGETS["lunch"],
        "agenda": pct_no_agenda / HEALTHY_TARGETS["agenda"],
        "after_hours": pct_after_hours / HEALTHY_TARGETS["after_hours"],
    }
    driver = max(deviations, key=deviations.get)
    window = hot_transition_window(events)
    bottleneck = {
        "back_to_back": (
            f"Absence of transition buffers between sessions "
            f"({window}) - {pct_b2b}% of meetings start within "
            f"{BACK_TO_BACK_GAP_MIN} minutes of the previous one ending"
        ),
        "lunch": (
            f"Meetings consuming the protected midday window (11:30 AM - 2:00 PM) on "
            f"{pct_no_lunch}% of working days, leaving no contiguous "
            f"{LUNCH_BUFFER_MIN}-minute break"
        ),
        "agenda": (
            f"Meetings scheduled without a stated agenda ({pct_no_agenda}% of all "
            f"sessions), forcing preparation and context-switching cost onto attendees"
        ),
        "after_hours": (
            f"Sessions scheduled outside 08:30-17:30 ({pct_after_hours}% of all "
            f"meetings), eroding recovery time at the edges of the day"
        ),
    }[driver]

    return {
        "summary_window": {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "total_days_analyzed": days,
        },
        "team_size": len(COHORT),
        "metrics": {
            "avg_daily_meetings_per_person": round(total_meetings / person_days, 1),
            "avg_daily_meeting_minutes_per_person": round(
                sum(e["duration_minutes"] for e in events) / person_days, 1),
            "pct_meetings_back_to_back": pct_b2b,
            "pct_days_without_lunch_buffer": pct_no_lunch,
            "pct_meetings_without_agenda": pct_no_agenda,
            "pct_meetings_after_hours": pct_after_hours,
        },
        "structural_stress_profile": {
            "fragmentation_index": fragmentation,
            "buffer_compliance_rating": buffer_rating,
            "primary_calendar_bottleneck": bottleneck,
        },
        "privacy_audit": {
            "contains_pii": False,           # overwritten by the live scan below
            "contains_individual_schedules": False,
            "k_anonymity_status": (
                f"Passed (k={len(COHORT)})" if len(COHORT) >= 5
                else f"Blocked (k={len(COHORT)}, minimum 5)"
            ),
        },
        "data_provenance": "SIMULATED DATA - synthetically generated for demonstration; no real calendars.",
    }


# --------------------------------------------------------------------------------------
# Privacy scan
# --------------------------------------------------------------------------------------

FORBIDDEN_TOKENS = ["person_id", "user_", "event_id", "attendee", "employee_id", "@", "name"]
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def privacy_scan(summary: dict) -> tuple[bool, list[str]]:
    """Scan the serialized summary for identifier and per-person-schedule leakage."""
    blob = json.dumps(summary)
    lowered = blob.lower()
    hits = [tok for tok in FORBIDDEN_TOKENS if tok in lowered]
    hits += [pid for pid in COHORT if pid in lowered]
    if TIMESTAMP_RE.search(blob):
        hits.append("iso_timestamp")
    return (len(hits) == 0), sorted(set(hits))


# --------------------------------------------------------------------------------------
# Self-verification of the controlled variations
# --------------------------------------------------------------------------------------

def verify_cohort_design(features: list[dict]) -> list[tuple[bool, str]]:
    """Assert the 4-week arc the demo narrative depends on, week by week."""
    checks: list[tuple[bool, str]] = []
    WEEKEND = {"Saturday", "Sunday"}

    def rows(person_id, week=None, weekdays=None, workdays_only=False):
        out = [f for f in features if f["person_id"] == person_id]
        if week is not None:
            out = [f for f in out if week_index(f["date"]) == week]
        if weekdays is not None:
            out = [f for f in out if f["day_of_week"] in weekdays]
        if workdays_only:
            out = [f for f in out if f["day_of_week"] not in WEEKEND]
        return out

    def avg(vals):
        return sum(vals) / len(vals) if vals else 0.0

    # --- window shape -------------------------------------------------------------
    checks.append((len(features) == len(COHORT) * 28,
                   f"28 days x {len(COHORT)} people = {len(COHORT) * 28} person-day rollups "
                   f"(got {len(features)})"))
    weekend_rows = [f for f in features if f["day_of_week"] in WEEKEND]
    checks.append((all(f["total_meetings"] == 0 for f in weekend_rows),
                   f"zero scheduled work meetings on all {len(weekend_rows)} weekend person-days"))

    # --- user_101: chronic burnout trajectory --------------------------------------
    w1 = rows("user_101", week=0, workdays_only=True)
    checks.append((all(3 <= f["total_meetings"] <= 4 for f in w1),
                   "user_101 week 1 sits at a moderate 3-4 meetings/day"))

    w2 = rows("user_101", week=1, workdays_only=True)
    checks.append((all(4 <= f["total_meetings"] <= 5 for f in w2),
                   "user_101 week 2 escalates to 4-5 meetings/day"))
    checks.append((all(f["back_to_back_count"] >= 2 for f in w2),
                   "user_101 week 2 runs 2+ back-to-back transitions every day"))

    crunch = rows("user_101", week=2, weekdays={"Tuesday", "Wednesday", "Thursday"}) \
        + rows("user_101", week=3, weekdays={"Tuesday", "Wednesday", "Thursday"})
    checks.append((len(crunch) == 6 and all(5 <= f["total_meetings"] <= 7 for f in crunch),
                   "user_101 weeks 3-4 hit 5-7 meetings on every Tue/Wed/Thu"))
    checks.append((all(f["no_lunch_buffer"] for f in crunch),
                   "user_101 weeks 3-4 lose the lunch window on every Tue/Wed/Thu"))
    checks.append((all(f["back_to_back_count"] >= 4 for f in crunch),
                   "user_101 weeks 3-4 run 4+ back-to-back transitions on every Tue/Wed/Thu"))
    checks.append((sum(1 for f in crunch if f["back_to_back_count"] > 4) >= 4,
                   "user_101 has >4 back-to-back days for Part 1's lag effect to key on"))
    crunch_all = rows("user_101", week=2, workdays_only=True) + rows("user_101", week=3, workdays_only=True)
    checks.append((sum(f["after_hours_count"] for f in crunch_all) >= 6,
                   "user_101 weeks 3-4 carry repeated after-hours sessions "
                   f"({sum(f['after_hours_count'] for f in crunch_all)} total)"))

    escalation = [avg([f["total_meetings"] for f in rows("user_101", week=w, workdays_only=True)])
                  for w in range(4)]
    checks.append((escalation[0] < escalation[1] < escalation[2],
                   "user_101 meeting load escalates monotonically across weeks 1->3 "
                   f"({' -> '.join(f'{v:.1f}' for v in escalation)})"))

    # --- user_102 / user_104: sustainably paced controls ---------------------------
    for pid in ("user_102", "user_104"):
        bal = rows(pid, workdays_only=True)
        checks.append((all(2 <= f["total_meetings"] <= 3 for f in bal),
                       f"{pid} holds 2-3 meetings per day for all 4 weeks"))
        checks.append((all(f["back_to_back_count"] == 0 for f in bal),
                       f"{pid} never runs back-to-back (every gap >= 15 min)"))
        checks.append((all(not f["no_lunch_buffer"] for f in bal),
                       f"{pid} preserves a lunch block every working day"))
        checks.append((all(f["no_agenda_pct"] == 0.0 for f in bal),
                       f"{pid} has an explicit agenda on every meeting"))
        checks.append((all(f["after_hours_count"] == 0 for f in rows(pid)),
                       f"{pid} has zero after-hours meetings across the window"))

    # --- user_103 / user_105 / user_106: baseline mixed dynamics --------------------
    for pid in ("user_103", "user_105", "user_106"):
        base = rows(pid, workdays_only=True)
        checks.append((all(3 <= f["total_meetings"] <= 4 for f in base),
                       f"{pid} carries a typical 3-4 meeting/day load for all 4 weeks"))

    return checks


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------

def write_json(path: Path, payload) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def main() -> int:
    random.seed(RANDOM_SEED)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(SCRIPT_DIR, exist_ok=True)

    events = generate_calendar()
    features = extract_features(events)
    summary = build_summary(events, features)

    clean, hits = privacy_scan(summary)
    summary["privacy_audit"]["contains_pii"] = not clean
    summary["privacy_audit"]["contains_individual_schedules"] = not clean

    write_json(CALENDAR_PATH, events)
    write_json(FEATURES_PATH, features)
    write_json(SUMMARY_PATH, summary)

    bar = "=" * 78
    print(bar)
    print("PART 2 - CALENDAR INGESTION, SEMANTIC EXTRACTION & STRUCTURAL ANALYZER")
    print("ALL DATA IS SIMULATED. random.seed(%d) -> deterministic output." % RANDOM_SEED)
    print(bar)
    print(f"Window        : {START_DATE} ({parse_date(START_DATE):%A}) -> "
          f"{END_DATE} ({parse_date(END_DATE):%A})  [{len(date_range(START_DATE, END_DATE))} days]")
    print(f"Cohort        : {len(COHORT)} simulated people ({', '.join(COHORT)})")
    print()
    print("ARTIFACTS WRITTEN")
    print(f"  [A] {CALENDAR_PATH.relative_to(PROJECT_ROOT)}  -> {len(events):>4} events")
    working = sum(1 for f in features if f["total_meetings"] > 0)
    print(f"  [B] {FEATURES_PATH.relative_to(PROJECT_ROOT)}    -> {len(features):>4} person-day rollups "
          f"({len(COHORT)} people x {len(date_range(START_DATE, END_DATE))} days; "
          f"{working} working, {len(features) - working} meeting-free)")
    print(f"  [C] {SUMMARY_PATH.relative_to(PROJECT_ROOT)} -> {len(summary)} top-level keys")
    print()
    print("TEAM STRUCTURAL METRICS (de-identified, denominator = working person-days)")
    for key, value in summary["metrics"].items():
        print(f"  {key:<42} {value}")
    print(f"  {'fragmentation_index':<42} {summary['structural_stress_profile']['fragmentation_index']}")
    print(f"  {'buffer_compliance_rating':<42} {summary['structural_stress_profile']['buffer_compliance_rating']}")
    print(f"  bottleneck: {summary['structural_stress_profile']['primary_calendar_bottleneck']}")
    print()
    print("COHORT DESIGN VERIFICATION (controlled variations for Part 3 correlation)")
    design = verify_cohort_design(features)
    for ok, label in design:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print()
    print("PRIVACY BARRIER VERIFICATION - data/team_structural_summary.json")
    print(f"  scanned tokens : {', '.join(FORBIDDEN_TOKENS)}, ISO timestamps, all 6 person ids")
    if clean:
        print("  [PASS] zero instances of person_id / user_ / attendee lists / timestamps")
    else:
        print(f"  [FAIL] identifier leakage detected: {', '.join(hits)}")
    print(f"  [{'PASS' if summary['privacy_audit']['k_anonymity_status'].startswith('Passed') else 'FAIL'}]"
          f" k-anonymity: {summary['privacy_audit']['k_anonymity_status']}")
    print("  NOTE: [A] and [B] are per-person and stay private to the individual; only [C]"
          " may be shared upward.")
    print(bar)

    failures = [label for ok, label in design if not ok]
    if not clean or failures:
        print("RESULT: FAILED - see [FAIL] lines above.")
        return 1
    print("RESULT: OK - all three artifacts generated and verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
