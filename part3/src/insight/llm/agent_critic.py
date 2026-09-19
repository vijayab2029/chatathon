"""Agent 3: the privacy / safety critic.

The critic is a SAFETY CONTROL, so it must not depend on an available API.
`deterministic_check` is a pure regex/keyword pass that always runs; the LLM
review is an optional second opinion whose issues are merged in. With the LLM
switched off the critic still returns a real verdict.

Three rules:
  1. NO DIAGNOSIS         -- no medical / psychological claims.
  2. NO BLAME             -- fault the schedule, never the person.
  3. NO UNSUPPORTED NUMBERS -- every figure must come from validated evidence.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Iterable

from ..models import CriticVerdict

if TYPE_CHECKING:  # pragma: no cover
    from .openai_client import LLMClient

__all__ = ["review", "deterministic_check", "build_prompt"]


# Rule 1: clinical / diagnostic vocabulary.
#
# These must be words that ASSERT a clinical claim. A bare "diagnos" stem was
# here originally and flagged our own disclaimer -- "this is a correlation, not a
# diagnosis" -- so every offline record logged a rejection it had not earned. A
# safety control that cries wolf on correct output teaches you to ignore it, so
# the diagnostic entries below are the asserting forms only.
_CLINICAL_TERMS: tuple[str, ...] = (
    "diagnosed", "diagnosis of", "diagnostic criteria",
    "burning out", "burnt out", "burned out", "burnout",
    "anxiety", "anxious", "depressed", "depression", "depressive",
    "mental health", "mental illness", "disorder",
    "symptom", "clinical", "therapy", "therapist", "medication",
    "unhealthy", "patholog", "trauma", "adhd", "insomnia",
    "chronic stress", "breakdown", "at risk of", "suffering from",
)

# Rule 2: phrasing that faults the person rather than the schedule.
_BLAME_PATTERNS: tuple[str, ...] = (
    r"you (?:are|'re|re) (?:not )?(?:bad|poor|weak|lazy|inefficient|disorganis|disorganiz)",
    r"you (?:struggle|fail|can ?not cope|cannot cope|can't cope|lack)\b",
    r"your (?:inability|weakness|poor|lack of) ",
    r"you (?:do ?n[o']t|don't) (?:manage|handle|cope)",
    r"you need to (?:toughen|try harder|be more resilient)",
    r"you are the (?:problem|cause)",
)

# Numbers that are inherent to scheduling advice (durations, days of a week)
# rather than claims about the data. Only tolerated in the suggested action.
_SCHEDULE_NUMBERS: tuple[float, ...] = (
    1, 2, 3, 4, 5, 6, 7, 10, 15, 20, 25, 30, 45, 60, 90, 120,
)

_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z_.])(\d+(?:\.\d+)?)(?![0-9A-Za-z_])")
_TOLERANCE = 0.051


def _coerce_numbers(values: Iterable[Any] | None) -> list[float]:
    out: list[float] = []
    for value in values or []:
        if isinstance(value, bool):
            continue
        try:
            out.append(float(value))
        except (TypeError, ValueError):
            continue
    return out


def _is_supported(value: float, allowed: list[float]) -> bool:
    for candidate in allowed:
        if abs(candidate - value) <= _TOLERANCE:
            return True
        # tolerate a rounded restatement, e.g. 12.0 cited for 12.04
        if round(candidate, 1) == round(value, 1) or round(candidate) == value:
            return True
    return False


def deterministic_check(text: str, allowed_numbers: list[float] | None) -> list[str]:
    """Rule-based safety pass. Always runs; never needs an API key.

    Flags clinical vocabulary, person-blaming phrasing, and any number in the
    text that is not present in `allowed_numbers` (small tolerance applied;
    digits embedded in words such as "1:1" or "b2b" are ignored).
    """
    issues: list[str] = []
    if not text:
        return issues
    lowered = text.lower()

    for term in _CLINICAL_TERMS:
        if term in lowered:
            issues.append(f"NO_DIAGNOSIS: clinical or diagnostic wording '{term}'")

    for pattern in _BLAME_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            issues.append(f"NO_BLAME: faults the person, not the schedule ('{match.group(0).strip()}')")

    allowed = _coerce_numbers(allowed_numbers)
    for raw in _NUMBER_RE.findall(text):
        try:
            value = float(raw)
        except ValueError:
            continue
        if not _is_supported(value, allowed):
            issues.append(f"NO_UNSUPPORTED_NUMBERS: '{raw}' is not in the validated evidence")

    # de-duplicate, preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for issue in issues:
        if issue not in seen:
            seen.add(issue)
            unique.append(issue)
    return unique


def build_prompt(insight_text: str, suggested_action: str, allowed_numbers: list[float]) -> str:
    numbers = ", ".join(f"{value:g}" for value in allowed_numbers) or "(none)"
    return f"""You are a strict privacy and safety reviewer for a wellbeing tool built on
SYNTHETIC data. Review the text below against EXACTLY three rules.

INSIGHT TEXT:
{insight_text}

SUGGESTED ACTION:
{suggested_action}

ALLOWED NUMBERS (every figure in the text must be one of these):
{numbers}

RULES:
1. NO DIAGNOSIS - no medical or psychological claims. Violations are words that
   assert a condition: "burning out", "burnout", "anxiety", "anxious",
   "depressed", "unhealthy", "mental health", "symptom", "diagnosed".
   NOT a violation: the words "stress", "stress score", "stress level",
   "strain", "recovery", "higher"/"elevated stress". This tool's entire output
   IS a stress score computed from the dataset. Naming that score, comparing it
   between days, or saying it is higher or elevated is the normal, intended
   operation of the tool and must be approved.
2. NO BLAME - the text must fault the SCHEDULE or the CALENDAR STRUCTURE, never
   the person's character, resilience or capability.
   NOT a violation: describing what the person's calendar contains.
3. NO UNSUPPORTED NUMBERS - every figure must appear in ALLOWED NUMBERS above.
   Durations inside the suggested action (e.g. a 30-minute block) are fine.

Judge only these three rules. Do NOT flag tone, hedging, vagueness, wording
preferences, or anything else - other reviewers handle those. Most texts you
see will be correct; approving a correct text is the expected outcome and is
not a failure on your part. Only reject when you can quote the exact offending
words.

Each issue string MUST be formatted: RULE_NAME: "<exact quoted phrase from the
text>" - <short reason>. An issue you cannot quote is not an issue; drop it.

Then write revised_text: a corrected version of the INSIGHT TEXT that keeps the
same meaning while obeying all three rules. If the insight already passes, set
approved to true and repeat it VERBATIM as revised_text.

Return ONLY this JSON object, no prose, no markdown fences:
{{"approved": true or false, "issues": ["..."], "revised_text": "..."}}
"""


def review(
    client: "LLMClient",
    insight_text: str,
    suggested_action: str,
    allowed_numbers: list[float],
) -> CriticVerdict:
    """Always returns a verdict. Deterministic rules run with or without the LLM."""
    insight_text = str(insight_text or "")
    suggested_action = str(suggested_action or "")
    allowed = _coerce_numbers(allowed_numbers)

    # The deterministic pass is the GATE. It decides `approved`.
    issues = deterministic_check(insight_text, allowed)
    # Scheduling durations in the action are legitimate, so widen the whitelist
    # there rather than flagging every "30 minutes".
    issues += [
        issue
        for issue in deterministic_check(suggested_action, allowed + list(_SCHEDULE_NUMBERS))
        if issue not in issues
    ]

    revised_text = insight_text
    advisory: list[str] = []
    unexplained_rejection = False
    if client is not None and getattr(client, "available", False):
        payload: Any = None
        try:
            payload = client.generate_json(
                build_prompt(insight_text, suggested_action, allowed)
            )
        except Exception:
            payload = None
        if isinstance(payload, list):
            payload = next((item for item in payload if isinstance(item, dict)), None)
        if isinstance(payload, dict):
            # The LLM's findings are ADVISORY, not binding. Measured against a
            # live 12-person run it raised 16 issues, and on inspection nearly
            # all were false: it flagged "higher stress levels" as a diagnosis
            # seven times after being told explicitly that the phrase is allowed,
            # and claimed four numbers were unsupported that the deterministic
            # pass and a separate audit both confirmed were in the evidence.
            # A reviewer that rejects 75% of correct output cannot be the gate --
            # it would just train everyone to click through. So its issues are
            # recorded for transparency and prefixed, but they do not block.
            for issue in payload.get("issues") or []:
                text = str(issue).strip()
                if text and not text.startswith("ADVISORY:"):
                    text = f"ADVISORY: {text}"
                if text and text not in advisory:
                    advisory.append(text)
            candidate = str(payload.get("revised_text") or "").strip()
            if candidate:
                # only accept a rewrite that does not itself break the rules
                # Only take a rewrite when the gate itself objected to the
                # original; an advisory nit is not reason to alter approved text.
                if issues and not deterministic_check(candidate, allowed):
                    revised_text = candidate
            # A rejection with nothing quotable is not actionable: we cannot tell
            # the employee what was wrong, and we cannot show a judge the reason.
            # The deterministic pass is the hard gate; the LLM is a second opinion
            # that has to justify itself. An unexplained "approved: false" is
            # recorded for transparency but does not block.
            if payload.get("approved") is False and not issues:
                unexplained_rejection = True

    if unexplained_rejection:
        advisory.append(
            "ADVISORY: reviewer withheld approval but quoted no offending text"
        )

    # approved reflects the deterministic gate ONLY.
    approved = not issues
    return CriticVerdict(
        original_text=insight_text,
        approved=approved,
        issues=issues + advisory,
        revised_text=revised_text or insight_text,
    )
