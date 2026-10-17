"""Deterministic progression: mastery, promotion and easing.

All rules are plain arithmetic over the session state so they can be unit
tested and reasoned about. The LLM never influences any of this.

Rules (mirroring the design doc):

* **Promote** to the next level once the child has made at least 8 attempts at
  the current level and answered at least 4 of the last 5 correctly.
* **Ease** after 3 consecutive misses: give a hint and re-serve the item the
  child is struggling with most.
* **Demote** if, after at least 6 attempts, the child is getting almost
  nothing right at the current level (at most 1 correct in the last 6).
* Each item keeps a mastery box in ``0..3``: correct moves it up, a miss moves
  it down. Weak items are re-served later in the session and in future sessions.
"""

from __future__ import annotations

from dataclasses import dataclass

from .text import normalize

PROMOTE_MIN_ATTEMPTS = 8
PROMOTE_WINDOW = 5
PROMOTE_MIN_CORRECT = 4

EASE_MISS_STREAK = 3

DEMOTE_MIN_ATTEMPTS = 6
DEMOTE_WINDOW = 6
DEMOTE_MAX_CORRECT = 1

MAX_BOX = 3
MIN_BOX = 0

MAX_LEVEL = 3
MIN_LEVEL = 1


@dataclass(frozen=True)
class Progression:
    """What changed for the child after this attempt."""

    promoted: bool = False
    demoted: bool = False
    eased: bool = False
    hint: bool = False
    reason: str = ""

    @property
    def level_changed(self) -> bool:
        return self.promoted or self.demoted


def record_result(state: dict, item_id: str | None, correct: bool, now: str) -> None:
    """Fold one scored attempt into mastery and the accuracy window.

    Only called when the child actually produced an answer (``correct`` is a
    real ``True``/``False``). Non-answers are handled by the engine and never
    count as an attempt, so a child cannot promote by spamming the send button.
    """
    state["attempts_at_level"] = min(state.get("attempts_at_level", 0) + 1, 10_000)
    window = list(state.get("recent_results") or [])
    window.append(bool(correct))
    state["recent_results"] = window[-10:]

    if correct:
        state["correct_streak"] = state.get("correct_streak", 0) + 1
        state["miss_streak"] = 0
    else:
        state["correct_streak"] = 0
        state["miss_streak"] = state.get("miss_streak", 0) + 1

    if not item_id:
        return

    entry = state["mastery"].setdefault(
        item_id, {"box": 0, "attempts": 0, "correct": 0, "last_seen": None}
    )
    entry["attempts"] = entry.get("attempts", 0) + 1
    if correct:
        entry["correct"] = entry.get("correct", 0) + 1
        entry["box"] = min(MAX_BOX, entry.get("box", 0) + 1)
    else:
        entry["box"] = max(MIN_BOX, entry.get("box", 0) - 1)
    entry["last_seen"] = now


def should_promote(state: dict) -> bool:
    if state.get("level", 1) >= MAX_LEVEL:
        return False
    if state.get("attempts_at_level", 0) < PROMOTE_MIN_ATTEMPTS:
        return False
    window = list(state.get("recent_results") or [])[-PROMOTE_WINDOW:]
    if len(window) < PROMOTE_WINDOW:
        return False
    return sum(1 for r in window if r) >= PROMOTE_MIN_CORRECT


def should_demote(state: dict) -> bool:
    if state.get("level", 1) <= MIN_LEVEL:
        return False
    if state.get("attempts_at_level", 0) < DEMOTE_MIN_ATTEMPTS:
        return False
    window = list(state.get("recent_results") or [])[-DEMOTE_WINDOW:]
    if len(window) < DEMOTE_WINDOW:
        return False
    return sum(1 for r in window if r) <= DEMOTE_MAX_CORRECT


def apply_progression(state: dict) -> Progression:
    """Apply promotion / easing / demotion after an attempt was recorded.

    Promotion wins over easing, easing wins over demotion.
    """
    if should_promote(state):
        state["level"] = min(MAX_LEVEL, state["level"] + 1)
        state["attempts_at_level"] = 0
        state["recent_results"] = []
        state["miss_streak"] = 0
        state["correct_streak"] = 0
        state["promote_count"] = state.get("promote_count", 0) + 1
        return Progression(promoted=True, reason="promoted_4_of_last_5_after_8_attempts")

    if state.get("miss_streak", 0) >= EASE_MISS_STREAK:
        state["miss_streak"] = 0
        state["ease_count"] = state.get("ease_count", 0) + 1
        return Progression(eased=True, hint=True, reason="eased_after_3_consecutive_misses")

    if should_demote(state):
        state["level"] = max(MIN_LEVEL, state["level"] - 1)
        state["attempts_at_level"] = 0
        state["recent_results"] = []
        state["miss_streak"] = 0
        state["correct_streak"] = 0
        return Progression(demoted=True, hint=True, reason="demoted_after_6_attempts_nearly_all_missed")

    return Progression()


def accuracy(state: dict, window: int = 5) -> float:
    results = list(state.get("recent_results") or [])[-window:]
    if not results:
        return 0.0
    return round(sum(1 for r in results if r) / len(results), 3)


def select_item(curriculum, state: dict, hint: bool = False):
    """Choose the next item deterministically.

    Struggling items (mastery box <= 1) are served first, then the remaining
    items rotate by a per-session cursor so the child sees variety. No global
    randomness is involved, which keeps tests and demos reproducible.

    With ``hint=True`` the current item is left in the pool so the child retries
    the item they just missed, with help.
    """
    level = int(state.get("level", 1))
    pool = list(curriculum.items(level))
    if not pool:
        for fallback_level in range(level - 1, 0, -1):
            pool = list(curriculum.items(fallback_level))
            if pool:
                break
    if not pool:
        return None

    def box_of(item) -> int:
        return state["mastery"].get(item.id, {}).get("box", 0)

    weak = [i for i in pool if box_of(i) <= 1]
    strong = [i for i in pool if box_of(i) > 1]

    if strong:
        cursor = int(state.get("item_cursor", 0)) % len(strong)
        strong = strong[cursor:] + strong[:cursor]

    ordered = weak + strong
    state["item_cursor"] = int(state.get("item_cursor", 0)) + 1

    if hint:
        # Most-struggling item, retried with help. Ties keep curriculum order.
        chosen = min(ordered, key=lambda i: box_of(i))
    else:
        last = state.get("current_item")
        candidates = [i for i in ordered if i.id != last] or ordered
        chosen = candidates[0]

    state["current_item"] = chosen.id
    return chosen


def is_repeat_of_last_text(state: dict, text: str) -> bool:
    previous = normalize(state.get("last_child_text") or "")
    current = normalize(text)
    return bool(previous) and previous == current
