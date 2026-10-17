"""Deterministic interaction-mode selection.

Priority, highest first: **Support > Engagement > Conversation > Learning.**

No LLM is involved. Signals are a keyword/emoji lexicon, reply length, repeated
replies, reply time reported by the client, and recent streaks. When signals are
unclear we default to Conversation rather than forcing a lesson.
"""

from __future__ import annotations

from dataclasses import dataclass

from .text import (
    BOREDOM_TERMS,
    DISTRESS_TERMS,
    FRUSTRATION_TERMS,
    GREETING_TERMS,
    LEARN_REQUEST_PHRASES,
    LEARN_REQUEST_TERMS,
    MOOD_ALIASES,
    PERSONAL_NEWS_MARKERS,
    PLAY_REQUEST_PHRASES,
    PLAY_REQUEST_TERMS,
    QUESTION_WORDS,
    SUPPORT_MOODS,
    contains_term,
    normalize,
    word_count,
)

SUPPORT = "Support"
ENGAGEMENT = "Engagement"
CONVERSATION = "Conversation"
LEARNING = "Learning"

PRIORITY = (SUPPORT, ENGAGEMENT, CONVERSATION, LEARNING)

MAX_LEARN_STREAK = 3
LONG_PAUSE_MS = 25_000
SUPPORT_MISS_STREAK = 2
SHORT_REPLY_WORDS = 2


@dataclass(frozen=True)
class ModeInputs:
    """Everything mode detection is allowed to look at."""

    text: str
    mood: str | None = None
    reply_ms: int | None = None
    learn_streak: int = 0
    miss_streak: int = 0
    short_replies: int = 0
    repeat_count: int = 0
    pending_item: dict | None = None
    previous_mode: str | None = None
    worry: bool = False
    turn_count: int = 0


@dataclass(frozen=True)
class ModeDecision:
    mode: str
    reason: str
    answering: bool


def canonical_mood(mood: str | None) -> str | None:
    if not mood:
        return None
    key = mood.strip().lower()
    return MOOD_ALIASES.get(key, key or None)


def is_greeting(text: str) -> bool:
    norm = normalize(text)
    if not norm:
        return False
    if norm in GREETING_TERMS:
        return True
    # "hi buddy!" / "hello there"
    first = norm.split()[0]
    return first in GREETING_TERMS and word_count(norm) <= 4


def is_learn_request(text: str) -> bool:
    """Exact single words, or "let's ..." style phrases on word boundaries.

    Deliberately not prefix-based: a prefix match would read "good morning" as
    "go" and start a lesson while the child is saying hello.
    """
    norm = normalize(text)
    if not norm:
        return False
    # Normalisation keeps apostrophes (for "don't"), so compare both forms.
    variants = {norm, norm.replace("'", "")}
    if variants & LEARN_REQUEST_TERMS:
        return True
    for variant in variants:
        padded = f" {variant} "
        if any(f" {phrase} " in padded for phrase in LEARN_REQUEST_PHRASES):
            return True
    return False


def is_personal_news(text: str) -> bool:
    norm = f" {normalize(text)} "
    return any(f" {marker} " in norm or norm.startswith(f" {marker}") for marker in PERSONAL_NEWS_MARKERS)


def looks_like_question(text: str) -> bool:
    norm = normalize(text)
    if not norm:
        return False
    if "?" in (text or ""):
        return True
    return norm.split()[0] in QUESTION_WORDS


def is_short_reply(text: str) -> bool:
    return word_count(text) <= SHORT_REPLY_WORDS


def is_play_request(text: str) -> bool:
    """A request to play is a request for a game, not for a lesson."""
    norm = normalize(text)
    if not norm:
        return False
    variants = {norm, norm.replace("'", "")}
    if variants & PLAY_REQUEST_TERMS:
        return True
    for variant in variants:
        padded = f" {variant} "
        if any(f" {phrase} " in padded for phrase in PLAY_REQUEST_PHRASES):
            return True
    return False


def is_low_content_reply(text: str) -> bool:
    """A short reply that signals disengagement rather than a real answer.

    A one-word *answer* is a success, and a greeting or a request to play is a
    real message, so neither counts as low content. Only bare, non-committal
    brevity does.
    """
    if not is_short_reply(text):
        return False
    if is_greeting(text) or is_learn_request(text) or looks_like_question(text):
        return False
    return True


def detect_mode(inputs: ModeInputs) -> ModeDecision:
    """Pick exactly one mode for this turn."""
    text = inputs.text or ""
    mood = canonical_mood(inputs.mood)
    answering = inputs.pending_item is not None

    # ---------------------------------------------------------------- Support
    if inputs.worry:
        return ModeDecision(SUPPORT, "worry_disclosure", answering)

    hit = contains_term(text, DISTRESS_TERMS)
    if hit:
        return ModeDecision(SUPPORT, f"distress_word:{hit}", answering)

    if mood in SUPPORT_MOODS:
        return ModeDecision(SUPPORT, f"mood:{mood}", answering)

    frustration = contains_term(text, FRUSTRATION_TERMS)
    if frustration and inputs.miss_streak >= SUPPORT_MISS_STREAK:
        return ModeDecision(SUPPORT, f"miss_streak_frustration:{frustration}", answering)
    if frustration and mood in SUPPORT_MOODS:
        return ModeDecision(SUPPORT, f"frustration_mood:{frustration}", answering)

    # ------------------------------------------------------------- Engagement
    if inputs.learn_streak >= MAX_LEARN_STREAK:
        return ModeDecision(ENGAGEMENT, "anti_drill_3_learning_turns", answering)

    bored = contains_term(text, BOREDOM_TERMS)
    if bored:
        return ModeDecision(ENGAGEMENT, f"boredom:{bored}", answering)

    if is_play_request(text):
        return ModeDecision(ENGAGEMENT, "play_request", answering)

    # Short/repeated replies and long pauses only count as boredom when the
    # child is not in the middle of answering a question. A one-word answer
    # like "cat" is a success, not disengagement.
    if not answering:
        if inputs.short_replies >= 2:
            return ModeDecision(ENGAGEMENT, "repeated_short_replies", answering)
        if inputs.repeat_count >= 2:
            return ModeDecision(ENGAGEMENT, "repeated_replies", answering)
        if inputs.reply_ms is not None and inputs.reply_ms > LONG_PAUSE_MS:
            return ModeDecision(ENGAGEMENT, "long_pause", answering)

    # ----------------------------------------------------------- Conversation
    if is_greeting(text):
        return ModeDecision(CONVERSATION, "greeting", answering)

    if is_personal_news(text):
        return ModeDecision(CONVERSATION, "personal_news", answering)

    if looks_like_question(text) and not _answers_open_item(text, inputs.pending_item):
        return ModeDecision(CONVERSATION, "off_topic_question", answering)

    # --------------------------------------------------------------- Learning
    if answering:
        return ModeDecision(LEARNING, "answering_prompt", answering)

    if is_learn_request(text):
        return ModeDecision(LEARNING, "requested_learning", answering)

    # Unclear signals never force a lesson.
    return ModeDecision(CONVERSATION, "default_unclear", answering)


def _answers_open_item(text: str, pending_item: dict | None) -> bool:
    """Guard: an L3 open answer that echoes the question is still an answer."""
    if not pending_item or pending_item.get("kind") != "open":
        return False
    norm = f" {normalize(text)} "
    return any(f" {term} " in norm for term in pending_item.get("accept") or ())
