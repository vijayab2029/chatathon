"""Safe-by-default k-anonymity gate for the employer-facing rollup.

See the design spec, section 3.1. Part 4 owns the authoritative policy and the
manager-facing language; this module exists so that the *accidental* path is
the safe one.

Why it is here at all, given Part 4 owns gating:

Run against the real Part 1/2 data, 8 of 9 patterns in the ungated rollup
covered exactly one person, and the adaptive cut points made them identifying
("days whose meetings average 6.83+ attendees"). Part 4 did not exist yet, and
`GET /team/correlations` was already documented as Part 5's employer feed. Any
wiring done before Part 4 landed would have shipped a k=1 employer view while
the pitch claimed individual data never reaches management.

Two conditions, both required:

    team-size floor   n_people_analysed  >= k     (a "team" of two is not a team)
    cell suppression  n_people_affected  >= k     (one person is not an aggregate)

The project design plan states only the first. On a six-person team the first
passes and all eight single-person patterns get published, which is why stating
only half the control is worse than stating none.

Suppression is always REPORTED, never silent. A judge asking "what are you not
showing me?" should get a number, not a shrug.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .models import TeamCorrelations

__all__ = ["DEFAULT_K", "apply_k_anonymity"]

DEFAULT_K = 5


def apply_k_anonymity(team: TeamCorrelations, k: int = DEFAULT_K) -> TeamCorrelations:
    """Return a gated copy of ``team``. The input is not modified.

    Patterns covering fewer than ``k`` distinct people are removed. If the team
    itself is below ``k``, every pattern is removed -- no employer view exists
    for a group that small.
    """
    patterns: list[dict[str, Any]] = list(team.patterns or [])
    total = len(patterns)

    if team.n_people_analysed < k:
        kept: list[dict[str, Any]] = []
        note = (
            f"Team has {team.n_people_analysed} people, below the k={k} floor. "
            f"No employer view is available for a group this small. "
            f"{total} pattern(s) withheld."
        )
    else:
        kept = [p for p in patterns if int(p.get("n_people_affected", 0)) >= k]
        withheld = total - len(kept)
        note = (
            f"k-anonymity gate applied at k={k}: {withheld} of {total} pattern(s) "
            f"withheld for covering fewer than {k} people. "
            f"Part 4 owns the authoritative policy; this is a safe default."
        )

    return replace(
        team,
        patterns=kept,
        gating_applied=True,
        gating_note=note,
    )
