"""OpenAI client for the three insight agents.

Replaces the previous Gemini client. The public surface is unchanged, so the
agents, pipeline and API did not have to move: construct it, check `.available`,
call `.generate_json()`. If you need the Gemini implementation back it is in git
history at commit 6fbb18f.

Design constraints this file exists to satisfy:

* **Never raise.** Every agent must be safe to call when the key is missing, the
  quota is gone, or the network is down. Failures return None and the pipeline
  falls back to deterministic templates.
* **Cache to disk.** Keyed on sha256(model + prompt). Re-running the demo costs
  zero API calls, which matters when you are demoing on a metered key.
* **Back off, then fall back.** 429/503 retries with exponential backoff, then a
  chain of alternate models before giving up.

Stdlib only -- no `openai` package, no `requests`. One less install to go wrong
on a teammate's machine ten minutes before judging.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

__all__ = ["OpenAIClient", "LLMClient", "load_env", "repo_root", "parse_json_loose"]

_API_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_FALLBACK = "gpt-4o"
# Tried in order after the primary and fallback both fail. Keep this list short
# and only include models verified against the project key: a dead entry costs a
# wasted round trip on EVERY call, and the chain exists to save us during a rate
# limit, not to burn quota. (That is not hypothetical -- two dead models in the
# old Gemini chain turned 9 calls into 39.)
_EXTRA_FALLBACKS: tuple[str, ...] = ()
_BACKOFFS = (1.0, 2.0, 4.0)
_TIMEOUT_S = 60
_PLACEHOLDER_KEYS = ("your_key_here", "none", "changeme", "sk-...", "")


# --------------------------------------------------------------------------
# env loading (hand-rolled -- no python-dotenv dependency)
# --------------------------------------------------------------------------

def repo_root() -> Path:
    """Repo root, derived from this file's location (.../part3/src/insight/llm)."""
    here = Path(__file__).resolve()
    try:
        return here.parents[4]
    except IndexError:  # pragma: no cover - defensive
        return here.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    """Tiny KEY=VALUE parser. Skips blank lines and # comments."""
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            out[key] = value
    return out


_ENV_LOADED = False


def load_env(force: bool = False) -> None:
    """Populate os.environ from the repo-root .env and part3/.env.

    The real process environment always wins; .env files only fill gaps.
    part3/.env wins over the repo-root .env. Never raises, never prints values.
    """
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    _ENV_LOADED = True
    root = repo_root()
    merged: dict[str, str] = {}
    for candidate in (root / ".env", root / "part3" / ".env"):
        if candidate.is_file():
            merged.update(_parse_env_file(candidate))
    for key, value in merged.items():
        if not os.environ.get(key):
            os.environ[key] = value


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

class OpenAIClient:
    """Cached, backoff-wrapped OpenAI chat-completions client."""

    def __init__(self, offline: bool = False, cache_dir: Path | None = None) -> None:
        load_env()
        self.offline = bool(offline)
        self._api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        self.model = (os.environ.get("OPENAI_MODEL") or _DEFAULT_MODEL).strip()
        self.fallback_model = (
            os.environ.get("OPENAI_FALLBACK_MODEL") or _DEFAULT_FALLBACK
        ).strip()
        self.cache_dir = Path(cache_dir) if cache_dir else repo_root() / ".llm_cache"
        self.calls_made = 0
        self.cache_hits = 0
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        if self.offline:
            return False
        key = self._api_key.lower()
        return bool(self._api_key) and key not in _PLACEHOLDER_KEYS

    def fallback_chain(self) -> list[str]:
        """Models to try after the primary, de-duplicated, order preserved."""
        chain: list[str] = []
        for name in (self.fallback_model, *_EXTRA_FALLBACKS):
            if name and name != self.model and name not in chain:
                chain.append(name)
        return chain

    def stats(self) -> dict[str, Any]:
        return {
            "provider": "openai",
            "model": self.model,
            "fallback_model": self.fallback_model,
            "available": self.available,
            "calls_made": self.calls_made,
            "cache_hits": self.cache_hits,
            "last_error": self.last_error,
        }

    # -- cache ------------------------------------------------------------

    def _cache_path(self, model: str, prompt: str) -> Path:
        digest = hashlib.sha256(f"{model}\x00{prompt}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.txt"

    def _cache_read(self, model: str, prompt: str) -> str | None:
        try:
            return self._cache_path(model, prompt).read_text(encoding="utf-8")
        except OSError:
            return None

    def _cache_write(self, model: str, prompt: str, text: str) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(model, prompt).write_text(text, encoding="utf-8")
        except OSError:
            pass  # a cache miss is not worth failing a run over

    # -- transport --------------------------------------------------------

    def _post(self, model: str, prompt: str, expect_json: bool) -> tuple[str | None, int | None]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        }
        if expect_json:
            # Requires the literal word "json" somewhere in the prompt; every
            # agent prompt in this package says "Return ONLY this JSON object".
            payload["response_format"] = {"type": "json_object"}

        request = urllib.request.Request(
            _API_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        try:
            self.calls_made += 1
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                body = json.loads(response.read().decode("utf-8"))
            return _extract_text(body), 200
        except urllib.error.HTTPError as exc:
            self.last_error = f"HTTP {exc.code} ({model}): {_safe_error_detail(exc, self._api_key)}"
            return None, exc.code
        except Exception as exc:  # URLError, timeout, JSON decode -- anything
            self.last_error = type(exc).__name__
            return None, None

    # -- public API -------------------------------------------------------

    def generate(self, prompt: str, *, expect_json: bool = True,
                 max_retries: int = 3) -> str | None:
        """Return model text, or None on any failure. Cache-first, never raises."""
        if not prompt:
            return None

        cached = self._cache_read(self.model, prompt)
        if cached is not None:
            self.cache_hits += 1
            return cached

        if not self.available:
            return None

        attempts = max(1, min(int(max_retries), len(_BACKOFFS)))
        for index in range(attempts):
            text, status = self._post(self.model, prompt, expect_json)
            if text:
                self._cache_write(self.model, prompt, text)
                return text
            if status in (429, 503) or status is None:
                if index < attempts - 1:
                    time.sleep(_BACKOFFS[index])
                continue
            break  # 4xx that retrying cannot fix

        for model in self.fallback_chain():
            cached = self._cache_read(model, prompt)
            if cached is not None:
                self.cache_hits += 1
                return cached
            text, _ = self._post(model, prompt, expect_json)
            if text:
                self._cache_write(model, prompt, text)
                return text

        return None

    def generate_json(self, prompt: str, *, max_retries: int = 3
                      ) -> dict[str, Any] | list[Any] | None:
        text = self.generate(prompt, expect_json=True, max_retries=max_retries)
        if not text:
            return None
        return parse_json_loose(text)


# Provider-neutral alias. Downstream code should prefer this name so a future
# provider swap does not touch the agents again.
LLMClient = OpenAIClient


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _safe_error_detail(exc: urllib.error.HTTPError, api_key: str, limit: int = 180) -> str:
    """Error body with the key redacted. The key must never reach a log."""
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except Exception:
        detail = getattr(exc, "reason", "") or ""
    try:
        parsed = json.loads(detail)
        if isinstance(parsed, dict):
            detail = str(parsed.get("error", {}).get("message") or detail)
    except Exception:
        pass
    if api_key:
        detail = detail.replace(api_key, "***")
    return detail[:limit]


def _extract_text(payload: dict[str, Any]) -> str | None:
    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _strip_fences(text: str) -> str:
    out = text.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[-1] if "\n" in out else out[3:]
        if out.rstrip().endswith("```"):
            out = out.rstrip()[:-3]
    return out.strip()


def _slice_outermost(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start:end + 1]


def parse_json_loose(text: str) -> dict[str, Any] | list[Any] | None:
    """Parse JSON that may arrive fenced or wrapped in prose. None on failure."""
    if not text:
        return None
    candidate = _strip_fences(text)
    try:
        return json.loads(candidate)
    except Exception:
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        sliced = _slice_outermost(candidate, open_ch, close_ch)
        if sliced:
            try:
                return json.loads(sliced)
            except Exception:
                continue
    return None
