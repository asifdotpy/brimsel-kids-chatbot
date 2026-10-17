"""Latency benchmark for candidate models, using the real Buddy prompt.

Run:  set -a; . /home/asif1/evidence-engine/.env; set +a; python scripts/bench_models.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.curriculum import load_curriculum  # noqa: E402
from app.llm import GeminiProvider, OpenRouterProvider  # noqa: E402
from app.prompts import SYSTEM_PROMPT, build_user_prompt  # noqa: E402
from app.state import new_state  # noqa: E402

# A representative mid-lesson turn: the child answered "cat", we now ask "dog".
DIRECTIVE = {
    "mode": "learn", "level": 1, "last_answer": "correct", "streak": 1,
    "mood": "happy", "praise": "effort", "hint": None, "reason": "answering_prompt",
    "answered": "cat", "item_kind": "repeat", "target": "dog", "ask": "Can you say dog?",
}

USER_PROMPT = build_user_prompt(new_state(), DIRECTIVE, "cat", load_curriculum())

CANDIDATES = [
    ("gemini", "gemini-3.5-flash-lite"),
    ("gemini", "gemini-3.5-flash"),
    ("gemini", "gemini-3.1-flash-lite"),
    ("openrouter", "google/gemini-3.5-flash-lite"),
    ("openrouter", "google/gemini-3.5-flash"),
    ("openrouter", "google/gemini-2.5-flash"),
]

SAMPLES = int(os.environ.get("BENCH_SAMPLES", "5"))
TIMEOUT_MS = 4000


async def measure(provider) -> tuple[list[int], str, str]:
    times: list[int] = []
    sample = ""
    error = ""
    for _ in range(SAMPLES):
        result = await provider.complete(SYSTEM_PROMPT, USER_PROMPT, TIMEOUT_MS, 48)
        if result.ok:
            times.append(result.latency_ms)
            sample = result.text
        else:
            error = result.error or "unknown"
    return times, sample, error


async def main() -> None:
    for kind, model in CANDIDATES:
        if kind == "gemini":
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not key:
                print(f"{model:44} skipped (no GEMINI_API_KEY)")
                continue
            provider = GeminiProvider(api_key=key, model=model)
        else:
            key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                print(f"{model:44} skipped (no OPENROUTER_API_KEY)")
                continue
            provider = OpenRouterProvider(api_key=key, model=model)

        times, sample, error = await measure(provider)
        if not times:
            print(f"{model:44} FAILED  {error}")
            continue
        ordered = sorted(times)
        p50 = statistics.median(times)
        print(f"{model:44} p50={p50:6.0f}ms  min={min(times):5d}  max={max(times):5d}  "
              f"ok={len(times)}/{SAMPLES}  {sample[:58]!r}")


if __name__ == "__main__":
    asyncio.run(main())
