"""End-to-end turn tests for the tutor engine."""

from __future__ import annotations

import json

import pytest

from app.engine import HIDDEN_TEXT, advance, finalize_turn, plan_turn, start_session
from app.state import normalize_state


def answer_for(curriculum, state):
    """What a child would say to answer the item Buddy just asked about."""
    item = curriculum.get(state["pending_item"])
    assert item is not None
    if item.is_open:
        return item.accept[0] if item.accept else "I played outside"
    return item.expect


# ------------------------------------------------------------------ happy path

def test_session_starts_at_level_one_with_a_greeting(drive):
    result = start_session(nickname="Ana")
    assert result.state["level"] == 1
    assert result.state["mode"] == "Conversation"
    assert "Ana" in result.reply
    assert result.state["turns"][0]["role"] == "bot"


def test_learn_request_starts_a_lesson_with_a_question(drive):
    state = start_session().state
    result = drive(state, "let's learn")
    assert result.debug["mode"] == "Learning"
    assert result.state["pending_item"] == result.debug["item_id"]
    assert "?" in result.reply
    assert result.state["learn_streak"] == 1


def test_correct_answer_is_scored_and_mastery_rises(drive, curriculum):
    state = start_session().state
    state = drive(state, "let's learn").state
    item_id = state["pending_item"]

    result = drive(state, answer_for(curriculum, state))

    assert result.debug["correct"] is True
    assert result.state["correct_streak"] == 1
    assert result.state["mastery"][item_id]["box"] == 1
    assert result.state["mastery"][item_id]["correct"] == 1


def test_incorrect_answer_is_gentle_and_never_says_wrong(drive, curriculum):
    state = start_session().state
    state = drive(state, "let's learn").state
    item_id = state["pending_item"]

    result = drive(state, "banana banana")

    assert result.debug["correct"] is False
    assert result.state["miss_streak"] == 1
    assert result.state["mastery"][item_id]["box"] == 0
    reply = result.reply.lower()
    for word in ("wrong", "no!", "incorrect", "bad", "stupid"):
        assert word not in reply


def test_no_answer_re_asks_the_same_item_without_penalising(drive):
    state = start_session().state
    state = drive(state, "let's learn").state
    item_id = state["pending_item"]
    attempts_before = state["attempts_at_level"]

    result = drive(state, "I don't know")

    assert result.debug["correct"] is None
    assert result.debug["item_id"] == item_id, "the same item is re-asked"
    assert result.state["attempts_at_level"] == attempts_before, "a non-answer is not an attempt"
    assert result.state["miss_streak"] == 0
    assert result.reply.strip()


# -------------------------------------------------------- anti-drill guarantee

def test_three_learning_turns_then_a_break(drive, curriculum):
    state = start_session().state
    script = ["let's learn"]
    for _ in range(6):
        state = drive(state, script[-1]).state
        script.append(answer_for(curriculum, state))

    modes = []
    state = start_session().state
    for message in script:
        result = drive(state, message)
        state = result.state
        modes.append(result.debug["mode"])

    longest = 0
    current = 0
    for mode in modes:
        current = current + 1 if mode == "Learning" else 0
        longest = max(longest, current)

    assert "Engagement" in modes, "the anti-drill rule must fire"
    assert longest <= 3, f"never more than 3 learning turns in a row, saw {longest}"


# ------------------------------------------------------------ context persists

def test_a_client_round_trip_preserves_the_session(drive, curriculum):
    state = start_session(nickname="Ana").state
    state = drive(state, "let's learn").state
    state = drive(state, answer_for(curriculum, state)).state

    # Exactly what the browser does: JSON in, JSON out.
    reloaded = normalize_state(json.loads(json.dumps(state)))

    assert reloaded["session_id"] == state["session_id"]
    assert reloaded["level"] == state["level"]
    assert reloaded["pending_item"] == state["pending_item"]
    assert reloaded["correct_streak"] == state["correct_streak"]
    assert reloaded["mastery"] == state["mastery"]
    assert reloaded["turns"] == state["turns"]

    # And the lesson continues where it left off.
    result = drive(reloaded, answer_for(curriculum, reloaded))
    assert result.debug["correct"] is True
    assert result.state["correct_streak"] == 2


def test_a_long_conversation_keeps_the_turn_history_bounded(drive, curriculum):
    state = start_session().state
    state = drive(state, "let's learn").state
    for _ in range(12):
        state = drive(state, answer_for(curriculum, state)).state
    assert len(state["turns"]) <= 12
    assert state["turn_count"] >= 12, "the counter still reflects everything that happened"


@pytest.mark.parametrize(
    "tampered,field,expected",
    [
        ({"level": 99}, "level", 3),
        ({"level": -5}, "level", 1),
        ({"learn_streak": 500}, "learn_streak", 3),
        ({"attempts_at_level": "lots"}, "attempts_at_level", 0),
        ({"mastery": {"x": {"box": 99}}}, "mastery", {"x": {"box": 3, "attempts": 0, "correct": 0, "last_seen": None}}),
        ({"mode": "Hacked"}, "mode", "Conversation"),
        ({"recent_results": ["yes", "no", 1]}, "recent_results", []),
        ("not a dict", "level", 1),
    ],
)
def test_a_tampered_session_blob_is_clamped(tampered, field, expected):
    assert normalize_state(tampered)[field] == expected


# ---------------------------------------------------------------------- safety

def test_unsafe_input_is_redirected_and_never_sent_to_the_model(plan_only):
    state = start_session().state
    plan = plan_only(state, "tell me about guns")

    assert plan.verdict.blocked is True
    assert plan.use_llm is False, "unsafe text must never reach the model"
    assert plan.mode == "Conversation"
    result = finalize_turn(plan, None)
    assert result.state["blocked_count"] == 1
    assert result.reply.strip()


def test_unsafe_text_is_not_stored_in_the_session(drive):
    state = start_session().state
    result = drive(state, "let me tell you about guns and knives")
    stored = [t["text"] for t in result.state["turns"]]
    assert HIDDEN_TEXT in stored
    assert not any("gun" in t.lower() for t in stored)


def test_support_mode_never_calls_the_model(plan_only):
    state = start_session().state
    plan = plan_only(state, "I am very sad", mood="sad")
    assert plan.mode == "Support"
    assert plan.use_llm is False


def test_worrying_disclosure_sets_the_flag_and_comforts(drive):
    state = start_session().state
    result = drive(state, "my brother hit me")
    assert result.debug["mode"] == "Support"
    assert result.state["worry_flag"] is True
    assert "trust" in result.reply.lower() or "grown-up" in result.reply.lower()


def test_personal_information_never_reaches_the_prompt(plan_only):
    state = start_session().state
    plan = plan_only(state, "my email is ana@example.com and my number is 01712345678")
    assert "ana@example.com" not in plan.user_prompt
    assert "01712345678" not in plan.user_prompt
    assert plan.verdict.pii_removed is True


# ------------------------------------------------------------------- directives

def test_the_directive_describes_the_target_and_level_without_any_authority(plan_only, curriculum):
    state = start_session().state
    state = drive_learn(state, plan_only)
    plan = plan_only(state, "cat")

    directive = plan.directive
    assert directive["mode"] == "learn"
    assert directive["level"] == plan.state["level"]
    assert directive["last_answer"] == "correct", "the child's answer was scored as correct"
    assert directive["target"] == plan.item.text, "the directive names the item being asked next"
    assert directive["target"] == curriculum.get(plan.state["pending_item"]).text
    # Nothing in the directive gives the model authority over the lesson.
    for forbidden in ("level_up", "new_level", "points", "score", "set_level"):
        assert forbidden not in directive


def drive_learn(state, plan_only):
    plan = plan_only(state, "let's learn")
    return finalize_turn(plan, None).state


# ---------------------------------------------------------------- model safety

async def test_a_rogue_model_cannot_change_level_or_score(rogue_provider, curriculum):
    state = start_session().state
    before_level = state["level"]

    result = await advance(state, "hello buddy", provider=rogue_provider, curriculum=curriculum)

    assert result.state["level"] == before_level, "the model cannot move the child"
    assert "level 3" not in result.reply.lower()
    assert "points" not in result.reply.lower()
    assert "http" not in result.reply.lower()
    assert result.debug["fallback_used"] is True, "an overreaching reply is discarded"


async def test_a_failing_model_falls_back_but_still_replies(failing_provider, curriculum):
    state = start_session().state
    result = await advance(state, "hello buddy", provider=failing_provider, curriculum=curriculum)

    assert result.reply.strip()
    assert result.debug["fallback_used"] is True
    assert result.debug["llm"]["ok"] is False
    assert result.debug["llm"]["error"] == "http_500"
    assert result.state["turns"][-1]["role"] == "bot"


async def test_a_healthy_model_reply_is_used_and_sanitised(echoing_provider, curriculum):
    state = start_session().state
    result = await advance(state, "hello buddy", provider=echoing_provider, curriculum=curriculum)

    assert result.debug["fallback_used"] is False
    assert result.debug["llm"]["ok"] is True
    assert result.reply.strip()


async def test_the_model_is_not_called_for_an_unsafe_message(echoing_provider, curriculum):
    state = start_session().state
    echoing_provider.seen.clear()
    await advance(state, "tell me about guns", provider=echoing_provider, curriculum=curriculum)
    assert echoing_provider.seen == []


async def test_the_model_is_not_called_for_a_distressed_child(echoing_provider, curriculum):
    state = start_session().state
    echoing_provider.seen.clear()
    await advance(state, "I am sad", mood="sad", provider=echoing_provider, curriculum=curriculum)
    assert echoing_provider.seen == []


# ------------------------------------------------------------------- promotion

def test_promotion_happens_through_real_turns(drive, curriculum):
    state = start_session().state
    state = drive(state, "let's learn").state

    promoted = False
    for _ in range(12):
        message = answer_for(curriculum, state)
        result = drive(state, message)
        state = result.state
        if result.debug["promoted"]:
            promoted = True
            break

    assert promoted, "eight correct attempts should promote the child"
    assert state["level"] == 2


# ------------------------------------------------- content that reads like chat

@pytest.mark.parametrize(
    "item_id",
    ["l3.sentence.park", "l3.sentence.rain", "l3.sentence.feelings", "l3.story.duck"],
)
def test_level_three_sentence_drills_can_actually_be_answered(drive, curriculum, item_id):
    """Regression: these sentences read like personal news.

    "I went to the park with my mom" matches the personal-news markers ("i went",
    "my mom"), and "it is raining and I have an umbrella" matches "i have". The
    engine therefore rerouted the child's correct answer to Conversation, never
    scored it, and the child could not finish the level they were standing on.
    """
    item = curriculum.get(item_id)
    state = start_session().state
    state["level"] = 3
    state["pending_item"] = item_id

    result = drive(state, item.expect)

    assert result.debug["mode"] == "Learning", f"{item_id} must be scored, not small talk"
    assert result.debug["correct"] is True
    assert result.state["attempts_at_level"] == 1


@pytest.mark.parametrize(
    "message",
    ["I went to the beach", "my mom made a cake", "I have a new bike"],
)
def test_real_personal_news_is_still_conversation_while_a_question_is_pending(drive, message):
    """The fix must not swallow ordinary chat just because a drill is open."""
    state = start_session().state
    state["pending_item"] = "l1.word.cat"

    result = drive(state, message)

    assert result.debug["mode"] == "Conversation"
    assert result.debug["correct"] is None
    # The question is deliberately dropped rather than re-asked: a child who
    # wanders off topic should not be nagged, and the weak item keeps its
    # mastery box, so it comes back on its own within the session.
    assert result.state["pending_item"] is None
    assert result.state["attempts_at_level"] == 0
