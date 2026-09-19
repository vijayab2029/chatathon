"""Per-person-day feature extraction.

Turns one day's MeetingEvent list into the feature vector described by
FEATURE_VOCABULARY in models.py. Every key in that vocabulary is always
present in the output, so downstream code never has to guard for absence.
"""

from __future__ import annotations

from .models import FEATURE_VOCABULARY, MeetingEvent

WORK_START = 8 * 60      # 08:00
WORK_END = 18 * 60       # 18:00
LUNCH_START = 11 * 60    # 11:00
LUNCH_END = 14 * 60      # 14:00
BACK_TO_BACK_GAP = 5     # minutes
LUNCH_BUFFER = 30        # minutes
LARGE_MEETING_ATTENDEES = 8
NEGATIVE_SENTIMENT = -0.3


def parse_hhmm(value: str | None) -> int:
    """"HH:MM" -> minutes from midnight. Unparseable input yields 0."""
    if not value:
        return 0
    text = str(value).strip()
    # Tolerate "HH:MM:SS" and ISO-ish "...THH:MM".
    if "T" in text:
        text = text.split("T", 1)[1]
    parts = text.split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 0
    return hours * 60 + minutes


def _intervals(meetings: list[MeetingEvent]) -> list[tuple[int, int]]:
    """Meeting [start, end) spans in minutes, sorted and end-clamped."""
    spans = []
    for m in meetings:
        start = parse_hhmm(m.start)
        end = parse_hhmm(m.end)
        if end < start:  # malformed or overnight -- treat as zero-length
            end = start
        spans.append((start, end))
    return sorted(spans)


def _free_gaps(spans: list[tuple[int, int]], window_start: int, window_end: int) -> list[int]:
    """Lengths of uninterrupted free stretches inside a window."""
    gaps: list[int] = []
    cursor = window_start
    for start, end in spans:
        if end <= window_start or start >= window_end:
            continue
        start = max(start, window_start)
        if start > cursor:
            gaps.append(start - cursor)
        cursor = max(cursor, min(end, window_end))
    if window_end > cursor:
        gaps.append(window_end - cursor)
    return gaps


def _back_to_back_flags(meetings: list[MeetingEvent]) -> list[bool]:
    """Per-meeting back-to-back flag, in start order.

    Trusts the upstream `is_back_to_back` field when Part 2 set it; falls back
    to recomputing from the clock times with a <=5 min gap rule.
    """
    order = sorted(range(len(meetings)), key=lambda i: parse_hhmm(meetings[i].start))
    flags = [False] * len(meetings)
    prev_end: int | None = None
    for position, i in enumerate(order):
        m = meetings[i]
        declared = getattr(m, "is_back_to_back", None)
        if declared is None:
            start = parse_hhmm(m.start)
            flags[i] = position > 0 and prev_end is not None and (start - prev_end) <= BACK_TO_BACK_GAP
        else:
            flags[i] = bool(declared)
        prev_end = max(parse_hhmm(m.end), parse_hhmm(m.start))
    return [flags[i] for i in order]


def compute_day_features(meetings_for_that_day: list[MeetingEvent]) -> dict[str, float]:
    """Feature vector for one person-day. Always covers FEATURE_VOCABULARY."""
    features: dict[str, float] = {name: 0.0 for name in FEATURE_VOCABULARY}
    meetings = list(meetings_for_that_day or [])
    spans = _intervals(meetings)

    features["meeting_count"] = float(len(meetings))
    features["total_meeting_minutes"] = float(sum(end - start for start, end in spans))

    flags = _back_to_back_flags(meetings)
    features["back_to_back_blocks"] = float(sum(flags))

    longest = 0
    run = 0
    for flag in flags:
        # A flagged meeting continues the chain that its predecessor started.
        run = (run if run else 1) + 1 if flag else 0
        longest = max(longest, run)
    features["longest_back_to_back_run"] = float(longest)

    after_hours = 0
    for m in meetings:
        declared = getattr(m, "is_after_hours", None)
        if declared is None:
            start, end = parse_hhmm(m.start), parse_hhmm(m.end)
            after_hours += int(start < WORK_START or end > WORK_END)
        else:
            after_hours += int(bool(declared))
    features["after_hours_meetings"] = float(after_hours)

    features["no_agenda_meetings"] = float(sum(1 for m in meetings if not m.has_agenda))
    features["large_meetings"] = float(
        sum(1 for m in meetings if (m.attendee_count or 0) > LARGE_MEETING_ATTENDEES)
    )
    features["negative_sentiment_meetings"] = float(
        sum(1 for m in meetings if (m.sentiment or 0.0) < NEGATIVE_SENTIMENT)
    )
    features["recurring_meetings"] = float(sum(1 for m in meetings if m.is_recurring))

    lunch_gaps = _free_gaps(spans, LUNCH_START, LUNCH_END)
    features["has_lunch_buffer"] = 1.0 if any(g >= LUNCH_BUFFER for g in lunch_gaps) else 0.0

    work_gaps = _free_gaps(spans, WORK_START, WORK_END)
    features["focus_time_minutes"] = float(max(work_gaps)) if work_gaps else 0.0

    features["context_switches"] = float(len({m.category for m in meetings if m.category}))

    return features
