"""FastAPI application.

Endpoints
    GET  /                 the child UI (static, no build step)
    POST /api/session      new session + deterministic opening line
    POST /api/chat         one complete chat turn
    POST /api/level        reviewer control: jump to a level and ask its first item
    POST /api/speech       spoken audio for a reply (SSML built server-side)
    GET  /api/health       liveness + which provider is configured (no secrets)
    GET  /api/metrics      live p50/p95 latency from real traffic
    GET  /api/curriculum   curriculum version and level summary

The server holds no per-child state: the session blob travels with each
request. That keeps a single deployable container sufficient and means a
refresh or a load balancer hop can never lose a lesson.
"""

from __future__ import annotations

import asyncio
import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .curriculum import get_curriculum
from .engine import advance, change_level, start_session
from .llm import provider_from_env
from .metrics import recorder
from .speech import SpeechUnavailable, speech_from_env
from .state import new_state, normalize_state

load_dotenv()  # local development convenience; env vars win

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(
    title="BRIMSEL Kids English Chatbot",
    description="English tutor chatbot for children under 7. Code decides, the LLM only speaks.",
    version=__version__,
)

_provider = provider_from_env()
_speech = speech_from_env()

# Detached pre-warms live here so they are not garbage-collected mid-flight.
_PREWARM_TASKS: set[asyncio.Task] = set()


def _prewarm_speech(reply: str, debug: dict | None = None, speed: float = 1.0) -> None:
    """Compose the audio for a reply the server has just decided on.

    Every route that returns Buddy's next line knows the exact sentence, so the
    two-to-three seconds of cold synthesis can run while the browser paints the
    text. The browser's own ``/api/speech`` then joins that running synthesis,
    and a repeat of the same sentence is a cache hit.

    `speed` must match what the client will send. It is part of the prosody and
    therefore part of the cache key, so a moved speed slider would otherwise make
    every warm-up useless.

    This must never delay the text, so it is detached; speech is an enhancement,
    and a failure here is silent by design.
    """
    if not getattr(_speech, "enabled", False):
        return
    debug = debug or {}
    hint = debug.get("hint")
    energy = hint.get("energy") if isinstance(hint, dict) else None
    task = asyncio.create_task(
        _speech.prewarm(
            reply,
            mode=debug.get("mode"),
            correct=debug.get("correct"),
            energy=energy if isinstance(energy, str) else None,
            speed=speed,
        )
    )
    _PREWARM_TASKS.add(task)
    task.add_done_callback(_PREWARM_TASKS.discard)


class SessionRequest(BaseModel):
    nickname: str | None = Field(default=None, max_length=40)
    session_id: str | None = Field(default=None, max_length=64)
    speed: float | None = Field(default=None, ge=0.5, le=1.5)


class ChatRequest(BaseModel):
    session: dict | None = None
    message: str = Field(default="", max_length=2000)
    mood: str | None = Field(default=None, max_length=20)
    reply_ms: int | None = Field(default=None, ge=0, le=600_000)
    speed: float | None = Field(default=None, ge=0.5, le=1.5)


class LevelRequest(BaseModel):
    session: dict | None = None
    level: int = Field(default=1, ge=1, le=3)
    speed: float | None = Field(default=None, ge=0.5, le=1.5)


class SpeechRequest(BaseModel):
    """The client sends plain text plus a few enums.

    It never sends SSML. The server rebuilds the markup from these fields, so a
    tampered client cannot inject anything into the synthesiser. Every field is
    length-capped here and the prosody values are clamped again in `style_for`.
    """

    text: str = Field(default="", max_length=600)
    mode: str | None = Field(default=None, max_length=20)
    correct: bool | None = None
    target: str | None = Field(default=None, max_length=120)
    hint: dict | None = None
    speed: float | None = Field(default=None, ge=0.6, le=1.3)


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
        "speech": _speech.describe(),
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
    _prewarm_speech(result.reply, result.debug, speed=body.speed or 1.0)
    return {"reply": result.reply, "session": result.state, "debug": result.debug}


@app.post("/api/level")
async def set_level(body: LevelRequest) -> JSONResponse:
    """Reviewer control: jump straight to a level.

    Deterministic and offline by design. Promotion still works on its own; this
    exists so a reviewer can see level-2 and level-3 content without earning it
    through ten correct answers first.
    """
    state = normalize_state(body.session) if body.session else new_state()
    result = change_level(state, body.level)
    # A level jump is the case reviewers notice: the text lands instantly, so the
    # audio for that same sentence should already be warm when the browser asks.
    _prewarm_speech(result.reply, result.debug, speed=body.speed or 1.0)
    return JSONResponse(
        {"reply": result.reply, "session": result.state, "debug": result.debug, "server_ms": 0}
    )


@app.get("/api/warm")
async def warm() -> dict:
    """The openers of the three levels, so the client can pre-compose them.

    Read-only: each jump runs against a throwaway state, so nothing the child has
    done is touched and no session is returned. Switching level is the one flow a
    reviewer always tries, each level opens with a fixed sentence, and the first
    switch is normally the slowest - so composing those three sentences while the
    page is still settling is what makes the first jump sound instant.
    """
    phrases = []
    for level in (1, 2, 3):
        try:
            result = change_level(new_state(), level)
        except Exception:  # pragma: no cover - defensive: a bad level must not 500
            continue
        debug = result.debug or {}
        phrases.append(
            {
                "text": result.reply,
                "mode": debug.get("mode"),
                "correct": debug.get("correct"),
            }
        )
    return {"phrases": phrases}


@app.post("/api/speech")
async def speech(body: SpeechRequest) -> Response:
    """Spoken audio for one reply.

    Returns `audio/mpeg` directly rather than base64 inside JSON, so a large
    payload is never inflated by a third. The body is sent as one piece: the
    synthesiser produces its first audio chunk only at the very end of its own
    ~3s, so progressive delivery would not start playback meaningfully sooner.
    On any failure it answers 503 and the client uses the browser voice instead -
    speech is an enhancement, so it must never break a turn.
    """
    started = time.perf_counter()
    energy = None
    if isinstance(body.hint, dict):
        raw = body.hint.get("energy")
        if isinstance(raw, str):
            energy = raw[:24]

    try:
        audio = await _speech.audio(
            text=body.text,
            mode=body.mode,
            correct=body.correct,
            energy=energy,
            speed=body.speed if body.speed is not None else 1.0,
        )
    except SpeechUnavailable as exc:
        return JSONResponse(
            {"error": "speech_unavailable", "detail": str(exc), "fallback": "browser"},
            status_code=503,
        )

    return Response(
        content=audio.data,
        media_type="audio/mpeg",
        headers={
            "X-Speech-Voice": audio.voice,
            "X-Speech-Style": audio.style,
            "X-Speech-Chars": str(audio.chars),
            "X-Speech-Cached": "1" if audio.cached else "0",
            "X-Speech-Ms": str(int((time.perf_counter() - started) * 1000)),
            "Cache-Control": "private, max-age=600",
        },
    )


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
    _prewarm_speech(result.reply, result.debug, speed=body.speed or 1.0)
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
