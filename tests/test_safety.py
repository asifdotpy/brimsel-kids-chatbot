"""Safety and child-appropriateness of everything a child can see."""

from __future__ import annotations

import pytest

from app.fallbacks import FallbackContext, fallback_reply
from app.modes import CONVERSATION, ENGAGEMENT, LEARNING, SUPPORT
from app.text import (
    MAX_INPUT_CHARS,
    MAX_OUTPUT_CHARS,
    MAX_OUTPUT_SENTENCES,
    BLOCK_TERMS,
    FALLBACK_SAFE_REPLY,
    attempts_item,
    detect_worry,
    evaluate_answer,
    normalize,
    sanitize_input,
    sanitize_output,
    sentence_count,
    similarity,
    strip_pii,
)
from app.curriculum import Item


# -------------------------------------------------------------- input safety

@pytest.mark.parametrize(
    "text",
    ["how do I kill someone", "I have a gun", "let's talk about sex", "I want drugs", "you are stupid"],
)
def test_unsafe_input_is_blocked_with_a_reason(text):
    verdict = sanitize_input(text)
    assert verdict.blocked is True
    assert verdict.block_reason.startswith("blocked_term:")


@pytest.mark.parametrize("text", ["cat", "the cat is big", "I like apples", "assault is a word?"])
def test_innocent_input_is_not_blocked(text):
    assert sanitize_input(text).blocked is False


def test_blocked_terms_do_not_match_inside_longer_words():
    # "war" must not fire on "warm" and "die" must not fire on "diet".
    assert sanitize_input("the sun is warm").blocked is False
    assert sanitize_input("my diet has fruit").blocked is False


def test_empty_input_is_blocked():
    assert sanitize_input("   ").blocked is True


@pytest.mark.parametrize(
    "text,leaked",
    [
        ("my email is ana@example.com", "ana@example.com"),
        ("call me on 01712345678", "01712345678"),
        ("I live at 42 Green Road", "42 Green Road"),
    ],
)
def test_personal_information_is_stripped(text, leaked):
    cleaned, removed = strip_pii(text)
    assert removed is True
    assert leaked not in cleaned
    norm_leaked = leaked.split()[-1].lower()
    assert norm_leaked not in normalize(cleaned)


def test_input_is_length_capped():
    verdict = sanitize_input("a" * (MAX_INPUT_CHARS + 500))
    assert verdict.too_long is True
    assert len(verdict.text) <= MAX_INPUT_CHARS


@pytest.mark.parametrize(
    "text",
    ["I am scared at home", "my brother hit me", "nobody loves me", "it hurts inside"],
)
def test_worrying_disclosures_are_flagged(text):
    assert detect_worry(text) is True
    assert sanitize_input(text).worry is True


def test_ordinary_sadness_is_not_a_worry_disclosure():
    assert sanitize_input("I am sad").worry is False


# ------------------------------------------------------------- output safety

def test_output_strips_links_and_markdown():
    safe, modified = sanitize_output("Great job! See more at http://example.com now")
    assert "http" not in safe
    assert modified is True


def test_output_sentence_budget_is_enforced():
    long_reply = "One. Two. Three. Four. Five."
    safe, modified = sanitize_output(long_reply)
    assert sentence_count(safe) <= MAX_OUTPUT_SENTENCES
    assert modified is True


def test_output_character_budget_is_enforced():
    safe, _ = sanitize_output("word " * 300)
    assert len(safe) <= MAX_OUTPUT_CHARS + 8


def test_output_with_a_banned_term_falls_back():
    safe, _ = sanitize_output("Let's talk about guns")
    assert safe == FALLBACK_SAFE_REPLY


def test_empty_model_output_falls_back():
    safe, modified = sanitize_output("")
    assert safe == FALLBACK_SAFE_REPLY
    assert modified is True


# ------------------------------------------------------------------ fallbacks

CONTEXTS = [
    FallbackContext(mode=SUPPORT, mood="sad", turn_count=0),
    FallbackContext(mode=SUPPORT, worry=True, turn_count=1),
    FallbackContext(mode=SUPPORT, reason="miss_streak_frustration:cant", turn_count=2),
    FallbackContext(mode=ENGAGEMENT, turn_count=0),
    FallbackContext(mode=ENGAGEMENT, turn_count=3),
    FallbackContext(mode=CONVERSATION, reason="greeting", turn_count=0),
    FallbackContext(mode=CONVERSATION, reason="default_unclear", turn_count=2, nickname="Ana"),
    FallbackContext(mode=LEARNING, correct=True, answered="cat", target="dog",
                    next_ask="Can you say dog?", turn_count=0),
    FallbackContext(mode=LEARNING, correct=True, answered="cat", target="dog",
                    next_ask="Can you say dog?", streak=3, turn_count=1),
    FallbackContext(mode=LEARNING, correct=True, target="", ask="What do you like to eat?",
                    next_ask="What is your favorite animal?", turn_count=2),
    FallbackContext(mode=LEARNING, correct=False, answered="cat", answered_hint="It says meow! It starts with c.",
                    target="dog", next_ask="Can you say dog?", turn_count=2),
    FallbackContext(mode=LEARNING, correct=False, answered="cat", hint="It says meow.",
                    target="cat", next_ask="Can you say cat?", reason="eased", turn_count=3),
    FallbackContext(mode=LEARNING, correct=None, ask="Can you say cat?", target="cat",
                    next_ask="Can you say cat?", reason="no_answer", turn_count=4),
    FallbackContext(mode=LEARNING, correct=None, ask="Can you say dog?", target="dog",
                    next_ask="Can you say dog?", turn_count=5),
]


@pytest.mark.parametrize("ctx", CONTEXTS)
def test_every_fallback_is_child_safe(ctx):
    reply = fallback_reply(ctx)
    assert reply.strip(), "a fallback must never be empty"
    assert len(reply) <= MAX_OUTPUT_CHARS + 8
    assert "http" not in reply.lower()
    assert "www." not in reply.lower()
    assert sentence_count(reply) <= 3
    assert not any(f" {term} " in f" {normalize(reply)} " for term in BLOCK_TERMS)
    # Nothing in a fallback should be replaced by the output filter.
    safe, _ = sanitize_output(reply, fallback="SENTINEL")
    assert safe != "SENTINEL", f"fallback was rejected by the output filter: {reply!r}"


def test_worry_fallback_points_to_a_trusted_adult():
    reply = fallback_reply(FallbackContext(mode=SUPPORT, worry=True))
    lowered = reply.lower()
    assert "grown-up" in lowered or "grown up" in lowered
    assert "trust" in lowered


def test_fallbacks_never_claim_to_diagnose():
    banned = ("you have", "diagnos", "therapy", "medicine", "depress")
    for ctx in CONTEXTS:
        reply = fallback_reply(ctx).lower()
        assert not any(term in reply for term in banned), reply


def test_support_fallbacks_do_not_start_a_lesson():
    """A distressed child is comforted, never quizzed."""
    for ctx in CONTEXTS:
        if ctx.mode != SUPPORT:
            continue
        reply = fallback_reply(ctx).lower()
        assert "can you say" not in reply
        assert "say it with me" not in reply


def test_praise_names_the_answered_item_and_the_question_names_the_next():
    reply = fallback_reply(FallbackContext(
        mode=LEARNING, correct=True, answered="cat", target="dog", next_ask="Can you say dog?",
    ))
    assert "cat" in reply.lower()
    assert "dog" in reply.lower()
    assert reply.lower().index("cat") < reply.lower().index("dog"), "praise first, then the next question"


def test_a_miss_teaches_the_missed_item_not_the_next_one():
    reply = fallback_reply(FallbackContext(
        mode=LEARNING, correct=False, answered="cat", answered_hint="It says meow.",
        target="dog", next_ask="Can you say dog?",
    ))
    assert "meow" in reply.lower(), "the hint belongs to the item the child just missed"
    assert "dog" in reply.lower(), "then the next item is asked"
    assert "wrong" not in reply.lower()


# ------------------------------------------------------------ answer scoring

def _item(kind="repeat", text="cat", expect="cat", accept=(), ask=None):
    return Item(
        id="t", level=1, kind=kind, text=text, ask=ask or text,
        expect=expect, accept=tuple(accept),
    )


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("cat", True),
        ("Cat!", True),
        ("a cat", True),
        ("I see a cat", True),
        ("caat", True),          # a four year old typo is still an attempt
        ("dog", False),
        ("", None),
        ("   ", None),
        ("I don't know", None),
        ("idk", None),
        ("😊", None),
    ],
)
def test_single_word_scoring(reply, expected):
    assert evaluate_answer(reply, _item()) is expected


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("the cat is big", True),
        ("cat is big", True),
        ("The cat is big!", True),
        ("the cat big", True),
        ("the dog is big", False),
        ("cat", False),
    ],
)
def test_phrase_scoring(reply, expected):
    item = _item(text="the cat is big", expect="the cat is big")
    assert evaluate_answer(reply, item) is expected


def test_a_letter_target_is_not_credited_by_a_longer_word():
    assert evaluate_answer("apple", _item(text="a", expect="a")) is False
    assert evaluate_answer("a", _item(text="a", expect="a")) is True


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("I like rice", True),
        ("rice", True),
        ("hmm", None),
        ("I like cars", False),      # a real attempt, but not on topic
        ("pizza and cake", False),   # two words, understood, not an accepted food
    ],
)
def test_open_question_scoring(reply, expected):
    item = _item(kind="open", text="what do you like to eat", expect="", accept=["rice", "apple"])
    assert evaluate_answer(reply, item) is expected


# ------------------------------------------------- is this an attempt at all?

SENTENCE = "i went to the park with my mom"


@pytest.mark.parametrize(
    "reply,expected",
    [
        # A drill that reads like personal news is still the drill.
        ("I went to the park with my mom", True),
        ("it is raining and i have an umbrella", False),   # a different item's text
        ("I went to the park with my mother", True),        # near-miss of the sentence
        # Genuine small talk is not an attempt.
        ("I went to the beach", False),
        ("my mom made a cake", False),
        ("hello", False),
        ("I don't know", False),
        ("", False),
    ],
)
def test_recognising_an_attempt_at_a_sentence_prompt(reply, expected):
    """``attempts_item`` answers "did they mean to answer?", never "was it right?"."""
    item = _item(text=SENTENCE, expect=SENTENCE)
    assert attempts_item(reply, item) is expected


@pytest.mark.parametrize(
    "reply,expected",
    [
        ("I have a cat", True),          # a level-1 word inside a chatty sentence
        ("cat", True),
        ("the cat is big now", True),
        ("I have a new bike", False),    # personal news that contains no target
        ("dog", False),
    ],
)
def test_recognising_an_attempt_at_a_single_word_prompt(reply, expected):
    assert attempts_item(reply, _item()) is expected


def test_an_open_item_is_never_an_attempt_here():
    """Open questions are recognised by their accept list elsewhere."""
    item = _item(kind="open", text="what do you like to eat", expect="", accept=["rice"])
    assert attempts_item("I like rice", item) is False
