"""LLM layer for Part 3.

Three narrow agents over one stdlib-only, cache-first Gemini client:

    agent_hypothesis.propose_hypotheses -> testable claims (validated elsewhere)
    agent_narrator.narrate              -> prose from validated evidence only
    agent_critic.review                 -> safety gate (works with the LLM off)

Every public function here is safe to call with `client.available == False`.
"""

from __future__ import annotations

from .agent_critic import deterministic_check, review
from .agent_hypothesis import propose_hypotheses
from .agent_narrator import allowed_numbers_for, narrate
from .gemini import GeminiClient, load_env

__all__ = [
    "GeminiClient",
    "load_env",
    "propose_hypotheses",
    "narrate",
    "allowed_numbers_for",
    "review",
    "deterministic_check",
]
