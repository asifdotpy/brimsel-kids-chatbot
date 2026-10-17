"""Mode detection: all four modes, their priority, and the anti-drill rule."""

from __future__ import annotations

import pytest

from app.modes import (
    CONVERSATION,
    ENGAGEMENT,
    LEARNING,
    MAX_LEARN_STREAK,
    SUPPORT,
    ModeInputs,
    detect_mode,
    is_learn_request,
    is_low_content_reply,
    is_play_request,
)

REPEAT_ITEM = {"id": "l1.word.cat", "kind": "repeat", "accept": []}
OPEN_ITEM = {"id": "l3.open.food", "kind": "open", "accept": ["rice", "apple"]}


def mode_of(text, **kwargs):
    return detect_mode(ModeInputs(text=text, **kwargs)).mode


# --------------------------------------------------------------- all four modes

@pytest.mark.parametrize(
    "text,expected",
    [
        ("I am sad", SUPPORT),
        ("I feel scared", SUPPORT),
        ("this is boring", ENGAGEMENT),
        ("hello buddy", CONVERSATION),
        ("let's learn", LEARNING),
    ],
)
def test_each_mode_is_reachable(text, expected):
    assert mode_of(text) == expected


def test_support_from_a_mood_tap_without_any_words():
    assert mode_of("", mood="sad") == SUPPORT
    assert mode_of("", mood="scared") == SUPPORT


def test_learning_when_answering_a_pending_question():
    decision = detect_mode(ModeInputs(text="cat", pending_item=REPEAT_ITEM))
    assert decision.mode == LEARNING
    assert decision.reason == "answering_prompt"
    assert decision.answering is True


# ------------------------------------------------------------------- priority

def test_support_beats_engagement():
    assert mode_of("this is boring and I am sad") == SUPPORT


def test_support_beats_conversation():
    assert mode_of("hi, I am angry today") == SUPPORT


def test_support_beats_learning_when_answering():
    decision = detect_mode(ModeInputs(text="I can't do it", pending_item=REPEAT_ITEM, miss_streak=2))
    assert decision.mode == SUPPORT
    assert decision.answering is True, "the pending item is still reported"


def test_engagement_beats_conversation():
    assert mode_of("hello, this is boring") == ENGAGEMENT


def test_engagement_beats_learning():
    decision = detect_mode(ModeInputs(text="cat", pending_item=REPEAT_ITEM, learn_streak=MAX_LEARN_STREAK))
    assert decision.mode == ENGAGEMENT
    assert decision.reason == "anti_drill_3_learning_turns"


def test_greeting_beats_learning():
    decision = detect_mode(ModeInputs(text="hello", pending_item=REPEAT_ITEM))
    assert decision.mode == CONVERSATION
    assert decision.reason == "greeting"


def test_personal_news_beats_learning():
    decision = detect_mode(ModeInputs(text="I went to the park", pending_item=REPEAT_ITEM))
    assert decision.mode == CONVERSATION
    assert decision.reason == "personal_news"


def test_off_topic_question_beats_learning():
    decision = detect_mode(ModeInputs(text="why is the sky blue?", pending_item=REPEAT_ITEM))
    assert decision.mode == CONVERSATION
    assert decision.reason == "off_topic_question"


def test_open_answer_that_echoes_the_question_still_counts_as_learning():
    decision = detect_mode(ModeInputs(text="can I say rice?", pending_item=OPEN_ITEM))
    assert decision.mode == LEARNING


# ------------------------------------------------------------ default + guard

def test_unclear_signals_default_to_conversation():
    # No greeting, no news, no question, no pending item, and no lesson request.
    decision = detect_mode(ModeInputs(text="purple elephant umbrella"))
    assert decision.mode == CONVERSATION
    assert decision.reason == "default_unclear"


def test_a_short_answer_is_not_treated_as_boredom():
    decision = detect_mode(ModeInputs(text="cat", pending_item=REPEAT_ITEM, short_replies=5))
    assert decision.mode == LEARNING, "a one-word answer is a success, not disengagement"


def test_repeated_short_replies_trigger_engagement_when_nothing_was_asked():
    decision = detect_mode(ModeInputs(text="mmm", short_replies=2))
    assert decision.mode == ENGAGEMENT
    assert decision.reason == "repeated_short_replies"


def test_long_pause_triggers_engagement_when_nothing_was_asked():
    assert mode_of("hmm", reply_ms=40_000) == ENGAGEMENT


def test_long_pause_does_not_interrupt_an_answer():
    decision = detect_mode(ModeInputs(text="cat", pending_item=REPEAT_ITEM, reply_ms=40_000))
    assert decision.mode == LEARNING


# ------------------------------------------------------------------ helpers

@pytest.mark.parametrize(
    "text",
    ["ok", "yes", "let's learn", "teach me english", "practice english"],
)
def test_learn_requests_are_recognised(text):
    assert is_learn_request(text) is True


@pytest.mark.parametrize(
    "text", ["good morning", "hello", "cat", "I see a dog", "goat", "start over please"],
)
def test_learn_requests_are_not_over_matched(text):
    assert is_learn_request(text) is False


@pytest.mark.parametrize("text", ["let's play", "play a game", "play with me", "game"])
def test_play_requests_are_engagement_not_lessons(text):
    assert is_play_request(text) is True
    assert is_learn_request(text) is False
    assert mode_of(text) == ENGAGEMENT


def test_greetings_are_not_low_content():
    assert is_low_content_reply("hello") is False
    assert is_low_content_reply("let's learn") is False
    assert is_low_content_reply("why?") is False
    assert is_low_content_reply("mmm") is True
