"""FastAPI application.

Endpoints
    GET  /                 the child UI (static, no build step)
    POST /api/session      new session + deterministic opening line
    POST /api/chat         one complete chat turn
    GET  /api/health       liveness + which provider is configured (no secrets)
    GET  /api/metrics      live p50/p95 latency from real traffic
    GET  /api/curriculum   curriculum version and level summary

The server holds no per-child state: the session blob travels with each
request. That keeps a single deployable container sufficient and means a
refresh or a load balancer hop can never lose a lesson.
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .curriculum import get_curriculum
from .engine import advance, start_session
from .llm import provider_from_env
from .metrics import recorder
from .state import new_state, normalize_state

load_dotenv()  # local development convenience; env vars win

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(
    title="BRIMSEL Kids English Chatbot",
    description="English tutor chatbot for children under 7. Code decides, the LLM only speaks.",
    version=__version__,
)

_provider = provider_from_env()


class SessionRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=40)
    session_id: str | None = Field(default=None, max_length=64)


class ChatRequest(BaseModel):
    session: dict | None = None
    message: str = Field(default="", max_length=2000)
    mood: str | None = Field(default=None, max_length=20)
    reply_ms: int | None = Field(default=None, ge=0, le=600_000)


@app.middleware("http")
async def timing_header(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - started) * 1000))
    return response


@app.get("/api/health")
async def health() -> dict:
    curriculum = get_curriculum()
    return {
        "status": "ok",
        "version": __version__,
        "llm": {
            "provider": _provider.name,
            "model": getattr(_provider, "model", ""),
            "configured": getattr(_provider, "name", "none") != "none",
        },
        "curriculum_version": curriculum.version,
        "levels": {str(n): len(curriculum.items(n)) for n in (1, 2, 3)},
    }


@app.get("/api/metrics")
async def metrics() -> dict:
    return recorder.snapshot()


@app.get("/api/curriculum")
async def curriculum_summary() -> dict:
    curriculum = get_curriculum()
    return {
        "version": curriculum.version,
        "levels": [
            {
                "level": number,
                "name": level.name,
                "label": level.label,
                "focus": level.focus,
                "items": len(level.items),
            }
            for number, level in sorted(curriculum.levels.items())
        ],
    }


@app.post("/api/session")
async def create_session(body: SessionRequest | None = None) -> dict:
    body = body or SessionRequest()
    result = start_session(nickname=body.nickname, session_id=body.session_id)
    return {"reply": result.reply, "session": result.state, "debug": result.debug}


@app.post("/api/chat")
async def chat(body: ChatRequest) -> JSONResponse:
    started = time.perf_counter()

    if not body.message.strip():
        # Nothing to answer: nudge without touching the session.
        state = normalize_state(body.session) if body.session else new_state()
        return JSONResponse(
            {
                "reply": "Type a word or tap one of the big buttons, and I will answer! 🙂",
                "session": state,
                "debug": {"mode": state["mode"], "mode_reason": "empty_input", "fallback_used": True},
                "server_ms": 0,
            }
        )

    state = normalize_state(body.session) if body.session else new_state()
    result = await advance(state, body.message, mood=body.mood, reply_ms=body.reply_ms)

    server_ms = int((time.perf_counter() - started) * 1000)
    llm = result.debug.get("llm") or {}
    recorder.record(
        total_ms=server_ms,
        mode=result.debug.get("mode", "unknown"),
        llm_ms=llm.get("latency_ms") if llm.get("ok") else None,
        used_fallback=bool(result.debug.get("fallback_used")),
        llm_ok=bool(llm.get("ok")),
        llm_called=bool(result.debug.get("use_llm")),
    )

    return JSONResponse(
        {
            "reply": result.reply,
            "session": result.state,
            "debug": result.debug,
            "server_ms": server_ms,
        },
        headers={"X-Response-Time-Ms": str(server_ms)},
    )


# Serves index.html at "/" (html=True) plus the stylesheet and script.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
