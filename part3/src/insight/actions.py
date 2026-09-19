"""Manager-facing actions, resolved from a table. No LLM is in this path.

ALL DATA IN THIS SYSTEM IS SYNTHETIC.

WHY A TABLE AND NOT A MODEL
---------------------------

A model that can phrase an action can phrase a person into one. Asked to turn
"fragmentation is elevated for most of the team" into advice, a language model
will reach for "identify the team members with the most fragmented schedules"
without being asked to, because that is what management advice normally sounds
like. The privacy critic would then have to catch it every time, and a control
that has to win every round is not a control.

A lookup table cannot produce a sentence that is not already in it.

THE RULE EVERY ROW MUST SATISFY
-------------------------------

**The action must be executable without knowing who is affected.**

    "Require an agenda on every recurring invite."     -> needs no name
    "Check in with whoever is struggling."             -> needs a name

The second sentence is the surveillance failure mode in a single line of
well-meant advice, and it is the one thing this file exists to make
unsayable. This is design principle 5 (*recommend process, not people*) made
structural rather than aspirational.

Each row also carries a `verify_metric`: what to re-measure next cycle to see
whether the change worked. It is a team-level schedule metric by construction,
so verifying an action never means looking at a person either.
"""

from __future__ import annotations

__all__ = ["ACTIONS", "resolve_action"]


# (theme_id, severity_band) -> (action, verify_metric)
#
# Severity keys are the bands from models.SEVERITY_BANDS plus NO_SIGNAL.
# "minimal" and "low" share the maintain line: below "moderate" there is
# nothing worth reorganising a team's calendar over.
_MAINTAIN = "maintain"

ACTIONS: dict[tuple[str, str], tuple[str, str]] = {
    # ---------------------------------------------------------------- load --
    ("meeting_load", _MAINTAIN): (
        "No change needed. Keep the current meeting budget as the default.",
        "total minutes booked in meetings per person per day",
    ),
    ("meeting_load", "moderate"): (
        "Audit every recurring invite over 30 minutes and cancel or halve any "
        "that has no decision owner.",
        "total minutes booked in meetings per person per day",
    ),
    ("meeting_load", "elevated"): (
        "Set a team meeting budget -- a cap on booked hours per person per day "
        "-- and a standing no-meeting block that applies to everyone.",
        "total minutes booked in meetings per person per day",
    ),
    ("meeting_load", "high"): (
        "Freeze new recurring invites for two weeks and require each existing "
        "one to be re-justified before it resumes.",
        "total minutes booked in meetings per person per day",
    ),
    # ------------------------------------------------------- fragmentation --
    ("fragmentation", _MAINTAIN): (
        "No change needed. Keep default invite lengths as they are.",
        "longest uninterrupted gap during work hours",
    ),
    ("fragmentation", "moderate"): (
        "Switch the calendar default to 25- and 50-minute meetings so gaps "
        "exist without anyone having to ask for them.",
        "longest uninterrupted gap during work hours",
    ),
    ("fragmentation", "elevated"): (
        "Declare a protected focus block on the shared calendar -- a fixed "
        "half-day each week with no meetings -- and enforce it.",
        "longest uninterrupted gap during work hours",
    ),
    ("fragmentation", "high"): (
        "Consolidate status meetings into a single block and move every other "
        "recurring meeting out of the protected half-day.",
        "longest uninterrupted gap during work hours",
    ),
    # --------------------------------------------------------- after hours --
    ("after_hours", _MAINTAIN): (
        "No change needed. Keep working hours set on the shared calendar.",
        "meetings scheduled outside 08:00-18:00",
    ),
    ("after_hours", "moderate"): (
        "Turn on working-hours enforcement in the calendar so out-of-hours "
        "slots stop being offered when someone schedules.",
        "meetings scheduled outside 08:00-18:00",
    ),
    ("after_hours", "elevated"): (
        "Move every standing meeting inside core hours. Where a timezone makes "
        "that impossible, rotate which timezone absorbs the cost.",
        "meetings scheduled outside 08:00-18:00",
    ),
    ("after_hours", "high"): (
        "Stop scheduling outside core hours entirely, and reschedule the "
        "recurring meetings that currently sit there.",
        "meetings scheduled outside 08:00-18:00",
    ),
    # ------------------------------------------------------------- hygiene --
    ("meeting_hygiene", _MAINTAIN): (
        "No change needed. Keep the current agenda expectation.",
        "share of meetings that carry an agenda",
    ),
    ("meeting_hygiene", "moderate"): (
        "Require an agenda field on every recurring invite before it can be "
        "saved.",
        "share of meetings that carry an agenda",
    ),
    ("meeting_hygiene", "elevated"): (
        "Adopt a team norm that meetings over 30 minutes without an agenda can "
        "be declined without explanation, and say so in writing.",
        "share of meetings that carry an agenda",
    ),
    ("meeting_hygiene", "high"): (
        "Require an agenda on every invite, and cancel the standing meetings "
        "that cannot produce one.",
        "share of meetings that carry an agenda",
    ),
    # ------------------------------------------------------------ recovery --
    ("recovery_buffers", _MAINTAIN): (
        "No change needed. Keep the midday break protected.",
        "share of days with a protected 30-minute midday gap",
    ),
    ("recovery_buffers", "moderate"): (
        "Block a 30-minute midday break on the shared team calendar.",
        "share of days with a protected 30-minute midday gap",
    ),
    ("recovery_buffers", "elevated"): (
        "Make the midday block mandatory and reschedule the recurring meetings "
        "that currently collide with it.",
        "share of days with a protected 30-minute midday gap",
    ),
    ("recovery_buffers", "high"): (
        "Treat the midday block as unbookable in the calendar system rather "
        "than as a convention people can override.",
        "share of days with a protected 30-minute midday gap",
    ),
}

# Bands that resolve to the maintain row. Anything below "moderate" is not
# worth reorganising a calendar over, and `no signal` must still produce a
# sentence -- an empty cell would let a manager infer signal from the mere
# presence of a recommendation.
_MAINTAIN_BANDS = frozenset({"no signal", "minimal", "low"})


def resolve_action(theme_id: str, severity_band: str) -> tuple[str, str]:
    """Return ``(action, verify_metric)`` for a theme at a severity band.

    Unknown combinations fall back to the theme's maintain row rather than
    raising: a missing table entry must not be able to break an employer view
    mid-demo, and a maintain line is the safe thing to show when we do not
    know what to recommend.
    """
    key = _MAINTAIN if severity_band in _MAINTAIN_BANDS else severity_band
    row = ACTIONS.get((theme_id, key)) or ACTIONS.get((theme_id, _MAINTAIN))
    if row is None:
        return (
            "No change needed.",
            "team schedule structure",
        )
    return row
