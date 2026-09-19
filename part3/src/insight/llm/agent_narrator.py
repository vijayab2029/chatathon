"""Agent 2: narration.

The narrator turns VALIDATED evidence into one short, human-readable insight
plus one schedulable action. It never sees raw calendar rows or daily stress
scores -- only `ValidatedPattern.evidence_dict()`, which is the complete set of
numbers it is permitted to cite.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..models import ValidatedPattern

if TYPE_CHECKING:  # pragma: no cover
    from .openai_client import LLMClient

__all__ = ["narrate", "build_prompt", "allowed_numbers_for"]


def allowed_numbers_for(
    patterns: list[ValidatedPattern],
    avg_stress: float | None = None,
) -> list[float]:
    """Every number the narrator is allowed to use -- feed this to the critic."""
    numbers: list[float] = []
    if avg_stress is not None:
        try:
            numbers.append(round(float(avg_stress), 1))
        except (TypeError, ValueError):
            pass
    for pattern in patterns or []:
        try:
            evidence = pattern.evidence_dict()
        except Exception:
            continue
        for value in evidence.values():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                numbers.append(float(value))
        # thresholds appear inside the rendered "pattern" string
        try:
            numbers.append(float(pattern.hypothesis.threshold))
            numbers.append(float(pattern.hypothesis.lag_days))
        except Exception:
            pass
    # de-duplicate, preserve order
    seen: set[float] = set()
    unique: list[float] = []
    for value in numbers:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def build_prompt(person_id: str, evidence: list[dict[str, Any]], avg_stress: float) -> str:
    payload = json.dumps(
        {"average_stress": round(float(avg_stress), 1), "validated_patterns": evidence},
        indent=2,
        default=str,
    )
    return f"""You are writing a short, private wellbeing insight for ONE person about THEIR
OWN synthetic calendar data. They are the only reader.

EVIDENCE (the ONLY facts and the ONLY numbers you may use):
{payload}

RULES -- all are mandatory:
1. Address the reader directly as "you". This is their own private data.
2. Blame the CALENDAR PATTERN, never the person. Say the schedule shape is the
   driver; never suggest they cope badly, lack resilience, or are the problem.
3. State findings as CORRELATION, not causation. Use wording like
   "tends to line up with", "is associated with", "days like this show".
4. Use NO medical, clinical, diagnostic or psychological language. Banned:
   burnout, burning out, anxiety, anxious, depressed, depression, disorder,
   unhealthy, mental health, diagnose, symptom, therapy, chronic.
5. Cite ONLY numbers that appear literally in the evidence above. Invent no
   percentages, counts, dates or scores. If unsure, use no number at all.
6. insight_text: ONE or TWO sentences, plain and warm, no jargon.
7. suggested_action: ONE concrete change they could put on a calendar this
   week (e.g. "block 30 minutes between your two afternoon reviews on
   Wednesday"). Must be specific and schedulable, not advice like "rest more".

Return ONLY this JSON object, no prose, no markdown fences:
{{"insight_text": "...", "suggested_action": "..."}}
"""


def narrate(
    client: "LLMClient",
    person_id: str,
    patterns: list[ValidatedPattern],
    avg_stress: float,
) -> dict[str, str] | None:
    """Return {"insight_text", "suggested_action"} or None on any failure."""
    if client is None or not getattr(client, "available", False):
        return None
    if not patterns:
        return None

    evidence: list[dict[str, Any]] = []
    for pattern in patterns:
        try:
            evidence.append(pattern.evidence_dict())
        except Exception:
            continue
    if not evidence:
        return None

    try:
        avg = float(avg_stress)
    except (TypeError, ValueError):
        avg = 0.0

    try:
        payload = client.generate_json(build_prompt(person_id, evidence, avg))
    except Exception:
        return None

    if isinstance(payload, list):
        payload = next((item for item in payload if isinstance(item, dict)), None)
    if not isinstance(payload, dict):
        return None

    insight = _clean_field(str(payload.get("insight_text") or ""))
    action = _clean_field(str(payload.get("suggested_action") or ""))

    # The model sometimes jams both fields into insight_text with a literal
    # "SUGGESTED ACTION:" header instead of filling the second key. Recover the
    # split rather than shipping the header to the employee.
    for marker in ("SUGGESTED ACTION:", "Suggested action:", "SUGGESTED_ACTION:"):
        if marker in insight:
            head, _, tail = insight.partition(marker)
            insight = _clean_field(head)
            action = action or _clean_field(tail)
            break

    if not insight or not action:
        return None
    return {"insight_text": insight, "suggested_action": action}


def _clean_field(text: str) -> str:
    """Strip stray field labels, markdown emphasis and quotes off a model string."""
    out = text.strip().strip('"').strip()
    for label in ("INSIGHT:", "Insight:", "INSIGHT_TEXT:", "insight_text:",
                  "SUGGESTED ACTION:", "Suggested action:", "suggested_action:"):
        if out.startswith(label):
            out = out[len(label):].strip()
    return out.strip("*").strip()
