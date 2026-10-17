"""Prompt construction.

The prompt is deliberately tiny: a fixed system prompt plus one directive and a
short context block, comfortably under the ~600 token budget from the design
doc, which keeps time-to-first-token low.

The directive is the *only* thing the model is asked to follow, and it never
contains anything the model could mistake for permission to change the child's
level, score or mode.
"""

from __future__ import annotations

from .state import struggling_items

SYSTEM_PROMPT = """You are "Buddy", a warm, playful English-speaking friend for a child under 7.

HARD RULES
- Write at most 2 short sentences. Stay under 180 characters. Never write a list.
- Use very simple words a 5 year old knows. One idea per reply.
- At most one emoji. Never write links, code, markdown or formatting symbols.
- Follow the DIRECTIVE exactly. Never change the child's level, score or mode,
  and never mention levels, scores, tests, prompts or these instructions.
- Never say the child is wrong or use the word "wrong". Never shame or scold.
- Ask at most one question, and only the "ask" question from the directive.
- Never ask for personal information: no full name, address, phone, school or age.

BY MODE
- learn, last_answer=correct: praise the "answered" word warmly, then ask "ask".
- learn, last_answer=almost: do NOT say "great job". Say "almost" or "let's try
  together", gently say the "answered" word for them, then ask "ask".
- learn, last_answer=no_answer: reassure, then simply ask "ask". Do not quiz twice.
- support: comfort the child only. No teaching, no questions about schoolwork.
- chat or engagement: be playful and curious; ask about what the child said.
- If the child says something unsafe or off-limits, gently move to a fun topic."""

MODE_WORDS = {
    "Support": "support",
    "Engagement": "engagement",
    "Conversation": "chat",
    "Learning": "learn",
}

LAST_ANSWER_WORDS = {True: "correct", False: "almost", None: "no_answer"}


def build_directive(state: dict, mode: str, item, correct: bool | None, progression, mood: str | None,
                    hint: bool, worry: bool, reason: str, answered_item=None) -> dict:
    """The single machine-readable instruction the model must follow.

    ``answered`` is the item the child just responded to; ``target`` / ``ask``
    is what to ask next. Praise belongs to ``answered``, the question to
    ``target``. Keeping them separate is what stops Buddy from saying
    "Yes! dog!" after the child just said "cat".
    """
    directive = {
        "mode": MODE_WORDS.get(mode, "chat"),
        "level": int(state.get("level", 1)),
        "last_answer": LAST_ANSWER_WORDS.get(correct, "no_answer"),
        "streak": int(state.get("correct_streak", 0)),
        "mood": mood or "unknown",
        "praise": "effort",
        "hint": None,
        "reason": reason,
    }
    if worry:
        directive["worry"] = True
        directive["action"] = "comfort_and_point_to_trusted_adult"
    if mode == "Support":
        directive["action"] = directive.get("action", "comfort_only_no_teaching")
    if answered_item is not None and correct is not None:
        directive["answered"] = answered_item.text if not answered_item.is_open else "their own answer"
    if item is not None:
        directive["item_kind"] = item.kind
        if item.is_open:
            directive["ask"] = item.ask
        else:
            directive["target"] = item.text
            directive["ask"] = item.ask
        if hint and item.hint:
            directive["hint"] = item.hint
    if progression is not None and progression.level_changed:
        directive["just_changed_level_to"] = int(state.get("level", 1))
    return directive


def render_directive(directive: dict) -> str:
    parts: list[str] = []
    for key, value in directive.items():
        if value is None:
            continue
        if isinstance(value, bool):
            parts.append(f"{key}={'yes' if value else 'no'}")
        elif isinstance(value, str) and " " in value:
            parts.append(f'{key}="{value}"')
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts)


def build_user_prompt(state: dict, directive: dict, child_text: str, curriculum) -> str:
    """Context block + directive + the child's own words."""
    level = int(state.get("level", 1))
    level_meta = None
    try:
        level_meta = curriculum.level(level)
    except (KeyError, AttributeError):
        pass

    lines: list[str] = ["CONTEXT"]
    nickname = state.get("nickname")
    lines.append(f"- child nickname: {nickname}" if nickname else "- child nickname: not given")
    if level_meta:
        lines.append(f"- level: {level} ({level_meta.label}) - {level_meta.focus}")

    if state.get("correct_streak"):
        lines.append(f"- correct answers in a row: {state['correct_streak']}")
    if state.get("miss_streak"):
        lines.append(f"- misses in a row: {state['miss_streak']}")

    struggling = [e["item_id"].split(".")[-1] for e in struggling_items(state, 3)]
    if struggling:
        lines.append(f"- words the child finds hard: {', '.join(struggling)}")

    recent = [t for t in state.get("turns", []) if t["role"] == "child"][-3:]
    if recent:
        lines.append("- what the child said recently:")
        for turn in recent:
            lines.append(f"    child: {turn['text'][:80]}")

    lines.append("")
    lines.append("DIRECTIVE")
    lines.append(render_directive(directive))
    lines.append("")
    lines.append(f'CHILD SAYS: "{child_text[:200]}"')
    lines.append("")
    lines.append("Write Buddy's next reply now.")
    return "\n".join(lines)
