"""The static UI is part of the deliverable, so a few invariants are pinned here.

These are deliberately about *contracts* rather than pixels: the things that
would be silently broken by a plausible-looking edit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.speech import SYNTH_TIMEOUT_S

STATIC = Path(__file__).resolve().parent.parent / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")


# ------------------------------------------------------------------ accessibility

def test_the_page_declares_a_responsive_viewport():
    assert 'name="viewport"' in HTML
    assert "width=device-width" in HTML


def test_the_input_has_a_label_for_screen_readers():
    assert 'class="sr-only"' in HTML
    assert 'for="text"' in HTML
    assert 'id="text"' in HTML


def test_tap_targets_meet_the_child_sized_floor():
    """Buttons are sized for small hands, not for a mouse.

    The floor is checked rather than pinned to one number: the layout has to fit
    a 1366x768 laptop at 100% zoom, so sizes are fluid, but nothing may drop
    below the 44px WCAG target (48px is used here for margin).
    """
    match = re.search(r"--tap:\s*(\d+)px", CSS)
    assert match, "styles.css must define a --tap size"
    assert int(match.group(1)) >= 44, "--tap must stay above the 44px accessibility floor"
    assert "min-height: var(--tap)" in CSS


def test_the_layout_fits_a_small_laptop_without_zooming_out():
    """A fixed 22px body font made the page need 67% browser zoom on a laptop.

    Sizes are now fluid, and the page itself never scrolls, so the composer
    cannot be pushed off the bottom of the screen.
    """
    assert re.search(r"font-size:\s*clamp\(", CSS), "body text must scale with the viewport"
    assert re.search(r"body\s*\{[^}]*overflow:\s*hidden", CSS, re.S), (
        "the page must not scroll; only the message list should"
    )
    assert re.search(r"\.chat\s*\{[^}]*overflow-y:\s*auto", CSS, re.S), "the message list must be the scroll area"
    assert re.search(r"\.chat\s*\{[^}]*min-height:\s*0", CSS, re.S), (
        "the message list needs min-height:0 inside a flex column, or it grows "
        "instead of scrolling and pushes the composer off screen"
    )


def test_motion_is_reduced_when_the_browser_asks():
    assert "prefers-reduced-motion: reduce" in CSS


# ------------------------------------------------------------------ the hidden trap

def test_hidden_is_actually_hidden():
    """A `display` rule beats the user-agent's ``[hidden]`` rule.

    Without an explicit guard, ``.typing { display: flex }`` makes the
    "Buddy is thinking..." row visible on every single page load.
    """
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", CSS), (
        "styles.css must force [hidden] to display:none, or any flex/grid "
        "component that carries the hidden attribute will stay visible"
    )


@pytest.mark.parametrize("element_id", ["typing", "settingsPanel"])
def test_optional_panels_start_hidden(element_id):
    match = re.search(rf'id="{element_id}"[^>]*', HTML)
    assert match, f"#{element_id} is missing from index.html"
    assert "hidden" in match.group(0), f"#{element_id} must start hidden"


def test_the_settings_panel_reads_like_an_app_not_a_debug_dump():
    """It used to be a dark <pre> dumping raw JSON at the user.

    A reviewer should see labelled sections and formatted values; the raw engine
    payload is still available, but behind an explicit disclosure.
    """
    assert 'aria-label="Settings"' in HTML
    assert "Practice level" in HTML and "Diagnostics" in HTML
    assert 'id="kvConn"' in HTML and 'id="kvModel"' in HTML
    assert "<summary>" in HTML, "the raw payload must be behind a disclosure"
    assert re.search(r"\.settings\s*\{[^}]*background:\s*#fff", CSS, re.S), (
        "the panel should look like a normal light panel, not a terminal"
    )


def test_the_typing_row_sets_display_so_the_guard_is_load_bearing():
    assert re.search(r"\.typing\s*\{[^}]*display:", CSS, re.S)


# ------------------------------------------------------------------ the engine panel

def test_the_settings_panel_only_reserves_a_column_when_open():
    """With an always-on second column the chat card is auto-placed into it and
    the narrow mood card takes the wide column, which reads backwards."""
    assert ".layout.is-open" in CSS, "the wide layout must be gated on the panel being open"
    assert re.search(r'grid-template-columns:\s*minmax\(0,\s*1fr\)\s*380px', CSS)
    assert 'classList.toggle("is-open"' in JS, "app.js must toggle the layout class"


def test_narrow_screens_get_an_overlay_instead_of_a_squeezed_column():
    """Below the two-column breakpoint the panel must float, not shrink the chat."""
    assert re.search(r"@media\s*\(max-width:\s*1039px\)\s*\{[^@]*position:\s*fixed", CSS, re.S)


# ------------------------------------------------------------------ level control

def test_the_level_switcher_is_present_and_labelled_as_a_review_tool():
    """Reviewers need to reach L2/L3 without earning promotion first.

    The control has to be obviously a demo affordance, so it cannot be mistaken
    for the promotion rule the brief asks for.
    """
    assert 'id="levelSeg"' in HTML
    assert 'data-level="1"' in HTML and 'data-level="2"' in HTML and 'data-level="3"' in HTML
    assert "demo control" in HTML.lower()
    assert '"/api/level"' in JS, "the switcher must call the server, not edit state locally"


# ------------------------------------------------------------------ no secrets

def test_no_key_or_provider_url_reaches_the_browser():
    """The brief requires that no API key is ever exposed client-side."""
    for name, text in (("index.html", HTML), ("app.js", JS), ("styles.css", CSS)):
        lowered = text.lower()
        for forbidden in ("api_key", "apikey", "generativelanguage", "openrouter.ai", "bearer "):
            assert forbidden not in lowered, f"{name} must not mention {forbidden!r}"
        assert not re.search(r"AIza[0-9A-Za-z_\-]{10,}", text), f"{name} looks like it embeds a Google key"
        assert not re.search(r"sk-or-v1", text), f"{name} looks like it embeds an OpenRouter key"


def test_a_superseded_speech_request_never_plays_over_a_newer_reply():
    """Audio is fetched asynchronously and can take seconds.

    A slow request resolving after a newer turn would otherwise speak the
    previous reply over the current one.
    """
    assert "speechGeneration" in JS
    assert re.search(r"generation\s*!==\s*speechGeneration", JS), (
        "a superseded speech response must be dropped, not played"
    )
    assert re.search(r"function stopSpeaking\(\)\s*\{[^}]*speechGeneration\s*\+=", JS, re.S), (
        "cancelling should also invalidate any in-flight speech"
    )


def test_the_client_waits_for_the_server_verdict_before_falling_back():
    """Cold synthesis measured 2.3-3.3s server-side, with a tail past its 8s budget.

    The client bound must sit *above* the server's own synthesis budget so that a
    slow phrase is decided by the server, not aborted client-side - an early abort
    throws away a synthesis that was about to succeed.
    """
    match = re.search(r"SPEECH_TIMEOUT_MS\s*=\s*(\d+)", JS)
    assert match, "app.js must bound how long it waits for server audio"
    timeout_ms = int(match.group(1))
    assert timeout_ms > SYNTH_TIMEOUT_S * 1000, (
        f"the client wait ({timeout_ms}ms) must exceed the server budget "
        f"({SYNTH_TIMEOUT_S * 1000}ms) so the server decides"
    )
    assert timeout_ms <= 15000, "but still be bounded, so silence is never indefinite"


def test_one_slow_phrase_does_not_latch_the_browser_voice():
    """A single timeout used to set serverVoice = false for the whole session.

    That silently swapped the child's voice for the browser default mid-lesson,
    which is what made the voice appear to change on its own.
    """
    assert re.search(r"SPEECH_FAIL_LIMIT\s*=\s*[2-9]", JS), (
        "a transient failure must not latch the fallback voice"
    )
    match = re.search(r"async function speak\(text, hint\).*?\n  \}", JS, re.S)
    assert match, "speak() must exist"
    body = match.group(0)
    assert '"timeout"' in body, "a slow synthesis must be handled separately from a failure"
    assert re.search(r"serverVoiceFails\s*\+=\s*1", body), "failures must be counted"
    assert re.search(
        r"if \(serverVoiceFails >= SPEECH_FAIL_LIMIT\)\s*\{\s*serverVoice = false", body
    ), "the browser voice is only latched after repeated failures"


def test_a_slow_phrase_is_quiet_rather_than_swapping_the_voice():
    """Silence for one turn beats a different person mid-sentence."""
    match = re.search(r"if \(outcome === \"timeout\"\)\s*\{(.*?)\n    \}", JS, re.S)
    assert match, "speak() must branch on a timeout"
    assert "browserSpeak" not in match.group(1), (
        "a timeout must not fall through to the browser voice"
    )


def test_the_fallback_voice_matches_the_gender_of_the_server_voice():
    """The server voice is female; an arbitrary platform default is often male."""
    assert "MALE_BROWSER_VOICE" in JS, "male voices must be excluded from the fallback"
    assert re.search(r"MALE_BROWSER_VOICE\s*=\s*/.*male.*/i", JS), "male voices are named"
    assert not re.search(r"/microsoft \(aria\|jenny\|guy", JS), (
        "the old preference list contained a male voice"
    )
    assert re.search(r"english\.filter\(\(v\) => !MALE_BROWSER_VOICE\.test\(v\.name\)\)", JS), (
        "the preference list must be drawn from the non-male voices"
    )


def test_the_client_warms_the_level_openers_on_load():
    """Switching level is the flow a reviewer always tries, and the first switch
    is the slowest. The openers are fixed sentences, so they can be composed up
    front instead of after the text has already appeared."""
    assert '"/api/warm"' in JS, "the client must ask for the openers"
    assert "function warmVoice" in JS
    assert re.search(r"loadHealth\(\)\.then\(\(\) => \{[^}]*warmVoice\(\)", JS, re.S), (
        "warming must happen after the page has connected"
    )


def test_the_client_tells_the_server_the_prosody_speed():
    """Speed is part of the server's cache key, so the pre-composed audio must be
    built with the same value the child's request will use."""
    assert re.search(r"currentSpeed\s*=\s*\(\)\s*=>", JS)
    assert len(re.findall(r"speed:\s*currentSpeed\(\)", JS)) >= 3, (
        "chat, session and level all need it"
    )


def test_a_level_jump_does_not_answer_its_own_question():
    """The jump already asks a level-appropriate item.

    Auto-sending a follow-up message would be evaluated as an answer to that very
    item and scored as a miss, so the switcher must stay quiet.
    """
    match = re.search(r"async function switchLevel.*?\n  \}", JS, re.S)
    assert match, "switchLevel must exist"
    body = match.group(0)
    assert "send(" not in body, "the level jump must not send a message of its own"


def test_the_client_never_names_a_model_or_a_provider():
    """Pedagogy lives on the server; the browser only renders what it is told."""
    assert "/api/chat" in JS
    lowered = JS.lower()
    for forbidden in ("gemini", "openrouter", "openai", "gpt-", "anthropic", "claude"):
        assert forbidden not in lowered, f"app.js must not name {forbidden!r}"


def test_the_client_only_shows_progress_and_never_decides_it():
    """The Settings panel displays attempts and mastery, so those words appear.

    What must never appear is the decision itself. Two checks pin that: no
    promotion vocabulary, and no field-level write into the session blob - the
    client only ever replaces it wholesale with what the server returned.
    """
    lowered = JS.lower()
    for forbidden in ("promote", "demote"):
        assert forbidden not in lowered, f"app.js must not contain {forbidden!r}"
    assert not re.search(r"\bsession\.[a-z_]+\s*=(?!=)", JS), (
        "app.js may read progression fields to display them, but must never assign "
        "into the session blob field by field"
    )
    assert re.search(r"session\s*=\s*(data\.session|loadSession\(\))", JS), (
        "the client should only ever take the session wholesale from the server"
    )


# ------------------------------------------------------------------ wording

def test_the_ui_never_tells_a_child_they_are_wrong():
    for name, text in (("index.html", HTML), ("app.js", JS)):
        assert not re.search(r"\bwrong\b", text, re.I), f"{name} must never say 'wrong'"
