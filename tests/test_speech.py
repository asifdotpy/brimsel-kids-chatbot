"""Speech: the prosody mapping, the text preparation, and the wire format.

The wire format matters more than it looks. This synthesiser accepts exactly one
document shape and answers ``NoAudioReceived`` for anything else, so a wrong
document is silent rather than loud - the worst kind of failure. The test below
pins our builder against edge-tts's own, which is the code that actually goes on
the wire.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.speech import (
    CACHE_SIZE,
    DEFAULT_VOICE,
    MAX_SPEECH_CHARS,
    SpeechAudio,
    SpeechService,
    SpeechStyle,
    SpeechUnavailable,
    SYNTH_TIMEOUT_S,
    build_ssml,
    clean_for_speech,
    speech_from_env,
    style_for,
)


# --------------------------------------------------------------------- prosody

def test_comfort_is_the_slowest_voice_and_praise_is_brighter():
    support = style_for("Support", correct=False)
    praise = style_for("Learning", correct=True)
    miss = style_for("Learning", correct=False)
    neutral = style_for("Conversation")

    assert support.rate.endswith("%") and praise.rate.endswith("%")
    assert int(support.rate.rstrip("%")) < int(miss.rate.rstrip("%")), (
        "a comforting turn must be calmer than a correction"
    )
    assert int(praise.rate.rstrip("%")) > int(miss.rate.rstrip("%"))
    assert int(neutral.rate.rstrip("%")) == 0


def test_model_intent_steers_the_voice():
    calm = style_for("Learning", energy="calm")
    bright = style_for("Learning", energy="bright")
    assert int(bright.rate.rstrip("%")) > int(calm.rate.rstrip("%"))
    assert int(bright.pitch.rstrip("Hz")) > int(calm.pitch.rstrip("Hz"))


def test_prosody_is_clamped_so_a_child_never_hears_an_extreme():
    """A tampered client could ask for a silly speed; the server decides."""
    fast = style_for("Engagement", correct=True, energy="excited", speed=1.3)
    slow = style_for("Support", correct=False, energy="calm", speed=0.6)
    for style in (fast, slow):
        rate = int(style.rate.rstrip("%"))
        assert -40 <= rate <= 40
        assert -8 <= int(style.pitch.rstrip("Hz")) <= 8


def test_speed_slider_scales_the_rate_in_the_right_direction():
    slow = style_for("Learning", speed=0.7)
    normal = style_for("Learning", speed=1.0)
    fast = style_for("Learning", speed=1.3)
    assert int(slow.rate.rstrip("%")) < int(normal.rate.rstrip("%")) < int(fast.rate.rstrip("%"))


def test_a_nonsense_speed_falls_back_instead_of_crashing():
    style = style_for("Learning", speed="fast please")  # type: ignore[arg-type]
    assert style.rate.endswith("%")


def test_an_unknown_mode_is_treated_as_conversation():
    assert style_for("Teleportation").as_dict()["rate"] == style_for("Conversation").as_dict()["rate"]


# ----------------------------------------------------------- text preparation

def test_emoji_are_removed_rather_than_spoken():
    """A voice reading "grinning face" mid-sentence breaks the illusion."""
    cleaned = clean_for_speech("Great job! 🌟🎉 Can you say cat? 🐱")
    assert "🌟" not in cleaned and "🐱" not in cleaned
    assert cleaned == "Great job! Can you say cat?"


def test_control_characters_and_runs_of_space_are_normalised():
    assert clean_for_speech("hi\x00\nthere   friend") == "hi there friend"


def test_speech_length_is_capped():
    assert len(clean_for_speech("a" * 5000)) == MAX_SPEECH_CHARS


def test_empty_input_is_rejected():
    assert clean_for_speech("") == ""
    assert clean_for_speech("   ") == ""
    assert clean_for_speech(None) == ""  # type: ignore[arg-type]


# -------------------------------------------------------------- the wire format

def test_the_ssml_matches_what_edge_tts_actually_sends():
    """The synthesiser accepts one shape only; anything else is silent.

    edge-tts builds the real request, so if these two ever diverge our
    understanding of the wire format has drifted and the audio would stop.
    """
    edge_tts = pytest.importorskip("edge_tts.communicate")
    from edge_tts.data_classes import TTSConfig

    voice = "en-US-AvaMultilingualNeural"
    style = SpeechStyle(rate="-10%", pitch="+2Hz", volume="+0%", label="normal")
    text = "Can you say cat?"

    theirs = edge_tts.mkssml(
        TTSConfig(
            voice=voice,
            rate=style.rate,
            volume=style.volume,
            pitch=style.pitch,
            boundary="SentenceBoundary",
        ),
        text,
    )
    ours = build_ssml(text, style, voice)
    assert ours == theirs


def test_markup_in_the_text_is_escaped():
    ssml = build_ssml("a <voice name='evil'> & b", style_for("Learning"))
    assert "<voice name='evil'>" not in ssml
    assert "&lt;voice" in ssml and "&amp;" in ssml


# --------------------------------------------------------------------- service

def test_a_disabled_service_refuses_rather_than_going_silent():
    service = SpeechService(enabled=False)
    with pytest.raises(SpeechUnavailable):
        asyncio.run(service.audio("Hello"))
    assert service.describe()["enabled"] is False


def test_empty_text_is_refused():
    service = SpeechService(enabled=True)
    with pytest.raises(SpeechUnavailable):
        asyncio.run(service.audio("   "))


def test_a_repeat_phrase_is_served_from_cache(monkeypatch):
    """Buddy repeats a small vocabulary, so the cache is what keeps speech cheap."""
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def fake(text, style):
        calls["n"] += 1
        return b"\xff\xf3fake-mp3" * 8

    monkeypatch.setattr(service, "_synthesise", fake)

    first = asyncio.run(service.audio("Good job!", mode="Learning", correct=True))
    second = asyncio.run(service.audio("Good job!", mode="Learning", correct=True))

    assert calls["n"] == 1, "the second identical request must not re-synthesise"
    assert first.cached is False and second.cached is True
    assert first.data == second.data


def test_a_different_style_is_a_different_cache_entry(monkeypatch):
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def fake(text, style):
        calls["n"] += 1
        return b"\xff\xf3" + style.rate.encode()

    monkeypatch.setattr(service, "_synthesise", fake)

    asyncio.run(service.audio("Good job!", mode="Learning", correct=True))
    asyncio.run(service.audio("Good job!", mode="Support", correct=False))
    assert calls["n"] == 2, "praise and comfort must not share audio"


def test_the_cache_is_bounded(monkeypatch):
    service = SpeechService(enabled=True, cache_size=3)

    async def fake(text, style):
        return b"\xff\xf3" + text.encode()

    monkeypatch.setattr(service, "_synthesise", fake)
    for n in range(6):
        asyncio.run(service.audio(f"Phrase number {n}"))
    assert service.cache_info()["entries"] == 3


# ------------------------------------------------------------------ one request

def test_a_long_reply_is_still_a_single_request(monkeypatch):
    """Measured, not assumed: segmentation serialises.

    Splitting a reply into one utterance per sentence made wall-clock grow with
    the sentence count (3 sentences took 1.86x the time of 2). One request is
    therefore both simpler and faster, and the neural voice already pauses at
    punctuation.
    """
    service = SpeechService(enabled=True)
    calls: list[str] = []

    async def fake(text, style):
        calls.append(text)
        return b"audio"

    monkeypatch.setattr(service, "_synthesise", fake)
    audio = asyncio.run(service.audio("Great job! You are working hard. Can you say cat?"))
    assert calls == ["Great job! You are working hard. Can you say cat?"]
    assert audio.chars == len("Great job! You are working hard. Can you say cat?")


def test_chars_are_reported_for_the_cleaned_text(monkeypatch):
    service = SpeechService(enabled=True)

    async def fake(text, style):
        return b"audio"

    monkeypatch.setattr(service, "_synthesise", fake)
    audio = asyncio.run(service.audio("Hi 🌟 there"))
    assert audio.chars == len("Hi there")


def test_a_slow_synthesiser_times_out_instead_of_hanging_the_turn(monkeypatch):
    service = SpeechService(enabled=True)

    async def never(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(service, "_synthesise", never)
    with pytest.raises(SpeechUnavailable):
        asyncio.run(service.audio("Hello", timeout_s=0.05))


# ------------------------------------------------------------------ configuration

def test_speech_can_be_switched_off_by_configuration():
    assert speech_from_env({"SPEECH_PROVIDER": "none"}).enabled is False
    assert speech_from_env({"SPEECH_PROVIDER": "off"}).enabled is False


def test_the_voice_can_be_overridden():
    service = speech_from_env({"SPEECH_VOICE": "en-GB-SoniaNeural"})
    assert service.voice == "en-GB-SoniaNeural"
    assert service.describe()["provider"] == "edge"


def test_an_empty_voice_setting_falls_back_to_the_default():
    assert speech_from_env({"SPEECH_VOICE": "   "}).voice == DEFAULT_VOICE


def test_the_audio_timeout_is_configurable_and_survives_nonsense():
    assert speech_from_env({"SPEECH_TIMEOUT_MS": "2500"}).timeout_s == 2.5
    assert speech_from_env({"SPEECH_TIMEOUT_MS": "nonsense"}).timeout_s == SYNTH_TIMEOUT_S
    assert speech_from_env({}).timeout_s == SYNTH_TIMEOUT_S


# --------------------------------------------------------------------- /api/speech

@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_the_endpoint_reports_503_so_the_browser_voice_can_take_over(client):
    """conftest replaces the service with a disabled one.

    Speech is an enhancement: when it is unavailable the contract is a 503 and a
    named fallback, never a broken turn.
    """
    response = client.post("/api/speech", json={"text": "Hello"})
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "speech_unavailable"
    assert body["fallback"] == "browser"


def test_the_endpoint_streams_audio_directly(client, monkeypatch):
    """`audio/mpeg`, not base64 inside JSON, so playback can start on first frames."""
    import app.main as main_module

    class FakeSpeech:
        enabled = True
        voice = "en-US-TestNeural"

        async def audio(self, **kwargs):
            return SpeechAudio(
                data=b"\xff\xf3fakeaudio",
                voice="en-US-TestNeural",
                chars=len("Great job! Can you say cat?"),
                style="normal",
                cached=False,
                ms=7,
            )

    monkeypatch.setattr(main_module, "_speech", FakeSpeech())
    response = client.post("/api/speech", json={"text": "Great job! Can you say cat?"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"\xff\xf3fakeaudio"
    assert response.headers["X-Speech-Chars"] == str(len("Great job! Can you say cat?"))
    assert response.headers["X-Speech-Cached"] == "0"


def test_the_endpoint_ignores_any_markup_the_client_sends(client, monkeypatch):
    """The server rebuilds the document, so the client cannot inject SSML."""
    import app.main as main_module

    seen: dict = {}

    class SpySpeech:
        enabled = True
        voice = "v"

        async def audio(self, **kwargs):
            seen.update(kwargs)
            return SpeechAudio(data=b"x", voice="v", chars=1, style="normal", cached=False, ms=0)

    monkeypatch.setattr(main_module, "_speech", SpySpeech())
    response = client.post(
        "/api/speech",
        json={
            "text": "hello",
            "ssml": "<speak><voice name='evil'>boo</voice></speak>",
            "voice": "evil-voice",
        },
    )
    assert response.status_code == 200
    assert "ssml" not in seen, "there is no SSML parameter to inject into"
    assert seen["text"] == "hello"
    assert seen["mode"] is None


def test_a_model_hint_is_clamped_and_never_trusted(client, monkeypatch):
    """The hint comes back through the browser, so it is untrusted input."""
    import app.main as main_module

    seen: dict = {}

    class SpySpeech:
        enabled = True
        voice = "v"

        async def audio(self, **kwargs):
            seen.update(kwargs)
            return SpeechAudio(data=b"x", voice="v", chars=1, style="normal", cached=False, ms=0)

    monkeypatch.setattr(main_module, "_speech", SpySpeech())

    client.post("/api/speech", json={"text": "hi", "hint": {"energy": "x" * 500}})
    assert len(seen["energy"]) == 24

    client.post("/api/speech", json={"text": "hi", "hint": {"energy": 12345}})
    assert seen["energy"] is None, "a non-string energy must be discarded"

    client.post("/api/speech", json={"text": "hi", "hint": "not a dict"})
    assert seen["energy"] is None


def test_a_transient_synthesis_failure_is_retried_once(monkeypatch):
    """A pre-warm must not hand its one-off failure to the child's request.

    This endpoint answers `NoAudioReceived` intermittently, so a single retry
    inside the same time budget turns a flaky miss into a spoken reply.
    """
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def flaky(text, style):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SpeechUnavailable("NoAudioReceived")
        return b"\xff\xf3recovered"

    monkeypatch.setattr(service, "_synthesise", flaky)
    heard = asyncio.run(service.audio("Can you say cat?"))
    assert calls["n"] == 2
    assert heard.data == b"\xff\xf3recovered"


def test_a_persistent_synthesis_failure_still_surfaces(monkeypatch):
    """The retry must not turn a real outage into an indefinite wait."""
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def broken(text, style):
        calls["n"] += 1
        raise SpeechUnavailable("the voice returned no audio")

    monkeypatch.setattr(service, "_synthesise", broken)
    with pytest.raises(SpeechUnavailable):
        asyncio.run(service.audio("Can you say cat?"))
    assert calls["n"] == 2, "exactly one retry, then it gives up"


def test_health_advertises_the_voice(client):
    body = client.get("/api/health").json()
    assert "speech" in body
    assert set(body["speech"]) >= {"enabled", "provider", "voice", "cache"}


# -------------------------------------------------------------------- pre-warm

def test_prewarming_fills_the_cache_before_anyone_asks(monkeypatch):
    """A route knows the sentence it just sent, so the ~3s can run while the UI paints."""
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def fake(text, style):
        calls["n"] += 1
        return b"\xff\xf3warm" * 4

    monkeypatch.setattr(service, "_synthesise", fake)

    warmed = asyncio.run(service.prewarm("Great job! Can you say cat?", mode="Learning", correct=True))
    assert warmed is True

    heard = asyncio.run(service.audio("Great job! Can you say cat?", mode="Learning", correct=True))
    assert calls["n"] == 1, "the later request must reuse the pre-warmed audio"
    assert heard.cached is True and heard.ms == 0


def test_prewarming_a_cached_phrase_costs_nothing(monkeypatch):
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def fake(text, style):
        calls["n"] += 1
        return b"x"

    monkeypatch.setattr(service, "_synthesise", fake)
    asyncio.run(service.audio("Hello there!"))
    assert asyncio.run(service.prewarm("Hello there!")) is True
    assert calls["n"] == 1, "a phrase already in the cache must not be synthesised again"


def test_concurrent_requests_for_one_phrase_share_a_single_synthesis(monkeypatch):
    """Pre-warming and the browser's own request overlap by design.

    Two syntheses for one phrase would double the load and could deliver audio
    out of order; the second caller has to join the first.
    """
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def slow(text, style):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return b"\xff\xf3shared" * 4

    monkeypatch.setattr(service, "_synthesise", slow)

    async def main():
        return await asyncio.gather(
            service.prewarm("Can you say cat?", mode="Learning", correct=True),
            service.audio("Can you say cat?", mode="Learning", correct=True),
        )

    warm, ask = asyncio.run(main())
    assert warm is True
    assert calls["n"] == 1, "one phrase must cost exactly one synthesis"
    # The joining caller reports cached=False (it did find a miss) but received the
    # shared audio, and the result still landed in the cache for the next turn.
    assert ask.data == b"\xff\xf3shared" * 4
    assert not service.cache_info()["inflight"], "an in-flight entry must be released"

    again = asyncio.run(service.audio("Can you say cat?", mode="Learning", correct=True))
    assert again.cached is True and calls["n"] == 1


def test_prewarming_is_a_no_op_when_speech_is_off(monkeypatch):
    service = SpeechService(enabled=False)

    async def boom(text, style):  # pragma: no cover - must never run
        raise AssertionError("disabled speech must not synthesise")

    monkeypatch.setattr(service, "_synthesise", boom)
    assert asyncio.run(service.prewarm("anything")) is False


def test_a_failed_prewarm_is_silent(monkeypatch):
    """Speech is an enhancement, so a warm-up failure must never surface."""
    service = SpeechService(enabled=True)

    async def broken(text, style):
        raise SpeechUnavailable("upstream refused")

    monkeypatch.setattr(service, "_synthesise", broken)
    assert asyncio.run(service.prewarm("anything at all")) is False


def test_the_server_warms_the_voice_for_a_level_jump(client, monkeypatch):
    """The reviewer case: the text lands instantly, so the audio should too.

    The pre-warm must use exactly the fields the browser will send, or the two
    requests produce different cache keys and nothing is shared.
    """
    import app.main as main_module

    warmed: list[tuple[str, dict]] = []

    class SpySpeech:
        enabled = True
        voice = "v"

        async def prewarm(self, text, **kwargs):
            warmed.append((text, kwargs))
            return True

        async def audio(self, **kwargs):  # pragma: no cover - no audio is requested
            raise AssertionError("this test requests no audio")

    monkeypatch.setattr(main_module, "_speech", SpySpeech())
    body = client.post("/api/level", json={"session": None, "level": 2}).json()

    deadline = time.time() + 2.0
    while not warmed and time.time() < deadline:
        time.sleep(0.01)

    assert warmed, "a level jump must warm the voice for the reply it just produced"
    text, kwargs = warmed[0]
    assert text == body["reply"]
    assert kwargs["mode"] == body["debug"]["mode"]
    assert kwargs["correct"] == body["debug"]["correct"]


def test_the_server_warms_the_voice_for_a_new_session(client, monkeypatch):
    import app.main as main_module

    warmed: list[str] = []

    class SpySpeech:
        enabled = True
        voice = "v"

        async def prewarm(self, text, **kwargs):
            warmed.append(text)
            return True

        async def audio(self, **kwargs):  # pragma: no cover
            raise AssertionError("this test requests no audio")

    monkeypatch.setattr(main_module, "_speech", SpySpeech())
    body = client.post("/api/session", json={}).json()

    deadline = time.time() + 2.0
    while not warmed and time.time() < deadline:
        time.sleep(0.01)

    assert warmed == [body["reply"]]


def test_prewarming_never_delays_the_reply(client, monkeypatch):
    """The warm-up is detached: a slow synthesis must not hold up the text."""
    import app.main as main_module

    class SlowPrewarmSpeech:
        enabled = True
        voice = "v"

        async def prewarm(self, text, **kwargs):
            await asyncio.sleep(5.0)  # would blow any request budget
            return True

        async def audio(self, **kwargs):  # pragma: no cover
            raise AssertionError("no audio is requested")

    monkeypatch.setattr(main_module, "_speech", SlowPrewarmSpeech())
    started = time.perf_counter()
    response = client.post("/api/level", json={"session": None, "level": 1})
    elapsed = time.perf_counter() - started

    assert response.status_code == 200
    assert elapsed < 1.0, "the reply must not wait for the voice"


def test_a_failed_warm_up_never_fails_the_childs_request(monkeypatch):
    """A speculative warm-up is allowed to fail; the real request is not."""
    service = SpeechService(enabled=True)
    calls = {"n": 0}

    async def flaky(text, style):
        calls["n"] += 1
        if calls["n"] <= 2:  # both attempts of the warm-up fail
            await asyncio.sleep(0.05)  # still running when the child asks
            raise SpeechUnavailable("NoAudioReceived")
        return b"\xff\xf3served-anyway"

    monkeypatch.setattr(service, "_synthesise", flaky)

    async def main():
        warm = asyncio.create_task(service.prewarm("Can you say cat?"))
        await asyncio.sleep(0.01)  # let the warm-up claim the in-flight slot
        try:
            return await service.audio("Can you say cat?")
        finally:
            await warm

    heard = asyncio.run(main())
    assert heard.data == b"\xff\xf3served-anyway"
    assert calls["n"] == 3, "two failed warm-up attempts, then one real synthesis"


def test_the_warm_up_uses_the_speed_the_client_will_ask_with(client, monkeypatch):
    """Speed shapes the prosody, so it is part of the cache key.

    A moved speed slider would otherwise make every warm-up useless and silently
    restore the delay.
    """
    import app.main as main_module

    seen: list[dict] = []

    class SpySpeech:
        enabled = True
        voice = "v"

        async def prewarm(self, text, **kwargs):
            seen.append(kwargs)
            return True

        async def audio(self, **kwargs):  # pragma: no cover - no audio requested
            raise AssertionError("this test requests no audio")

    monkeypatch.setattr(main_module, "_speech", SpySpeech())
    client.post("/api/level", json={"session": None, "level": 1, "speed": 0.8})

    deadline = time.time() + 2.0
    while not seen and time.time() < deadline:
        time.sleep(0.01)

    assert seen, "the reply must still be warmed"
    assert seen[0]["speed"] == 0.8


def test_the_warm_endpoint_lists_the_three_level_openers(client):
    """Read-only: it must neither return nor touch a session."""
    body = client.get("/api/warm").json()
    phrases = body["phrases"]

    assert len(phrases) == 3, "one opener per level"
    assert all(p["text"] for p in phrases)
    assert all(p["mode"] == "Learning" for p in phrases)
    assert all(p["correct"] is None for p in phrases)
    assert len({p["text"] for p in phrases}) == 3, "each level opens differently"
    assert "session" not in body, "a warm-up must not hand back session state"
