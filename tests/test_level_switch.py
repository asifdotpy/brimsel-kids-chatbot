"""The reviewer's level control.

This is a demo affordance, not the promotion rule, and the two must not blur:
promotion stays deterministic and earned, while this simply moves a session to a
level so a reviewer can see L2/L3 content without grinding there first.

The interesting cases are the boundary ones - what a jump clears, what it must
keep, and whether ordinary promotion still works afterwards.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.curriculum import get_curriculum
from app.engine import change_level
from app.main import app
from app.state import new_state, normalize_state, set_level


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------- set_level

def test_a_jump_clears_the_level_counters_but_keeps_mastery_and_history():
    """Mastery is about the child, not the level.

    Clearing it would throw away the record of which words this particular child
    finds hard - which is just as true at level 2 as at level 1. Only the
    level-local promotion counters are reset, so a freshly chosen level does not
    arrive already part-way to promoting itself.
    """
    state = new_state()
    state["attempts_at_level"] = 7
    state["recent_results"] = [True, False, True]
    state["miss_streak"] = 2
    state["learn_streak"] = 2
    state["ease_count"] = 1
    state["pending_item"] = "l1.word.cat"
    state["current_item"] = "l1.word.cat"
    state["mastery"] = {"l1.word.cat": {"box": 2, "attempts": 3, "correct": 2, "last_seen": None}}
    turns_before = len(state["turns"])

    set_level(state, 2)

    assert state["level"] == 2
    assert state["attempts_at_level"] == 0
    assert state["recent_results"] == []
    assert state["miss_streak"] == 0
    assert state["learn_streak"] == 0
    assert state["ease_count"] == 0
    assert state["pending_item"] is None
    assert state["current_item"] is None
    assert state["mastery"]["l1.word.cat"]["box"] == 2, "mastery must survive"
    assert len(state["turns"]) == turns_before, "the conversation must survive"


@pytest.mark.parametrize(
    "asked,expected",
    [(0, 1), (-5, 1), (1, 1), (2, 2), (3, 3), (4, 3), (999, 3)],
)
def test_the_level_is_clamped_to_a_real_level(asked, expected):
    state = new_state()
    set_level(state, asked)
    assert state["level"] == expected


def test_a_jump_resets_the_learn_streak_so_practice_is_not_blocked():
    """The anti-drill rule caps three learning turns in a row.

    Without clearing the streak, jumping to a level mid-drill would leave the
    child unable to practise on arrival.
    """
    state = new_state()
    state["learn_streak"] = 3
    set_level(state, 3)
    assert state["learn_streak"] == 0


# ------------------------------------------------------------- change_level

@pytest.mark.parametrize("level", [1, 2, 3])
def test_a_jump_asks_an_item_from_that_level(level):
    curriculum = get_curriculum()
    result = change_level(new_state(), level)

    item_id = result.state["pending_item"]
    assert item_id, f"level {level} should arrive with an item already chosen"

    item = curriculum.get(item_id)
    assert item is not None
    assert item.level == level, f"asked a level-{item.level} item after jumping to {level}"
    assert result.reply.strip()
    assert result.state["mode"] == "Learning"
    assert result.state["level"] == level


def test_a_jump_does_not_put_words_in_the_childs_mouth():
    """plan_turn writes the child's message into history.

    On this path the child typed nothing - a reviewer pressed a button - so the
    synthetic turn is dropped rather than stored as something the child said.
    """
    result = change_level(new_state(), 2)
    texts = [turn["text"] for turn in result.state["turns"]]
    roles = [turn["role"] for turn in result.state["turns"]]
    assert "let's learn" not in texts
    assert roles[-1] == "bot", "the jump should end with Buddy speaking"


def test_a_jump_is_offline_and_says_so():
    """A level jump must never depend on an upstream provider."""
    result = change_level(new_state(), 3)
    assert result.debug["mode_reason"] == "level_switch"
    assert result.debug["use_llm"] is False
    assert result.debug["fallback_used"] is True


def test_a_tampered_session_is_normalised_before_the_jump():
    """The blob round-trips through the browser, so it is untrusted input."""
    garbage = {"level": "banana", "attempts_at_level": 10**9, "recent_results": "no", "turns": "x"}
    result = change_level(garbage, 2)
    assert result.state["level"] == 2
    assert result.state["attempts_at_level"] == 0
    assert isinstance(result.state["recent_results"], list)
    assert isinstance(result.state["turns"], list)


def test_promotion_still_works_normally_after_a_jump(drive):
    """The manual control must not disable the real rule."""
    state = change_level(new_state(), 2).state
    item = get_curriculum().get(state["pending_item"])
    assert item is not None

    result = drive(state, item.text)
    assert result.state["attempts_at_level"] == 1
    assert result.state["recent_results"][-1] is True, (
        "answering the asked item after a jump must still score and count"
    )


def test_a_jump_keeps_a_normalised_session_valid_for_the_next_turn(drive):
    """Whatever a jump produces has to be safe to feed straight back in."""
    state = change_level(new_state(), 3).state
    again = normalize_state(state)
    assert again["level"] == 3
    assert again["pending_item"] == state["pending_item"]
    reply = drive(again, "hello buddy")
    assert reply.reply


# -------------------------------------------------------------------- /api/level

def test_the_endpoint_returns_a_playable_session(client):
    session = client.post("/api/session", json={}).json()["session"]
    response = client.post("/api/level", json={"session": session, "level": 3})
    assert response.status_code == 200
    body = response.json()

    assert body["session"]["level"] == 3
    assert body["reply"]
    assert body["debug"]["mode_reason"] == "level_switch"
    # The session it hands back must be usable by the very next endpoint.
    follow_up = client.post("/api/chat", json={"session": body["session"], "message": "hello buddy"})
    assert follow_up.status_code == 200


def test_the_endpoint_works_without_a_session(client):
    response = client.post("/api/level", json={"level": 2})
    assert response.status_code == 200
    assert response.json()["session"]["level"] == 2


def test_the_endpoint_rejects_an_out_of_range_level(client):
    """Pydantic rejects it before it can reach the engine."""
    assert client.post("/api/level", json={"level": 0}).status_code == 422
    assert client.post("/api/level", json={"level": 9}).status_code == 422
