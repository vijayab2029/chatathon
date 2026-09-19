"""The employer view: five fixed schedule themes, bands, and actions.

ALL DATA IN THIS SYSTEM IS SYNTHETIC.

WHY THE EMPLOYER'S UNIT OF RECORD IS NOT A PATTERN
--------------------------------------------------

`team_correlations.json` reports patterns. Patterns are the right unit for
Part 4, which needs to reason about the analysis. They are the wrong unit for
a manager, for a reason that survives every gate applied to the rows
themselves: **which patterns appear, and how many, varies with the data.**

That variation is a channel. Eight rows sharing `lag_days: 1` with near
identical lifts (19.6, 19.6, 19.0, 16.4) is one person's week enumerated eight
ways, and no amount of scrubbing the individual rows removes the shape of the
list they sit in.

So the employer view emits a CONSTANT list: the same five themes, in the same
order, for every team, on every run, including themes reading `no signal`. Set
membership then carries no information, because the set never changes.

Everything else follows from that decision:

  * no thresholds tied to magnitudes, no lift points, no people counts --
    `max_lift_points` in particular is ALWAYS one identifiable person's number,
    since the maximum of a set is a member of it at every value of k
  * severity and prevalence are strings from closed vocabularies, so there is
    no number for a UI to plot
  * no dates beyond the team-level range, so there is no per-day axis

That last set of absences is also what keeps the employer SCREEN from
resembling the employee screen. Part 5 cannot render a stress timeline from
this payload because the payload contains no timeline -- which is a stronger
guarantee than asking Part 5 not to draw one.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .actions import resolve_action
from .models import NO_SIGNAL, TeamCorrelations, TeamTheme, TeamThemeView

__all__ = [
    "THEME_ORDER",
    "THEME_LABELS",
    "FEATURE_THEME",
    "prevalence_band",
    "roll_up_themes",
]


# The closed vocabulary. Fixed order so two runs are comparable and a diff of
# two employer views shows content changes, never ordering noise.
THEME_ORDER: tuple[str, ...] = (
    "meeting_load",
    "fragmentation",
    "after_hours",
    "meeting_hygiene",
    "recovery_buffers",
)

THEME_LABELS: dict[str, str] = {
    "meeting_load": "Meeting load",
    "fragmentation": "Fragmentation & focus",
    "after_hours": "After-hours encroachment",
    "meeting_hygiene": "Meeting hygiene",
    "recovery_buffers": "Recovery buffers",
}

# Every feature in FEATURE_VOCABULARY maps to exactly one theme. A feature with
# no mapping would silently vanish from the employer view, so tests assert the
# map is total.
FEATURE_THEME: dict[str, str] = {
    "meeting_count": "meeting_load",
    "total_meeting_minutes": "meeting_load",
    "large_meetings": "meeting_load",
    "avg_attendee_count": "meeting_load",
    "back_to_back_blocks": "fragmentation",
    "longest_back_to_back_run": "fragmentation",
    "context_switches": "fragmentation",
    "focus_time_minutes": "fragmentation",
    "longest_meeting_stretch_min": "fragmentation",
    "after_hours_meetings": "after_hours",
    "no_agenda_meetings": "meeting_hygiene",
    "recurring_meetings": "meeting_hygiene",
    "negative_sentiment_meetings": "meeting_hygiene",
    "has_lunch_buffer": "recovery_buffers",
}

# Fallback sentences for a theme with nothing to report. Written so that a
# `no signal` row reads as a real statement rather than a blank, because a
# blank invites the reader to wonder what was removed.
_NO_SIGNAL_FACTS: dict[str, str] = {
    "meeting_load": "No meeting-volume pattern reached the reporting threshold for this team.",
    "fragmentation": "No schedule-fragmentation pattern reached the reporting threshold for this team.",
    "after_hours": "No out-of-hours scheduling pattern reached the reporting threshold for this team.",
    "meeting_hygiene": "No meeting-hygiene pattern reached the reporting threshold for this team.",
    "recovery_buffers": "No midday-recovery pattern reached the reporting threshold for this team.",
}


def prevalence_band(n_affected: int, n_analysed: int) -> str:
    """Coarse reach, never a count.

    A raw "5 of 12", combined with what a manager already knows about who is
    busy, narrows the field further than a band does.
    """
    if n_affected <= 0 or n_analysed <= 0:
        return "none"
    fraction = n_affected / n_analysed
    if fraction < 0.34:
        return "some"
    if fraction <= 0.66:
        return "about half"
    return "most"


def _severity_from_lift(mean_lift: float) -> str:
    """Same band edges as emit._severity_band, on a 0-100 stress scale."""
    a = abs(mean_lift)
    if a < 3:
        return "minimal"
    if a < 7:
        return "low"
    if a < 12:
        return "moderate"
    if a < 20:
        return "elevated"
    return "high"


def _pick(patterns: list[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The most representative pattern: widest reach, then strongest effect."""
    if not patterns:
        return None
    return max(
        patterns,
        key=lambda p: (
            int(p.get("n_people_affected", 0)),
            abs(float(p.get("mean_lift_points", 0.0))),
        ),
    )


def roll_up_themes(
    team: TeamCorrelations,
    *,
    below_floor: bool = False,
) -> TeamThemeView:
    """Project gated team patterns onto the five fixed themes.

    ``team`` must ALREADY be k-gated -- this function does not suppress
    anything, it only reshapes. Passing it an ungated rollup would publish
    single-person patterns as themes. `emit.write_outputs` is the only caller
    and passes the gated artifact.

    Every theme in THEME_ORDER is emitted, in order, whatever the input.
    """
    by_theme: dict[str, list[Mapping[str, Any]]] = {t: [] for t in THEME_ORDER}
    for pattern in (team.patterns or []):
        theme_id = FEATURE_THEME.get(str(pattern.get("feature", "")))
        if theme_id is None:
            continue  # unmapped feature: better absent than mis-filed
        by_theme[theme_id].append(pattern)

    themes: list[dict[str, Any]] = []
    for theme_id in THEME_ORDER:
        patterns = [] if below_floor else by_theme[theme_id]

        # Direction matters. focus_time_minutes carries a NEGATIVE lift -- more
        # focus time, less strain -- and banding on abs(lift) would report a
        # protective finding as a severity, telling a manager to intervene
        # against the thing that is helping.
        risk = [p for p in patterns if float(p.get("mean_lift_points", 0.0)) > 0]
        protective = [p for p in patterns if float(p.get("mean_lift_points", 0.0)) < 0]

        if risk:
            # Weight by reach, so a pattern that cleared k by one person cannot
            # outweigh one that covers the whole team.
            weights = [max(1, int(p.get("n_people_affected", 0))) for p in risk]
            lifts = [float(p.get("mean_lift_points", 0.0)) for p in risk]
            weighted = sum(w * l for w, l in zip(weights, lifts)) / sum(weights)
            severity = _severity_from_lift(weighted)
            reach = max(int(p.get("n_people_affected", 0)) for p in risk)
        else:
            severity = NO_SIGNAL
            reach = 0

        lead = _pick(risk)
        guard = _pick(protective)

        action, verify_metric = resolve_action(theme_id, severity)
        themes.append(
            TeamTheme(
                theme_id=theme_id,
                label=THEME_LABELS[theme_id],
                severity_band=severity,
                prevalence_band=prevalence_band(reach, team.n_people_analysed),
                calendar_fact=(
                    str(lead.get("calendar_fact", "")) if lead
                    else _NO_SIGNAL_FACTS[theme_id]
                ),
                protective_fact=(
                    str(guard.get("calendar_fact", "")) if guard else None
                ),
                action=action,
                verify_metric=verify_metric,
            ).to_dict()
        )

    if below_floor:
        note = (
            f"Team has {team.n_people_analysed} people, below the reporting "
            f"floor. No employer view is available for a group this small. "
            f"The themes below are shown at 'no signal' so that a team too "
            f"small to report on stays distinguishable from a team with "
            f"nothing to report."
        )
    else:
        note = team.gating_note

    return TeamThemeView(
        n_people_analysed=team.n_people_analysed,
        date_range=team.date_range,
        themes=themes,
        below_floor=below_floor,
        gating_note=note,
    )
