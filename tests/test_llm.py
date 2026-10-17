"""Provider adapter: parsing, timeouts, error handling and the bounded fallback."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.engine import advance, start_session
from app.llm import (
    ChainProvider,
    GeminiProvider,
    NullProvider,
    OpenRouterProvider,
    provider_from_env,
)

GEMINI_OK = {"candidates": [{"content": {"parts": [{"text": "Yes! cat! You did it!"}]}}]}
OPENAI_OK = {"choices": [{"message": {"content": "Yes! cat! You did it!"}}]}

# Every request body the fake provider received, oldest first. Tests that assert
# on the wire format clear it first; the servers run sequentially within a test.
RECORDED: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"
    delay = 0.0

    def do_POST(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            RECORDED.append(json.loads(raw or b"{}"))
        except ValueError:
            RECORDED.append({})
        if self.delay:
            time.sleep(self.delay)

        if self.mode == "http_500":
            self.send_response(500)
            self.end_headers()
            return
        if self.mode == "http_429":
            self.send_response(429)
            self.end_headers()
            return

        body = GEMINI_OK if "generateContent" in self.path else OPENAI_OK
        if self.mode == "no_candidates":
            body = {"promptFeedback": {"blockReason": "SAFETY"}}
        if self.mode == "empty":
            body = {"candidates": [{"content": {"parts": []}}]}

        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # keep test output quiet
        return


@pytest.fixture
def server():
    created = []

    def _start(mode="ok", delay=0.0):
        handler = type("Handler", (_Handler,), {"mode": mode, "delay": delay})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        created.append(httpd)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    yield _start

    for httpd in created:
        httpd.shutdown()
        httpd.server_close()


def gemini(base_url, **kwargs):
    return GeminiProvider(api_key="test-key", model="test-model", base_url=base_url, **kwargs)


def openrouter(base_url, **kwargs):
    return OpenRouterProvider(api_key="test-key", model="test-model", base_url=base_url, **kwargs)


# ------------------------------------------------------------------- parsing

async def test_gemini_success_is_parsed(server):
    result = await gemini(server()).complete("sys", "user", timeout_ms=2000, max_tokens=32)
    assert result.ok is True
    assert result.text == "Yes! cat! You did it!"
    assert result.provider == "gemini"
    assert result.latency_ms >= 0


async def test_openrouter_success_is_parsed(server):
    result = await openrouter(server()).complete("sys", "user", timeout_ms=2000, max_tokens=32)
    assert result.ok is True
    assert result.text == "Yes! cat! You did it!"
    assert result.provider == "openrouter"


async def test_an_empty_model_reply_is_a_failure(server):
    result = await gemini(server(mode="empty")).complete("sys", "user", timeout_ms=2000, max_tokens=32)
    assert result.ok is False
    assert "empty_text" in (result.error or "")


async def test_a_safety_blocked_prompt_is_a_failure(server):
    result = await gemini(server(mode="no_candidates")).complete("sys", "user", timeout_ms=2000, max_tokens=32)
    assert result.ok is False
    assert "no_candidates" in (result.error or "")


# ------------------------------------------------------------------- failures

async def test_http_500_is_reported_not_raised(server):
    result = await gemini(server(mode="http_500")).complete("sys", "user", timeout_ms=2000, max_tokens=32)
    assert result.ok is False
    assert result.error == "http_500"


async def test_rate_limit_is_reported(server):
    result = await gemini(server(mode="http_429")).complete("sys", "user", timeout_ms=2000, max_tokens=32)
    assert result.ok is False
    assert result.error == "http_429"


async def test_a_dead_endpoint_is_reported_not_raised():
    result = await gemini("http://127.0.0.1:1").complete("sys", "user", timeout_ms=800, max_tokens=32)
    assert result.ok is False
    assert result.error
    assert result.text == ""


async def test_a_slow_model_times_out_within_the_budget(server):
    provider = gemini(server(delay=1.5))
    started = time.perf_counter()
    result = await provider.complete("sys", "user", timeout_ms=200, max_tokens=32)
    elapsed = time.perf_counter() - started

    assert result.ok is False
    assert result.error == "timeout"
    assert elapsed < 1.0, f"the timeout must be enforced, took {elapsed:.2f}s"


# ------------------------------------------------------------- request payload

async def test_the_reply_is_asked_for_immediately_without_thinking(server):
    """A two-sentence child reply should not pay for hidden reasoning tokens."""
    RECORDED.clear()
    await gemini(server()).complete("sys", "user", timeout_ms=2000, max_tokens=48)

    config = RECORDED[-1]["generationConfig"]
    assert config["thinkingConfig"] == {"thinkingBudget": 0}
    assert config["maxOutputTokens"] == 48


async def test_the_thinking_field_can_be_omitted_for_models_that_reject_it(server):
    """``gemini-3.5-flash-lite`` answers 400 to ``thinkingBudget: 0``."""
    RECORDED.clear()
    provider = gemini(server(), thinking_budget=None)
    await provider.complete("sys", "user", timeout_ms=2000, max_tokens=48)

    assert "thinkingConfig" not in RECORDED[-1]["generationConfig"]


def test_the_thinking_budget_defaults_to_zero():
    from app.llm import thinking_budget_from_env

    assert thinking_budget_from_env({}) == 0
    assert thinking_budget_from_env({"GEMINI_THINKING_BUDGET": "0"}) == 0
    assert thinking_budget_from_env({"GEMINI_THINKING_BUDGET": "512"}) == 512
    assert thinking_budget_from_env({"GEMINI_THINKING_BUDGET": "none"}) is None
    assert thinking_budget_from_env({"GEMINI_THINKING_BUDGET": "Auto"}) is None
    assert thinking_budget_from_env({"GEMINI_THINKING_BUDGET": "nonsense"}) == 0


def test_the_gemini_key_can_carry_a_thinking_budget():
    provider = provider_from_env({"GEMINI_API_KEY": "a", "GEMINI_THINKING_BUDGET": "none"})
    assert isinstance(provider, GeminiProvider)
    assert provider.thinking_budget is None


# --------------------------------------------------------------- configuration

def test_gemini_is_chosen_when_its_key_is_present():
    provider = provider_from_env({"GEMINI_API_KEY": "abc", "LLM_PROVIDER": "auto"})
    assert isinstance(provider, GeminiProvider)


def test_openrouter_is_chosen_when_it_is_the_only_key():
    provider = provider_from_env({"OPENROUTER_API_KEY": "abc"})
    assert isinstance(provider, OpenRouterProvider)


def test_no_keys_means_a_null_provider():
    assert isinstance(provider_from_env({}), NullProvider)


def test_an_explicit_choice_becomes_the_primary():
    provider = provider_from_env(
        {"LLM_PROVIDER": "openrouter", "GEMINI_API_KEY": "a", "OPENROUTER_API_KEY": "b"}
    )
    # Both keys are present, so the chosen one leads and the other backs it up.
    assert provider.name == "openrouter+gemini"
    assert isinstance(provider.providers[0], OpenRouterProvider)


def test_the_second_key_becomes_a_fallback():
    provider = provider_from_env({"GEMINI_API_KEY": "a", "OPENROUTER_API_KEY": "b"})
    assert isinstance(provider, ChainProvider)
    assert provider.name == "gemini+openrouter"


def test_a_single_key_produces_a_single_provider():
    provider = provider_from_env({"GEMINI_API_KEY": "a"})
    assert isinstance(provider, GeminiProvider)
    assert not isinstance(provider, ChainProvider)


def test_an_explicit_fallback_can_be_named():
    provider = provider_from_env({
        "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "a",
        "OPENROUTER_API_KEY": "b", "LLM_FALLBACK_PROVIDER": "openrouter",
    })
    assert provider.name == "gemini+openrouter"


def test_a_fallback_without_a_key_is_not_added():
    provider = provider_from_env(
        {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "a", "LLM_FALLBACK_PROVIDER": "openrouter"}
    )
    assert isinstance(provider, GeminiProvider)


def test_a_provider_is_never_created_without_its_key():
    assert isinstance(provider_from_env({"LLM_PROVIDER": "gemini"}), NullProvider)


async def test_the_null_provider_always_fails_so_the_fallback_runs():
    result = await NullProvider().complete("sys", "user", timeout_ms=100, max_tokens=8)
    assert result.ok is False
    assert result.error == "no_provider_configured"


# ------------------------------------------------------------ provider chaining

async def test_a_rate_limited_primary_falls_through_to_the_secondary(server):
    """A 429 must cost a few hundred ms, not the child's reply."""
    chain = ChainProvider([gemini(server(mode="http_429")), openrouter(server())])

    result = await chain.complete("sys", "user", timeout_ms=1800, max_tokens=48)

    assert result.ok is True
    assert result.provider == "openrouter"
    assert result.text == "Yes! cat! You did it!"


async def test_a_timeout_does_not_stack_onto_the_secondary(server):
    """Once the budget is spent we stop and let the template reply."""
    chain = ChainProvider([gemini(server(delay=1.5)), openrouter(server())])

    started = time.perf_counter()
    result = await chain.complete("sys", "user", timeout_ms=300, max_tokens=48)
    elapsed = time.perf_counter() - started

    assert result.ok is False
    assert result.error == "timeout"
    assert elapsed < 1.0, f"a timeout must not be retried, took {elapsed:.2f}s"


async def test_the_healthy_primary_is_used_without_touching_the_secondary(server):
    chain = ChainProvider([gemini(server()), gemini(server(mode="http_500"))])

    result = await chain.complete("sys", "user", timeout_ms=1800, max_tokens=48)

    assert result.ok is True
    assert result.provider == "gemini"


# ------------------------------------------------------- bounded end-to-end turn

async def test_a_timing_out_model_still_gives_a_fast_child_safe_reply(server, curriculum):
    provider = gemini(server(delay=1.5))
    state = start_session().state

    started = time.perf_counter()
    result = await advance(state, "hello buddy", provider=provider, curriculum=curriculum, timeout_ms=200)
    elapsed = time.perf_counter() - started

    assert result.reply.strip(), "the child always gets an answer"
    assert result.debug["fallback_used"] is True
    assert result.debug["llm"]["error"] == "timeout"
    assert elapsed < 1.0, f"a slow model must not hold the turn open, took {elapsed:.2f}s"
