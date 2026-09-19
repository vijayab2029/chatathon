"""Minimal, dependency-free Gemini client for the insight agents.

Design constraints (hackathon, FREE Gemini tier: ~10 req/min, ~250/day):
  * stdlib only -- no `requests`, no `google-generativeai`.
  * every failure path returns None; a live demo must never crash on a 429.
  * every successful response is cached to disk keyed on (model, prompt), so
    re-running the demo costs ZERO API calls.

ALL DATA IN THIS SYSTEM IS SYNTHETIC.
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

__all__ = ["GeminiClient", "load_env", "repo_root", "parse_json_loose"]

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_DEFAULT_MODEL = "gemini-2.5-flash"
_DEFAULT_FALLBACK = "gemini-3.5-flash-lite"
# Verified 2026-09-19 against the live models.list endpoint: the documented
# default fallback (gemini-2.5-flash-lite) now 404s for newer API keys --
# "no longer available to new users". Since the fallback IS the rate-limit
# escape hatch, we try these known-good cheap models after it rather than
# letting the demo die on a 429.
# Verified live on 2026-09-19 against our key: gemini-2.5-flash and
# gemini-3.5-flash-lite return 200. gemini-2.5-flash-lite 404s (retired for new
# keys) and gemini-3.8-flash 429s (not on our free quota) -- both are deliberately
# absent, since a dead model in the chain costs a wasted HTTP round trip on every
# single call and the chain exists to SAVE us during a rate limit, not burn quota.
_EXTRA_FALLBACKS = ("gemini-2.5-flash", "gemini-flash-lite-latest")
_BACKOFFS = (1.0, 2.0, 4.0)
_TIMEOUT_S = 60
_PLACEHOLDER_KEYS = ("your_key_here", "none", "changeme", "")


# --------------------------------------------------------------------------
# env loading (hand-rolled -- no python-dotenv dependency)
# --------------------------------------------------------------------------

def repo_root() -> Path:
    """Repo root, derived from this file's location (.../part3/src/insight/llm)."""
    here = Path(__file__).resolve()
    # llm -> insight -> src -> part3 -> <repo root>
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
    """Populate os.environ from part3/.env (and the repo-root .env, if any).

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

class GeminiClient:
    """Thin REST wrapper with a disk cache and graceful degradation."""

    def __init__(self, offline: bool = False, cache_dir: Path | None = None) -> None:
        load_env()
        self.offline = bool(offline)
        self.model = os.environ.get("GEMINI_MODEL") or _DEFAULT_MODEL
        self.fallback_model = os.environ.get("GEMINI_FALLBACK_MODEL") or _DEFAULT_FALLBACK
        self._api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
        self.cache_dir = Path(cache_dir) if cache_dir else (repo_root() / ".llm_cache")
        self.calls_made = 0
        self.cache_hits = 0
        self.last_error: str | None = None

    # -- state ------------------------------------------------------------

    @property
    def available(self) -> bool:
        """False when offline or when no usable API key is configured."""
        if self.offline:
            return False
        return self._api_key.lower() not in _PLACEHOLDER_KEYS

    def fallback_chain(self) -> list[str]:
        """Cheap models to try, in order, once the primary has given up."""
        chain: list[str] = []
        for model in (self.fallback_model, *_EXTRA_FALLBACKS):
            if model and model != self.model and model not in chain:
                chain.append(model)
        return chain

    def stats(self) -> dict[str, Any]:
        """Demo-friendly counters. Deliberately contains no key material."""
        return {
            "model": self.model,
            "fallback_model": self.fallback_model,
            "available": self.available,
            "offline": self.offline,
            "calls_made": self.calls_made,
            "cache_hits": self.cache_hits,
            "last_error": self.last_error,
        }

    # -- cache ------------------------------------------------------------

    def _cache_path(self, model: str, prompt: str) -> Path:
        digest = hashlib.sha256(f"{model}\n{prompt}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.txt"

    def _cache_read(self, model: str, prompt: str) -> str | None:
        try:
            path = self._cache_path(model, prompt)
            if path.is_file():
                return path.read_text(encoding="utf-8")
        except OSError:
            pass
        return None

    def _cache_write(self, model: str, prompt: str, text: str) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._cache_path(model, prompt).write_text(text, encoding="utf-8")
        except OSError:
            pass  # the cache is an optimisation, never a hard requirement

    # -- network ----------------------------------------------------------

    def _post(self, model: str, prompt: str, expect_json: bool) -> tuple[str | None, int | None]:
        """One HTTP attempt. Returns (text, http_status_or_None).

        Never raises. The API key only ever appears in a request header; it is
        never logged, printed or stored in an error message.
        """
        generation_config: dict[str, Any] = {"temperature": 0.4}
        if expect_json:
            generation_config["responseMimeType"] = "application/json"
        body = json.dumps(
            {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{_API_BASE}/{model}:generateContent",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
        )
        try:
            self.calls_made += 1
            with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return _extract_text(payload), 200
        except urllib.error.HTTPError as exc:
            self.last_error = f"HTTP {exc.code} ({model}): {_safe_error_detail(exc, self._api_key)}"
            return None, exc.code
        except Exception as exc:  # URLError, timeout, JSON decode -- anything
            self.last_error = type(exc).__name__
            return None, None

    # -- public API -------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        expect_json: bool = True,
        max_retries: int = 3,
    ) -> str | None:
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
                # rate limited / overloaded / transient network: back off
                if index < attempts - 1:
                    time.sleep(_BACKOFFS[index])
                    continue
                break
            break  # 4xx that retrying will not fix

        # last resort: one shot each at the cheaper fallback models
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

    def generate_json(
        self,
        prompt: str,
        *,
        max_retries: int = 3,
    ) -> dict[str, Any] | list[Any] | None:
        """generate() plus tolerant JSON parsing. None if anything goes wrong."""
        text = self.generate(prompt, expect_json=True, max_retries=max_retries)
        if not text:
            return None
        return parse_json_loose(text)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _safe_error_detail(exc: urllib.error.HTTPError, api_key: str, limit: int = 180) -> str:
    """Short, key-redacted reason from an HTTP error body. For demo debugging."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        return exc.reason if isinstance(getattr(exc, "reason", None), str) else "no detail"
    try:
        message = str((json.loads(body).get("error") or {}).get("message") or body)
    except (ValueError, AttributeError, TypeError):
        message = body
    if api_key:
        message = message.replace(api_key, "<redacted>")
    message = " ".join(message.split())
    return message[:limit] or "no detail"


def _extract_text(payload: dict[str, Any]) -> str | None:
    """Pull the concatenated text out of a generateContent response."""
    try:
        chunks: list[str] = []
        for candidate in payload.get("candidates") or []:
            parts = ((candidate or {}).get("content") or {}).get("parts") or []
            for part in parts:
                if part.get("thought"):
                    continue
                piece = part.get("text")
                if piece:
                    chunks.append(piece)
            if chunks:
                break
        joined = "".join(chunks).strip()
        return joined or None
    except Exception:
        return None


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped[3:]
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        end = stripped.rfind("```")
        if end != -1:
            stripped = stripped[:end]
    return stripped.strip()


def _slice_outermost(text: str, open_ch: str, close_ch: str) -> str | None:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start != -1 and end > start:
        return text[start:end + 1]
    return None


def parse_json_loose(text: str) -> dict[str, Any] | list[Any] | None:
    """Parse JSON that may be fenced or wrapped in prose. None on failure."""
    if not text:
        return None
    body = _strip_fences(text)
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        candidate = _slice_outermost(body, open_ch, close_ch)
        if candidate:
            try:
                return json.loads(candidate)
            except (ValueError, TypeError):
                continue
    return None
