"""Deterministic, child-safe replies used when the LLM is slow or unavailable.

These are the safety net for the whole system: every branch the engine can take
has a hand-written reply that is short, warm, age-appropriate and free of links
or personal data. They are also the replies used for Support mode, where we
deliberately never call the model.
"""

from __future__ import annotations

from dataclasses import dataclass

from .modes import CONVERSATION, ENGAGEMENT, LEARNING, SUPPORT
from .text import first_sentence

# Word count / length kept small so a fallback always fits the output budget.
MAX_FALLBACK_CHARS = 280


@dataclass(frozen=True)
class FallbackContext:
    mode: str
    level: int = 1
    nickname: str | None = None
    # The item the child is asked next.
    target: str = ""
    ask: str = ""
    hint: str = ""
    # The item the child just answered (may differ from ``target``).
    answered: str = ""
    answered_hint: str = ""
    next_ask: str = ""
    correct: bool | None = None
    streak: int = 0
    mood: str | None = None
    worry: bool = False
    turn_count: int = 0
    reason: str = ""


def _pick(options: list[str], index: int) -> str:
    return options[index % len(options)]


def _name(ctx: FallbackContext) -> str:
    return f", {ctx.nickname}" if ctx.nickname else ""


# --------------------------------------------------------------------------
# Support: comfort first, never teach a distressed child
# --------------------------------------------------------------------------

SUPPORT_WORRY = (
    "Thank you for telling me. That sounds really hard. "
    "Please tell a grown-up you trust, like your mom or dad. 💙"
)

SUPPORT_TEMPLATES = {
    "sad": [
        "It is okay to feel sad. I am right here with you. 💙 Let's take one slow breath together.",
        "Thank you for telling me. Sad feelings come and go. I am staying with you. 💙",
    ],
    "angry": [
        "It is okay to feel angry. Let's take a big breath together, in and out. 💙",
        "Thank you for telling me. Big feelings are okay. I am here with you. 💙",
    ],
    "scared": [
        "It is okay to feel scared. You are safe here with me. 💙 Let's breathe slowly together.",
        "Thank you for telling me. I am here, and you are safe. 💙",
    ],
    "default": [
        "It is okay to feel that way. I am right here with you. 💙 Let's take a slow breath together.",
        "Thank you for telling me. Your feelings matter. I am staying with you. 💙",
    ],
    "frustrated": [
        "That was tricky, and you still tried! Let's do an easier one together. You are doing great. 💛",
        "Trying is the brave part. Let's slow down and do it together. 💛",
    ],
}


# --------------------------------------------------------------------------
# Engagement: play, rhythm, guessing games
# --------------------------------------------------------------------------

ENGAGEMENT_TEMPLATES = (
    "Let's play a game! I am thinking of an animal that says meow. Can you guess? 🐱",
    "Let's make a silly rhyme! Cat, cat, wearing a hat! Can you say it with me? 🧢",
    "Let's clap a rhythm! Clap, clap, clap clap clap! Can you do it too? 👏",
    "I spy something blue. It is up in the sky. Can you guess what it is? 🔵",
    "Tap the face that you feel right now: happy, sleepy, sad or grumpy! Then tell me why. 🙂",
    "Let's wiggle our fingers and count to three! One, two, three! 🎉",
)


# --------------------------------------------------------------------------
# Conversation: warm chat, at most one simple word woven in
# --------------------------------------------------------------------------

GREETING_TEMPLATES = (
    "Hi{name}! I am Buddy! 🧸 How do you feel today?",
    "Hello{name}! Buddy here! 🧸 How are you feeling today?",
)

CONVERSATION_TEMPLATES = (
    "That sounds fun{name}! Tell me more. 💬",
    "Ooh, nice{name}! What happened next? 💬",
    "I love hearing that{name}! Here is a word we can say: sun. Can you say sun? ☀️",
    "Thank you for telling me{name}! What do you like about it? 💬",
)


# --------------------------------------------------------------------------
# Learning
# --------------------------------------------------------------------------

CORRECT_WORD_TEMPLATES = (
    "Yes! {answered}! 🌟 {next}",
    "That is right{name}! {answered}! 🌟 {next}",
    "Well done{name}! {answered}! 🌟 {next}",
)

CORRECT_OPEN_TEMPLATES = (
    "That is a great answer{name}! 🌟 {next}",
    "Lovely talking with you{name}! 🌟 {next}",
)

STREAK_TEMPLATES = (
    "Wow{name}! {streak} in a row! 🌟 {next}",
    "Amazing{name}! {streak} correct in a row! 🎉 {next}",
)

ALMOST_TEMPLATES = (
    "Almost! {hint} {next} 💛",
    "So close{name}! {hint} {next} 💛",
)

NO_ANSWER_TEMPLATES = (
    "That is okay! Take your time. {next} 💛",
    "No rush at all{name}. {next} 💛",
)

HINT_TEMPLATES = (
    "Let's do it together, nice and slow. {hint} {next} 💛",
    "Here is a little help. {hint} {next} 💛",
)

ASK_TEMPLATES = (
    "{ask}",
    "{ask} You can do it!",
)


def fallback_reply(ctx: FallbackContext) -> str:
    """Build the deterministic reply for this turn."""
    text: str

    if ctx.mode == SUPPORT:
        if ctx.worry:
            text = SUPPORT_WORRY
        elif ctx.reason.startswith(("miss_streak_frustration", "frustration_mood")):
            text = _pick(SUPPORT_TEMPLATES["frustrated"], ctx.turn_count)
        else:
            key = ctx.mood if ctx.mood in SUPPORT_TEMPLATES else "default"
            text = _pick(SUPPORT_TEMPLATES[key], ctx.turn_count)

    elif ctx.mode == ENGAGEMENT:
        text = _pick(list(ENGAGEMENT_TEMPLATES), ctx.turn_count)

    elif ctx.mode == LEARNING:
        text = _learning_fallback(ctx)

    else:  # Conversation
        if ctx.reason == "greeting":
            text = _pick(list(GREETING_TEMPLATES), ctx.turn_count).format(name=_name(ctx))
        else:
            text = _pick(list(CONVERSATION_TEMPLATES), ctx.turn_count).format(name=_name(ctx))

    return _fit(text)


def next_prompt(ctx: FallbackContext) -> str:
    """What the child is asked next.

    A repeat item becomes \"Now can you say dog?\"; an open question is simply
    asked, because there is no single word to repeat.
    """
    if ctx.next_ask:
        return ctx.next_ask if ctx.next_ask.endswith(("?", "!", ".")) else ctx.next_ask + "?"
    if ctx.target:
        return f"Now can you say {ctx.target}?"
    return "What would you like to talk about?"


def _learning_fallback(ctx: FallbackContext) -> str:
    # The child is praised for the item they actually answered, and then asked
    # for the *next* item. Mixing the two up reads as nonsense to a child.
    answered = ctx.answered or ctx.target
    answered_hint = first_sentence(ctx.answered_hint) or first_sentence(ctx.hint) or "Listen carefully."
    next_ask = next_prompt(ctx)

    if ctx.correct is True:
        if ctx.streak >= 3:
            return _pick(list(STREAK_TEMPLATES), ctx.turn_count).format(
                name=_name(ctx), streak=ctx.streak, **{"next": next_ask}
            )
        templates = CORRECT_WORD_TEMPLATES if answered else CORRECT_OPEN_TEMPLATES
        return _pick(list(templates), ctx.turn_count).format(
            name=_name(ctx), answered=answered, **{"next": next_ask}
        )

    if ctx.correct is False:
        if ctx.reason == "eased":
            return _pick(list(HINT_TEMPLATES), ctx.turn_count).format(
                hint=first_sentence(ctx.hint) or "Let's try it slowly.", **{"next": next_ask}
            )
        return _pick(list(ALMOST_TEMPLATES), ctx.turn_count).format(
            name=_name(ctx), hint=answered_hint, **{"next": next_ask}
        )

    if ctx.correct is None and ctx.ask and ctx.reason in ("no_answer", "re_ask"):
        return _pick(list(NO_ANSWER_TEMPLATES), ctx.turn_count).format(
            name=_name(ctx), **{"next": next_ask}
        )

    ask = ctx.ask or f"Can you say {ctx.target or 'it'}?"
    return _pick(list(ASK_TEMPLATES), ctx.turn_count).format(ask=ask)


def _fit(text: str) -> str:
    text = " ".join(text.split())
    if len(text) <= MAX_FALLBACK_CHARS:
        return text
    return text[:MAX_FALLBACK_CHARS].rsplit(" ", 1)[0].rstrip() + " ..."


def greeting(state: dict) -> str:
    """Deterministic opening line, so the first screen is instant and offline-safe."""
    name = state.get("nickname")
    name_part = f", {name}" if name else ""
    return _fit(
        f"Hi{name_part}! I am Buddy, your English friend. 🧸 "
        "Tap a face to tell me how you feel, then we can talk or play!"
    )
