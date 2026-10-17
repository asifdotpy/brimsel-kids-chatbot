"""Session state: creation and defensive normalisation.

The server is stateless. The full session state travels with each request and is
held in the browser's ``localStorage``, so a page refresh never resets the
lesson, and no child data is persisted server-side.

Because the state makes a round trip through the client it is treated as
**untrusted input**: :func:`normalize_state` validates and clamps every field
before the engine sees it. A tampered or corrupt blob degrades to a valid
session instead of crashing a child's chat.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

STATE_VERSION = 1

MAX_TURNS = 12
MAX_RESULTS = 10
MAX_MOODS = 6
MAX_MASTERY_BOX = 3

MODES = ("Support", "Engagement", "Conversation", "Learning")
MOOD_VALUES = (
    "happy", "okay", "shy", "tired", "sad", "angry", "scared", "excited",
)

MAX_NICKNAME = 20
MAX_NOTE = 200


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_session_id() -> str:
    return "kid_" + uuid.uuid4().hex[:16]


def new_state(nickname: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """A fresh, valid session."""
    now = utcnow()
    return {
        "version": STATE_VERSION,
        "session_id": session_id or new_session_id(),
        "nickname": _clean_nickname(nickname),
        "level": 1,
        "attempts_at_level": 0,
        "recent_results": [],
        "correct_streak": 0,
        "miss_streak": 0,
        "learn_streak": 0,
        "short_replies": 0,
        "repeat_count": 0,
        "ease_count": 0,
        "promote_count": 0,
        "mastery": {},
        "current_item": None,
        "pending_item": None,
        "item_cursor": 0,
        "mode": "Conversation",
        "last_mode": "Conversation",
        "turns": [],
        "moods": [],
        "last_child_text": "",
        "worry_flag": False,
        "blocked_count": 0,
        "turn_count": 0,
        "created_at": now,
        "updated_at": now,
    }


# --------------------------------------------------------------------------
# Coercion helpers
# --------------------------------------------------------------------------

def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clean_nickname(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    # Nickname only, never a full name. Strip anything that is not a letter.
    cleaned = "".join(ch for ch in value.strip() if ch.isalpha() or ch in " _-").strip()
    if not cleaned:
        return None
    return cleaned[:MAX_NICKNAME]


def _clean_note(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:MAX_NOTE]


def _clean_mode(value: Any, default: str = "Conversation") -> str:
    return value if value in MODES else default


def _clean_mood(value: Any) -> str | None:
    return value if value in MOOD_VALUES else None


def _clean_turns(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    turns: list[dict[str, Any]] = []
    for raw in value[-MAX_TURNS:]:
        if not isinstance(raw, dict):
            continue
        role = raw.get("role")
        if role not in ("child", "bot"):
            continue
        text = raw.get("text")
        if not isinstance(text, str):
            text = ""
        correct = raw.get("correct")
        turns.append(
            {
                "role": role,
                "text": text[:500],
                "mode": _clean_mode(raw.get("mode")),
                "mood": _clean_mood(raw.get("mood")),
                "correct": correct if isinstance(correct, bool) else None,
                "item_id": raw.get("item_id") if isinstance(raw.get("item_id"), str) else None,
                "latency_ms": _int(raw.get("latency_ms"), 0, 0, 600_000) if raw.get("latency_ms") is not None else None,
                "ts": raw.get("ts") if isinstance(raw.get("ts"), str) else None,
            }
        )
    return turns


def _clean_mastery(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    mastery: dict[str, dict[str, Any]] = {}
    for item_id, raw in list(value.items())[:200]:
        if not isinstance(item_id, str) or not isinstance(raw, dict):
            continue
        mastery[item_id] = {
            "box": _int(raw.get("box"), 0, 0, MAX_MASTERY_BOX),
            "attempts": _int(raw.get("attempts"), 0, 0, 100_000),
            "correct": _int(raw.get("correct"), 0, 0, 100_000),
            "last_seen": raw.get("last_seen") if isinstance(raw.get("last_seen"), str) else None,
        }
    return mastery


def _clean_results(value: Any) -> list[bool]:
    if not isinstance(value, list):
        return []
    return [bool(v) for v in value if isinstance(v, bool)][-MAX_RESULTS:]


def _clean_moods(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [m for m in (_clean_mood(v) for v in value) if m][-MAX_MOODS:]


def normalize_state(raw: Any) -> dict[str, Any]:
    """Validate and clamp a state blob from the client."""
    if not isinstance(raw, dict):
        return new_state()

    state = new_state(
        nickname=raw.get("nickname"),
        session_id=raw.get("session_id") if isinstance(raw.get("session_id"), str) and raw.get("session_id") else None,
    )

    state["level"] = _int(raw.get("level"), 1, 1, 3)
    state["attempts_at_level"] = _int(raw.get("attempts_at_level"), 0, 0, 10_000)
    state["recent_results"] = _clean_results(raw.get("recent_results"))
    state["correct_streak"] = _int(raw.get("correct_streak"), 0, 0, 1_000)
    state["miss_streak"] = _int(raw.get("miss_streak"), 0, 0, 1_000)
    state["learn_streak"] = _int(raw.get("learn_streak"), 0, 0, 3)
    state["short_replies"] = _int(raw.get("short_replies"), 0, 0, 100)
    state["repeat_count"] = _int(raw.get("repeat_count"), 0, 0, 100)
    state["ease_count"] = _int(raw.get("ease_count"), 0, 0, 10_000)
    state["promote_count"] = _int(raw.get("promote_count"), 0, 0, 1_000)
    state["mastery"] = _clean_mastery(raw.get("mastery"))
    state["current_item"] = raw.get("current_item") if isinstance(raw.get("current_item"), str) else None
    state["pending_item"] = raw.get("pending_item") if isinstance(raw.get("pending_item"), str) else None
    state["item_cursor"] = _int(raw.get("item_cursor"), 0, 0, 100_000)
    state["mode"] = _clean_mode(raw.get("mode"))
    state["last_mode"] = _clean_mode(raw.get("last_mode"), state["mode"])
    state["turns"] = _clean_turns(raw.get("turns"))
    state["moods"] = _clean_moods(raw.get("moods"))
    state["last_child_text"] = _clean_note(raw.get("last_child_text"))
    state["worry_flag"] = bool(raw.get("worry_flag"))
    state["blocked_count"] = _int(raw.get("blocked_count"), 0, 0, 100_000)
    state["turn_count"] = _int(raw.get("turn_count"), len(state["turns"]), 0, 1_000_000)
    state["created_at"] = raw.get("created_at") if isinstance(raw.get("created_at"), str) else state["created_at"]
    state["updated_at"] = utcnow()
    return state


def append_turn(
    state: dict[str, Any],
    role: str,
    text: str,
    mode: str,
    mood: str | None = None,
    correct: bool | None = None,
    item_id: str | None = None,
    latency_ms: int | None = None,
) -> None:
    state["turns"].append(
        {
            "role": role,
            "text": text[:500],
            "mode": mode,
            "mood": mood,
            "correct": correct,
            "item_id": item_id,
            "latency_ms": latency_ms,
            "ts": utcnow(),
        }
    )
    del state["turns"][:-MAX_TURNS]
    if role == "child":
        state["turn_count"] += 1


def last_child_turns(state: dict[str, Any], count: int) -> list[dict[str, Any]]:
    child = [t for t in state["turns"] if t["role"] == "child"]
    return child[-count:]


def set_mode(state: dict[str, Any], mode: str) -> None:
    state["last_mode"] = state["mode"]
    state["mode"] = mode


def mastery_of(state: dict[str, Any], item_id: str) -> dict[str, Any]:
    return state["mastery"].setdefault(
        item_id, {"box": 0, "attempts": 0, "correct": 0, "last_seen": None}
    )


def struggling_items(state: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    """Items with the lowest mastery boxes, weakest first."""
    entries = [
        {"item_id": item_id, **data}
        for item_id, data in state["mastery"].items()
        if data.get("attempts", 0) > 0
    ]
    entries.sort(key=lambda e: (e["box"], -e["attempts"], e["item_id"]))
    return entries[:limit]


def mastered_items(state: dict[str, Any], limit: int = 5) -> list[str]:
    entries = [
        (data.get("box", 0), data.get("attempts", 0), item_id)
        for item_id, data in state["mastery"].items()
        if data.get("box", 0) >= 3
    ]
    entries.sort(key=lambda e: (-e[0], -e[1], e[2]))
    return [e[2] for e in entries[:limit]]
