"""LLM provider adapter.

One interface, four implementations, so a provider swap is a config change
rather than a code change:

* :class:`GeminiProvider` - Google ``generateContent`` (default).
* :class:`OpenRouterProvider` - any OpenAI-compatible chat completions endpoint.
* :class:`ChainProvider` - a primary plus a backup, sharing one time budget.
* :class:`NullProvider`  - no model configured; the engine always falls back.

Exactly one model call per turn succeeds. The chain may make a *second* request
only when the first provider hard-fails (rate limit, HTTP error, dead socket)
and time remains in the budget; a timeout ends the turn immediately, because two
stacked timeouts would blow the latency target.

Every call is bounded by a hard wall-clock cap. A timeout, HTTP error or
malformed body is returned as a failed :class:`LLMResult`, never raised at the
child, so the deterministic fallback reply always has a chance to run.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

DEFAULT_TIMEOUT_MS = 1800
DEFAULT_MAX_TOKENS = 48
DEFAULT_TEMPERATURE = 0.6

GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"
OPENROUTER_DEFAULT_MODEL = "google/gemini-3.5-flash-lite"


@dataclass(frozen=True)
class LLMResult:
    text: str = ""
    provider: str = "none"
    model: str = ""
    latency_ms: int = 0
    ok: bool = False
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


class Provider(Protocol):
    name: str

    async def complete(self, system: str, user: str, timeout_ms: int, max_tokens: int) -> LLMResult: ...


class NullProvider:
    """Used when no API key is configured. Always fails, so fallbacks run."""

    name = "none"
    model = ""

    async def complete(self, system: str, user: str, timeout_ms: int, max_tokens: int) -> LLMResult:
        return LLMResult(provider="none", ok=False, error="no_provider_configured")


class ChainProvider:
    """Try providers in order, but never exceed the turn's latency budget.

    A rate limit or a server error on the first provider should not cost the
    child a reply, so the next provider is tried with whatever time is left. A
    *timeout* is different: the budget is already spent, so we stop and let the
    deterministic fallback speak immediately.
    """

    name = "chain"
    model = ""

    def __init__(self, providers: list[Provider]) -> None:
        self.providers = [provider for provider in providers if provider is not None]
        self.name = "+".join(provider.name for provider in self.providers) or "none"
        self.model = getattr(self.providers[0], "model", "") if self.providers else ""

    async def complete(self, system: str, user: str, timeout_ms: int, max_tokens: int) -> LLMResult:
        started = time.perf_counter()
        last = LLMResult(provider="none", ok=False, error="no_provider_configured")

        for provider in self.providers:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            remaining_ms = timeout_ms - elapsed_ms
            if remaining_ms < 250:
                break

            result = await provider.complete(system, user, remaining_ms, max_tokens)
            if result.ok:
                return result
            last = result

            if result.error == "timeout":
                break  # budget exhausted; do not stack another timeout

        return last


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str = GEMINI_DEFAULT_MODEL, base_url: str | None = None,
                 thinking_budget: int | None = 0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        # 0 = answer immediately (right for a two-sentence reply and fastest).
        # ``None`` omits the field entirely, which some models require.
        self.thinking_budget = thinking_budget

    async def complete(self, system: str, user: str, timeout_ms: int, max_tokens: int) -> LLMResult:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        generation_config: dict = {
            "maxOutputTokens": max_tokens,
            "temperature": DEFAULT_TEMPERATURE,
            "topP": 0.9,
        }
        if self.thinking_budget is not None:
            generation_config["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation_config,
        }
        headers = {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}
        return await _post_json(url, headers, payload, timeout_ms, self.name, self.model, _parse_gemini)


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, api_key: str, model: str = OPENROUTER_DEFAULT_MODEL,
                 base_url: str | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://openrouter.ai/api/v1").rstrip("/")

    async def complete(self, system: str, user: str, timeout_ms: int, max_tokens: int) -> LLMResult:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": DEFAULT_TEMPERATURE,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        return await _post_json(url, headers, payload, timeout_ms, self.name, self.model, _parse_openai)


def _parse_gemini(body: dict) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        feedback = (body.get("promptFeedback") or {}).get("blockReason")
        raise ValueError(f"no_candidates:{feedback}" if feedback else "no_candidates")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
    if not text.strip():
        raise ValueError("empty_text")
    return text.strip()


def _parse_openai(body: dict) -> str:
    choices = body.get("choices") or []
    if not choices:
        raise ValueError("no_choices")
    text = (choices[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise ValueError("empty_text")
    return text.strip()


async def _post_json(url: str, headers: dict, payload: dict, timeout_ms: int,
                     provider: str, model: str, parser) -> LLMResult:
    started = time.perf_counter()
    timeout_s = max(0.05, timeout_ms / 1000.0)

    async def _request() -> httpx.Response:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            return await client.post(url, headers=headers, json=payload)

    try:
        # ``httpx`` timeouts are per-socket-operation, so a slowly trickling body
        # can outlive them. ``wait_for`` is the wall-clock guarantee the turn
        # budget actually needs.
        response = await asyncio.wait_for(_request(), timeout=timeout_s)
        latency = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            return LLMResult(provider=provider, model=model, latency_ms=latency, ok=False,
                             error=f"http_{response.status_code}")
        text = parser(response.json())
        return LLMResult(text=text, provider=provider, model=model, latency_ms=latency, ok=True)
    except (asyncio.TimeoutError, httpx.TimeoutException):
        latency = int((time.perf_counter() - started) * 1000)
        return LLMResult(provider=provider, model=model, latency_ms=latency, ok=False, error="timeout")
    except Exception as exc:  # noqa: BLE001 - never let a provider error reach the child
        latency = int((time.perf_counter() - started) * 1000)
        return LLMResult(provider=provider, model=model, latency_ms=latency, ok=False,
                         error=f"{type(exc).__name__}:{str(exc)[:80]}")


def provider_from_env(env: dict | None = None) -> Provider:
    """Build a provider chain from configuration.

    ``LLM_PROVIDER`` is ``gemini``, ``openrouter`` or ``none``. With no explicit
    value we pick whichever key is present, preferring Gemini. If a second key
    is available it becomes a fallback, so one provider's rate limit or outage
    does not cost the child a reply.
    """
    env = env if env is not None else os.environ
    choice = (env.get("LLM_PROVIDER") or "").strip().lower()

    gemini_key = (env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") or "").strip()
    openrouter_key = (env.get("OPENROUTER_API_KEY") or "").strip()

    if choice in ("", "auto"):
        choice = "gemini" if gemini_key else ("openrouter" if openrouter_key else "none")

    def build(name: str) -> Provider:
        if name == "gemini" and gemini_key:
            return GeminiProvider(
                api_key=gemini_key,
                model=(env.get("GEMINI_MODEL") or GEMINI_DEFAULT_MODEL).strip(),
                base_url=env.get("GEMINI_BASE_URL"),
                thinking_budget=thinking_budget_from_env(env),
            )
        if name == "openrouter" and openrouter_key:
            return OpenRouterProvider(
                api_key=openrouter_key,
                model=(env.get("OPENROUTER_MODEL") or OPENROUTER_DEFAULT_MODEL).strip(),
                base_url=env.get("OPENROUTER_BASE_URL"),
            )
        return NullProvider()

    primary = build(choice)
    if isinstance(primary, NullProvider):
        return primary

    chain: list[Provider] = [primary]

    explicit_fallback = (env.get("LLM_FALLBACK_PROVIDER") or "").strip().lower()
    if explicit_fallback and explicit_fallback != choice:
        secondary = build(explicit_fallback)
        if not isinstance(secondary, NullProvider):
            chain.append(secondary)
    else:
        # Auto: the other provider is used if its key happens to be present.
        other = "openrouter" if choice == "gemini" else "gemini"
        secondary = build(other)
        if not isinstance(secondary, NullProvider):
            chain.append(secondary)

    return chain[0] if len(chain) == 1 else ChainProvider(chain)


def timeout_ms_from_env(env: dict | None = None) -> int:
    env = env if env is not None else os.environ
    try:
        return max(100, int(env.get("LLM_TIMEOUT_MS") or DEFAULT_TIMEOUT_MS))
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_MS


def max_tokens_from_env(env: dict | None = None) -> int:
    env = env if env is not None else os.environ
    try:
        return max(8, min(256, int(env.get("LLM_MAX_TOKENS") or DEFAULT_MAX_TOKENS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS


def thinking_budget_from_env(env: dict | None = None) -> int | None:
    """How much internal reasoning to allow on a Gemini call.

    ``0`` means "answer straight away", which is what a two-sentence child reply
    wants. Some models (for example ``gemini-3.5-flash-lite``) reject ``0`` with
    a 400, so those can be pointed at with ``GEMINI_THINKING_BUDGET=none`` to
    omit the field altogether.
    """
    env = env if env is not None else os.environ
    raw = (env.get("GEMINI_THINKING_BUDGET") or "").strip().lower()
    if raw in ("none", "off", "omit", "auto"):
        return None
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0
