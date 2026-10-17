"""HTTP surface: the endpoints the browser actually calls."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def new_session(client, nickname=None):
    response = client.post("/api/session", json={"nickname": nickname})
    assert response.status_code == 200
    return response.json()


def chat(client, session, message, mood=None, reply_ms=None):
    response = client.post(
        "/api/chat",
        json={"session": session, "message": message, "mood": mood, "reply_ms": reply_ms},
    )
    assert response.status_code == 200
    return response.json()


# --------------------------------------------------------------------- basics

def test_root_serves_the_child_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Buddy" in response.text
    assert "How do you feel?" in response.text


def test_static_assets_are_served(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_health_reports_configuration_without_secrets(client):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["version"]
    assert payload["llm"]["provider"] in {"none", "gemini", "openrouter"}
    assert "api_key" not in str(payload).lower()
    assert set(payload["levels"]) == {"1", "2", "3"}


def test_curriculum_endpoint_lists_three_levels(client):
    payload = client.get("/api/curriculum").json()
    assert payload["version"]
    assert [level["level"] for level in payload["levels"]] == [1, 2, 3]
    assert all(level["items"] > 0 for level in payload["levels"])


# ------------------------------------------------------------------- chat flow

def test_a_full_turn_over_http(client):
    session = new_session(client, "Ana")["session"]
    assert session["level"] == 1

    payload = chat(client, session, "hello buddy")
    assert payload["reply"].strip()
    assert payload["session"]["turn_count"] == 1
    assert payload["server_ms"] >= 0
    assert payload["debug"]["mode"] in {"Support", "Engagement", "Conversation", "Learning"}


def test_a_refresh_keeps_the_same_lesson(client):
    data = new_session(client)
    session = data["session"]
    session = chat(client, session, "let's learn")["session"]
    first_item = session["pending_item"]
    assert first_item

    # The browser reloads and posts the stored blob back unchanged.
    reloaded = chat(client, session, session["pending_item"])["session"]
    assert reloaded["session_id"] == session["session_id"]
    assert reloaded["level"] == session["level"]
    assert reloaded["turn_count"] > 1
    assert reloaded["learn_streak"] >= 1


def test_an_empty_message_nudges_without_touching_the_session(client):
    session = new_session(client)["session"]
    session = chat(client, session, "hello buddy")["session"]
    turns_before = session["turn_count"]

    payload = chat(client, session, "   ")

    assert payload["debug"]["mode_reason"] == "empty_input"
    assert payload["session"]["turn_count"] == turns_before


def test_a_session_can_be_started_without_a_body(client):
    response = client.post("/api/session")
    assert response.status_code == 200
    assert response.json()["session"]["level"] == 1


def test_chat_without_a_session_starts_fresh(client):
    payload = chat(client, None, "hello buddy")
    assert payload["session"]["session_id"]
    assert payload["reply"].strip()


# --------------------------------------------------------------------- metrics

def test_metrics_record_real_turns(client):
    session = new_session(client)["session"]
    for message in ("hello buddy", "let's learn"):
        session = chat(client, session, message)["session"]

    payload = client.get("/api/metrics").json()
    assert payload["turns"] >= 2
    assert payload["total_ms"]["p50"] >= 0
    assert payload["total_ms"]["p95"] >= payload["total_ms"]["p50"]
    assert payload["by_mode"]


def test_the_latency_header_is_present(client):
    session = new_session(client)["session"]
    response = client.post("/api/chat", json={"session": session, "message": "hello buddy"})
    assert "X-Response-Time-Ms" in response.headers
    assert int(response.headers["X-Response-Time-Ms"]) >= 0


# ---------------------------------------------------------------- robustness

def test_a_corrupt_session_blob_is_recovered(client):
    payload = chat(client, {"level": "banana", "turns": "not a list"}, "hello buddy")
    assert payload["session"]["level"] == 1
    assert payload["reply"].strip()


def test_an_over_long_message_is_rejected_by_validation(client):
    response = client.post("/api/chat", json={"message": "a" * 5000})
    assert response.status_code == 422


def test_unknown_api_routes_do_not_leak_the_spa(client):
    assert client.get("/api/does-not-exist").status_code == 404
