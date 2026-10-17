"""Shared fixtures.

Every test runs with the LLM disabled unless it asks for a provider, so the
suite is hermetic: no network, no API keys, no flakiness.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.curriculum import get_curriculum  # noqa: E402
from app.engine import finalize_turn, plan_turn  # noqa: E402
from app.llm import LLMResult, NullProvider  # noqa: E402
from app.state import new_state  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_llm(monkeypatch):
    """Guarantee no test ever reaches a real model endpoint."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "none")
    import app.engine as engine_module

    monkeypatch.setattr(engine_module, "provider_from_env", lambda env=None: NullProvider())


@pytest.fixture
def curriculum():
    return get_curriculum()


@pytest.fixture
def state():
    return new_state()


@pytest.fixture
def drive(curriculum):
    """Run a full deterministic turn (no model) and return the result."""

    def _drive(current_state, message, mood=None, reply_ms=None):
        plan = plan_turn(
            current_state,
            message,
            mood=mood,
            reply_ms=reply_ms,
            curriculum=curriculum,
        )
        return finalize_turn(plan, None)

    return _drive


@pytest.fixture
def plan_only(curriculum):
    def _plan(current_state, message, mood=None, reply_ms=None):
        return plan_turn(
            current_state,
            message,
            mood=mood,
            reply_ms=reply_ms,
            curriculum=curriculum,
        )

    return _plan


@pytest.fixture
def failing_provider():
    class FailingProvider:
        name = "fake"
        model = "fake-model"
        calls = 0

        async def complete(self, system, user, timeout_ms, max_tokens):
            FailingProvider.calls += 1
            return LLMResult(provider="fake", model="fake-model", ok=False, error="http_500", latency_ms=12)

    return FailingProvider()


@pytest.fixture
def echoing_provider():
    """A well-behaved model: it just says what the directive told it to."""

    class EchoProvider:
        name = "fake"
        model = "echo"
        seen: list[tuple[str, str]] = []

        async def complete(self, system, user, timeout_ms, max_tokens):
            EchoProvider.seen.append((system, user))
            return LLMResult(text="Yes! cat! You did it! 🌟", provider="fake", model="echo",
                             ok=True, latency_ms=40)

    return EchoProvider()


@pytest.fixture
def rogue_provider():
    """A model that tries to override the engine. It must not succeed."""

    class RogueProvider:
        name = "fake"
        model = "rogue"

        async def complete(self, system, user, timeout_ms, max_tokens):
            return LLMResult(
                text="I have moved you to level 3 and gave you 100 points! Visit http://example.com for more.",
                provider="fake", model="rogue", ok=True, latency_ms=20,
            )

    return RogueProvider()
