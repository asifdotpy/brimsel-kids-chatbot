"""The tutor engine: one deterministic function per turn.

``plan_turn`` runs the entire pipeline except the model call:

    safety -> state -> mode -> answer evaluation -> progression -> item choice
    -> directive -> prompt -> fallback reply

``finalize_turn`` then filters the model's reply (or uses the fallback) and
commits the turn to the session.

The split means every pedagogical rule is testable synchronously, with no
network and no model in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .curriculum import Curriculum, Item, get_curriculum
from .fallbacks import FallbackContext, fallback_reply, greeting
from .llm import LLMResult, Provider, max_tokens_from_env, provider_from_env, timeout_ms_from_env
from .modes import (
    CONVERSATION,
    ENGAGEMENT,
    LEARNING,
    SUPPORT,
    ModeInputs,
    canonical_mood,
    detect_mode,
    is_low_content_reply,
)
from .progression import Progression, apply_progression, is_repeat_of_last_text, record_result, select_item
from .prompts import SYSTEM_PROMPT, build_directive, build_user_prompt
from .state import (
    MAX_MOODS,
    append_turn,
    new_state,
    normalize_state,
    set_level,
    set_mode,
    utcnow,
)
from .text import InputVerdict, attempts_item, evaluate_answer, sanitize_input, sanitize_output

HIDDEN_TEXT = "[hidden for safety]"


@dataclass
class TurnPlan:
    """Everything decided before the model is asked to speak."""

    state: dict[str, Any]
    mode: str
    mode_reason: str
    item: Item | None
    correct: bool | None
    progression: Progression
    directive: dict[str, Any]
    system_prompt: str
    user_prompt: str
    fallback: str
    use_llm: bool
    hint: bool
    verdict: InputVerdict
    previous_mode: str
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnResult:
    reply: str
    state: dict[str, Any]
    debug: dict[str, Any]


def _item_hint(item: Item | None) -> dict | None:
    """Minimal item view passed to mode detection (it must not score answers)."""
    if item is None:
        return None
    return {"id": item.id, "kind": item.kind, "accept": list(item.accept)}


def plan_turn(
    state: dict[str, Any],
    raw_message: str,
    mood: str | None = None,
    reply_ms: int | None = None,
    curriculum: Curriculum | None = None,
) -> TurnPlan:
    curriculum = curriculum or get_curriculum()
    state = normalize_state(state)
    mood = canonical_mood(mood)
    previous_mode = state["mode"]

    # ---------------------------------------------------------------- 1. safety
    verdict = sanitize_input(raw_message)
    child_text = verdict.text

    if mood:
        state["moods"] = (state["moods"] + [mood])[-MAX_MOODS:]

    # ------------------------------------------------- 2. pace / engagement signals
    answering_previous = state["pending_item"] is not None
    if child_text:
        if is_repeat_of_last_text(state, child_text):
            state["repeat_count"] = min(state.get("repeat_count", 0) + 1, 100)
        else:
            state["repeat_count"] = 0
        state["short_replies"] = (
            min(state.get("short_replies", 0) + 1, 100)
            if (not answering_previous and is_low_content_reply(child_text))
            else (0 if not answering_previous else state.get("short_replies", 0))
        )
    state["last_child_text"] = child_text[:200]

    # ------------------------------------------------------------------ 3. mode
    pending_item = curriculum.get(state["pending_item"]) if state["pending_item"] else None
    decision = detect_mode(
        ModeInputs(
            text=child_text,
            mood=mood,
            reply_ms=reply_ms,
            learn_streak=state["learn_streak"],
            miss_streak=state["miss_streak"],
            short_replies=state["short_replies"],
            repeat_count=state["repeat_count"],
            pending_item=_item_hint(pending_item),
            previous_mode=previous_mode,
            worry=verdict.worry,
            turn_count=state["turn_count"],
            attempts_pending=attempts_item(child_text, pending_item) if pending_item else False,
        )
    )
    mode, reason = decision.mode, decision.reason

    if verdict.blocked:
        # Never forward unsafe text to the model, and never treat it as a lesson.
        mode, reason = CONVERSATION, "safety_redirect"
        state["blocked_count"] = state.get("blocked_count", 0) + 1

    if verdict.worry:
        state["worry_flag"] = True

    # --------------------------------------- 4. learning: evaluate and progress
    item: Item | None = None
    correct: bool | None = None
    progression = Progression()
    hint = False
    answered_item: Item | None = None

    if mode == LEARNING:
        if pending_item is not None:
            answered_item = pending_item
            correct = evaluate_answer(child_text, pending_item)
            if correct is None:
                # No usable answer: re-ask the same item, gently, with help.
                item = pending_item
                hint = True
                reason = "no_answer" if reason == "answering_prompt" else reason
            else:
                now = utcnow()
                record_result(state, pending_item.id, correct, now)
                progression = apply_progression(state)
                hint = progression.hint and not progression.promoted
                item = select_item(curriculum, state, hint=hint)
        else:
            item = select_item(curriculum, state)
            if reason == "default_unclear":
                reason = "start_lesson"

        if item is not None:
            state["current_item"] = item.id
            state["pending_item"] = item.id
        state["learn_streak"] = min(state["learn_streak"] + 1, 3)
    else:
        state["learn_streak"] = 0
        # Keep the pending question across a single Engagement break so the
        # child can slide back into practice; otherwise the lesson is dropped.
        if not (mode == ENGAGEMENT and previous_mode == LEARNING):
            state["pending_item"] = None

    set_mode(state, mode)

    # ------------------------------------------------- 5. directive and prompt
    directive = build_directive(
        state=state,
        mode=mode,
        item=item if mode == LEARNING else None,
        answered_item=answered_item if mode == LEARNING else None,
        correct=correct,
        progression=progression,
        mood=mood,
        hint=hint,
        worry=verdict.worry,
        reason=reason,
    )
    user_prompt = build_user_prompt(state, directive, child_text, curriculum)

    # Support turns and unsafe input never reach the model: the deterministic
    # reply is safer and has no latency tail.
    use_llm = (not verdict.blocked) and mode != SUPPORT and bool(child_text)

    fb_reason = reason
    if progression.eased:
        fb_reason = "eased"
    target = item.text if (item is not None and not item.is_open) else ""
    fallback = fallback_reply(
        FallbackContext(
            mode=mode,
            level=int(state["level"]),
            nickname=state.get("nickname"),
            target=target,
            ask=item.ask if item is not None else "",
            hint=item.hint if item is not None else "",
            answered=answered_item.text if (answered_item is not None and not answered_item.is_open) else "",
            answered_hint=answered_item.hint if answered_item is not None else "",
            next_ask=item.ask if item is not None else "",
            correct=correct,
            streak=int(state.get("correct_streak", 0)),
            mood=mood,
            worry=verdict.worry,
            turn_count=int(state.get("turn_count", 0)),
            reason=fb_reason,
        )
    )

    # ------------------------------------------------------- 6. commit child turn
    append_turn(
        state,
        role="child",
        text=HIDDEN_TEXT if verdict.blocked else child_text,
        mode=mode,
        mood=mood,
        correct=correct,
        item_id=answered_item.id if answered_item is not None else None,
    )
    state["updated_at"] = utcnow()

    debug = {
        "mode": mode,
        "mode_reason": reason,
        "previous_mode": previous_mode,
        "level": int(state["level"]),
        "correct": correct,
        "attempts_at_level": int(state["attempts_at_level"]),
        "accuracy_window": list(state["recent_results"][-5:]),
        "promoted": progression.promoted,
        "demoted": progression.demoted,
        "eased": progression.eased,
        "hint": hint,
        "item_id": item.id if item is not None else None,
        "item_ask": item.ask if item is not None else None,
        "learn_streak": int(state["learn_streak"]),
        "input": verdict.as_dict(),
        "use_llm": use_llm,
        "directive": directive,
    }
    return TurnPlan(
        state=state,
        mode=mode,
        mode_reason=reason,
        item=item,
        correct=correct,
        progression=progression,
        directive=directive,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        fallback=fallback,
        use_llm=use_llm,
        hint=hint,
        verdict=verdict,
        previous_mode=previous_mode,
        debug=debug,
    )


def finalize_turn(plan: TurnPlan, llm_result: LLMResult | None = None) -> TurnResult:
    """Filter a model reply (or use the fallback) and commit the bot turn."""
    used_fallback = True
    output_filtered = False

    if llm_result is not None and llm_result.ok and llm_result.text:
        reply, output_filtered = sanitize_output(llm_result.text, plan.fallback)
        used_fallback = reply == plan.fallback
    else:
        reply = plan.fallback

    if not reply.strip():
        reply, used_fallback = plan.fallback, True

    state = plan.state
    append_turn(
        state,
        role="bot",
        text=reply,
        mode=plan.mode,
        item_id=plan.item.id if plan.item is not None else None,
    )
    state["updated_at"] = utcnow()

    debug = dict(plan.debug)
    debug["fallback_used"] = used_fallback
    debug["output_filtered"] = output_filtered
    debug["reply"] = reply
    if llm_result is not None:
        debug["llm"] = llm_result.as_dict()
    else:
        debug["llm"] = {"ok": False, "provider": "none", "model": "", "latency_ms": 0, "error": "not_called"}

    return TurnResult(reply=reply, state=state, debug=debug)


async def advance(
    state: dict[str, Any],
    raw_message: str,
    mood: str | None = None,
    reply_ms: int | None = None,
    provider: Provider | None = None,
    curriculum: Curriculum | None = None,
    timeout_ms: int | None = None,
    max_tokens: int | None = None,
) -> TurnResult:
    """Run one complete turn, calling the model only when it is needed."""
    plan = plan_turn(state, raw_message, mood=mood, reply_ms=reply_ms, curriculum=curriculum)

    if not plan.use_llm:
        return finalize_turn(plan, None)

    provider = provider or provider_from_env()
    result = await provider.complete(
        plan.system_prompt,
        plan.user_prompt,
        timeout_ms if timeout_ms is not None else timeout_ms_from_env(),
        max_tokens if max_tokens is not None else max_tokens_from_env(),
    )
    return finalize_turn(plan, result)


def start_session(nickname: str | None = None, session_id: str | None = None) -> TurnResult:
    """Create a session and its deterministic opening line (no model call)."""
    state = new_state(nickname=nickname, session_id=session_id)
    text = greeting(state)
    append_turn(state, role="bot", text=text, mode=CONVERSATION)
    return TurnResult(
        reply=text,
        state=state,
        debug={
            "mode": CONVERSATION,
            "mode_reason": "session_start",
            "level": 1,
            "correct": None,
            "fallback_used": True,
            "llm": {"ok": False, "provider": "none", "model": "", "latency_ms": 0, "error": "not_called"},
        },
    )


def change_level(state: dict[str, Any], level: int, curriculum: Curriculum | None = None) -> TurnResult:
    """Reviewer control: jump to a level and ask that level's first item.

    Deliberately deterministic and offline - no model call. A level jump should
    be instant and must never depend on an upstream provider, and the opening
    question of a level is content rather than conversation.

    The request is routed through the ordinary :func:`plan_turn` path, so the
    first item, ``pending_item``, the learn streak and the mode are all set by
    exactly the same code that handles a child asking to practise. That matters:
    a second, hand-rolled "start a lesson" path is precisely how two behaviours
    drift apart.
    """
    curriculum = curriculum or get_curriculum()
    state = normalize_state(state)
    # set_level mutates in place and returns None, like set_mode. Assigning its
    # result would blank the session and silently drop the level.
    set_level(state, level)

    plan = plan_turn(state, "let's learn", curriculum=curriculum)
    state = plan.state

    # plan_turn records the child's message, but here the child typed nothing -
    # a reviewer pressed a button. Drop that synthetic turn rather than putting
    # words in the child's mouth in their own transcript.
    if state["turns"] and state["turns"][-1]["role"] == "child":
        state["turns"].pop()
        state["turn_count"] = max(0, int(state.get("turn_count", 1)) - 1)

    text = plan.fallback
    append_turn(
        state,
        role="bot",
        text=text,
        mode=plan.mode,
        item_id=plan.item.id if plan.item is not None else None,
    )

    debug = dict(plan.debug)
    debug["mode_reason"] = "level_switch"
    debug["fallback_used"] = True
    # plan_turn marks a Learning turn as model-eligible; this path deliberately
    # never calls the model, so the debug block must not claim otherwise.
    debug["use_llm"] = False
    return TurnResult(reply=text, state=state, debug=debug)
