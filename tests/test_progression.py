"""Progression: promotion, easing, demotion, mastery and item selection."""

from __future__ import annotations

import pytest

from app.progression import (
    DEMOTE_MAX_CORRECT,
    DEMOTE_MIN_ATTEMPTS,
    EASE_MISS_STREAK,
    PROMOTE_MIN_ATTEMPTS,
    PROMOTE_MIN_CORRECT,
    PROMOTE_WINDOW,
    accuracy,
    apply_progression,
    record_result,
    select_item,
    should_demote,
    should_promote,
)
from app.state import new_state, utcnow

ITEM = "l1.word.cat"


def _attempts(state, results, item=ITEM):
    """Record a sequence of results, applying progression after each."""
    outcomes = []
    for i, correct in enumerate(results):
        record_result(state, item, correct, utcnow())
        outcomes.append(apply_progression(state))
    return outcomes


# ---------------------------------------------------------------- promotion

def test_does_not_promote_before_eight_attempts():
    state = new_state()
    for _ in range(PROMOTE_MIN_ATTEMPTS - 1):
        record_result(state, ITEM, True, utcnow())
    assert state["attempts_at_level"] == PROMOTE_MIN_ATTEMPTS - 1
    assert not should_promote(state)


def test_promotes_after_eight_attempts_and_four_of_last_five():
    state = new_state()
    results = [False, False, False] + [True] * 5
    outcomes = _attempts(state, results)
    assert any(o.promoted for o in outcomes)
    assert state["level"] == 2
    assert state["attempts_at_level"] == 0
    assert outcomes[-1].reason == "promoted_4_of_last_5_after_8_attempts"


def test_does_not_promote_with_only_three_of_last_five_correct():
    state = new_state()
    state["attempts_at_level"] = PROMOTE_MIN_ATTEMPTS - 1
    # Four of the last five are needed; give it only three.
    for correct in [True, True, False, True, False]:
        state["recent_results"].append(correct)
    assert sum(1 for r in state["recent_results"] if r) == PROMOTE_MIN_CORRECT - 1

    record_result(state, ITEM, False, utcnow())
    assert state["attempts_at_level"] == PROMOTE_MIN_ATTEMPTS
    assert not should_promote(state)
    assert apply_progression(state).promoted is False


def test_never_promotes_past_level_three():
    state = new_state()
    state["level"] = 3
    for _ in range(10):
        record_result(state, "l3.open.food", True, utcnow())
    assert not should_promote(state)
    assert apply_progression(state).promoted is False
    assert state["level"] == 3


# ------------------------------------------------------------------- easing

def test_eases_after_three_consecutive_misses():
    state = new_state()
    outcomes = _attempts(state, [False, False, False])
    assert not outcomes[0].eased
    assert not outcomes[1].eased
    assert outcomes[2].eased
    assert outcomes[2].hint is True
    assert state["ease_count"] == 1
    assert state["miss_streak"] == 0, "the miss streak resets so easing does not re-trigger every turn"


def test_does_not_ease_after_only_two_misses():
    state = new_state()
    outcomes = _attempts(state, [False, False])
    assert not any(o.eased for o in outcomes)
    assert state["miss_streak"] == EASE_MISS_STREAK - 1


def test_a_correct_answer_clears_the_miss_streak():
    state = new_state()
    _attempts(state, [False, False, True, False, False])
    assert state["miss_streak"] == 2
    assert state["ease_count"] == 0


# ------------------------------------------------------------------ demotion

def test_demotes_when_the_level_is_clearly_too_hard():
    state = new_state()
    state["level"] = 2
    # Three misses ease once, then a correct, then two more misses: six attempts
    # with only one correct, and no active three-miss streak left to ease.
    outcomes = _attempts(state, [False, False, False, True, False, False])

    assert outcomes[2].eased is True, "the third miss eases first"
    assert outcomes[-1].demoted is True, "the sustained failure then demotes"
    assert state["level"] == 1
    assert state["attempts_at_level"] == 0, "the level counter resets on a move"


def test_does_not_demote_below_level_one():
    state = new_state()
    _attempts(state, [False] * 8)
    assert state["level"] == 1
    assert not should_demote(state)


# ------------------------------------------------------------------- mastery

def test_mastery_box_rises_and_falls_and_stays_in_range():
    state = new_state()
    for _ in range(6):
        record_result(state, ITEM, True, utcnow())
    assert state["mastery"][ITEM]["box"] == 3, "box is capped at 3"

    for _ in range(6):
        record_result(state, ITEM, False, utcnow())
    assert state["mastery"][ITEM]["box"] == 0, "box is floored at 0"
    assert state["mastery"][ITEM]["attempts"] == 12


def test_accuracy_window_is_bounded():
    state = new_state()
    for correct in [True, False] * 20:
        record_result(state, ITEM, correct, utcnow())
    assert len(state["recent_results"]) <= 10
    assert state["attempts_at_level"] == 40
    assert accuracy(state, 5) in (0.4, 0.6)


# ------------------------------------------------------------ item selection

def test_selection_is_deterministic_and_does_not_repeat_immediately():
    state = new_state()
    curriculum = __import__("app.curriculum", fromlist=["get_curriculum"]).get_curriculum()

    first = select_item(curriculum, state)
    second = select_item(curriculum, state)
    assert first.id != second.id, "the same item is not served twice in a row"

    twin = new_state()
    assert select_item(curriculum, twin).id == first.id, "selection is reproducible"


def test_selection_prefers_struggling_items():
    curriculum = __import__("app.curriculum", fromlist=["get_curriculum"]).get_curriculum()
    state = new_state()
    items = curriculum.items(1)

    # Master everything except one item, which the child keeps missing.
    for item in items:
        state["mastery"][item.id] = {"box": 3, "attempts": 5, "correct": 5, "last_seen": None}
    struggling = items[7]
    state["mastery"][struggling.id] = {"box": 0, "attempts": 4, "correct": 0, "last_seen": None}

    chosen = select_item(curriculum, state)
    assert chosen.id == struggling.id


def test_hint_selection_keeps_the_struggling_item_in_play():
    curriculum = __import__("app.curriculum", fromlist=["get_curriculum"]).get_curriculum()
    state = new_state()
    items = curriculum.items(1)
    for item in items:
        state["mastery"][item.id] = {"box": 2, "attempts": 2, "correct": 2, "last_seen": None}
    weakest = items[3]
    state["mastery"][weakest.id] = {"box": 0, "attempts": 2, "correct": 0, "last_seen": None}
    state["current_item"] = weakest.id

    assert select_item(curriculum, state, hint=True).id == weakest.id


def test_selection_falls_back_when_a_level_has_no_items():
    class EmptyCurriculum:
        def items(self, level):
            return ()

    assert select_item(EmptyCurriculum(), new_state()) is None
