"""Agent 1: hypothesis generation.

The LLM proposes *claims to test*, never conclusions. Everything it emits is
forced through the Hypothesis DSL and then validated statistically elsewhere.
The prompt deliberately carries only summary statistics -- never raw day rows --
to keep it small, cheap and privacy-preserving.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..models import FEATURE_VOCABULARY, OPERATORS, Hypothesis

if TYPE_CHECKING:  # pragma: no cover
    from .openai_client import LLMClient

__all__ = ["propose_hypotheses", "build_prompt"]

_MAX_REQUESTED = 12


def build_prompt(person_id: str, timeline_digest: dict[str, Any], n: int) -> str:
    """Compact prompt: the vocabulary, the operators, and this person's stats."""
    vocabulary = "\n".join(
        f"  - {name}: {description}" for name, description in FEATURE_VOCABULARY.items()
    )
    operators = " ".join(OPERATORS)
    try:
        digest = json.dumps(timeline_digest, indent=2, default=str, sort_keys=True)
    except (TypeError, ValueError):
        digest = str(timeline_digest)

    return f"""You are a careful data analyst generating TESTABLE HYPOTHESES about how a
person's calendar structure relates to their daily stress score (0-100).
All data is SYNTHETIC. You do not draw conclusions -- a deterministic
statistical validator will test each hypothesis you propose.

PERSON: {person_id}

SUMMARY STATISTICS FOR THIS PERSON (the only data you get):
{digest}

ALLOWED FEATURES (use the exact name, nothing else):
{vocabulary}

ALLOWED OPERATORS: {operators}
ALLOWED lag_days VALUES: 0 (same day), 1 (previous day), 2 (two days earlier)

RULES:
1. Propose exactly {n} DIFFERENT hypotheses. Vary the feature, the operator and
   the lag -- do not submit {n} variations of the same idea.
2. Choose a threshold that will ACTUALLY PARTITION THIS PERSON'S DATA given the
   statistics above. A threshold above the observed maximum, or below the
   observed minimum, matches every day or no day and is worthless. Aim for a
   value between the mean and the max (or between min and mean for "<" style
   operators) so both groups have days in them.
3. "threshold" must be a plain number. "lag_days" must be 0, 1 or 2.
4. "rationale" is one short sentence about the SCHEDULE, not about the person's
   character, health or ability.
5. Make no medical or psychological claims.

Return ONLY a JSON array, no prose, no markdown fences. Each element:
{{"feature": str, "operator": str, "threshold": number, "lag_days": int, "rationale": str}}
"""


def propose_hypotheses(
    client: "LLMClient",
    person_id: str,
    timeline_digest: dict[str, Any],
    n: int = 6,
) -> list[Hypothesis]:
    """Ask the LLM for hypotheses; return only shape-valid ones.

    Returns [] if the client is unavailable, the call fails, or nothing
    validates. The caller always supplies baseline hypotheses regardless, so an
    empty list is a normal, non-exceptional outcome.
    """
    # Lazy import: hypotheses.py is owned by another agent and may not exist yet.
    try:
        from ..hypotheses import validate_hypothesis_shape
    except Exception:
        return []

    if client is None or not getattr(client, "available", False):
        return []

    try:
        n = max(1, min(int(n), _MAX_REQUESTED))
    except (TypeError, ValueError):
        n = 6

    try:
        payload = client.generate_json(build_prompt(person_id, timeline_digest or {}, n))
    except Exception:
        return []

    if payload is None:
        return []
    if isinstance(payload, dict):
        # tolerate {"hypotheses": [...]} style wrappers
        for key in ("hypotheses", "results", "items", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        return []

    out: list[Hypothesis] = []
    seen: set[tuple[str, str, float, int]] = set()
    for raw in payload[: _MAX_REQUESTED * 2]:
        if not isinstance(raw, dict):
            continue
        try:
            hypothesis = validate_hypothesis_shape(raw)
        except Exception:
            continue
        if hypothesis is None:
            continue
        key = (
            hypothesis.feature,
            hypothesis.operator,
            float(hypothesis.threshold),
            int(hypothesis.lag_days),
        )
        if key in seen:
            continue
        seen.add(key)
        try:
            hypothesis.source = "llm"
        except Exception:
            pass
        out.append(hypothesis)
        if len(out) >= n:
            break
    return out
