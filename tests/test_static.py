"""The static UI is part of the deliverable, so a few invariants are pinned here.

These are deliberately about *contracts* rather than pixels: the things that
would be silently broken by a plausible-looking edit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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
    """Buttons are sized for small hands, not for a mouse."""
    assert "--tap: 64px" in CSS
    assert "min-height: var(--tap)" in CSS


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


@pytest.mark.parametrize("element_id", ["typing", "debugPanel"])
def test_optional_panels_start_hidden(element_id):
    match = re.search(rf'id="{element_id}"[^>]*', HTML)
    assert match, f"#{element_id} is missing from index.html"
    assert "hidden" in match.group(0), f"#{element_id} must start hidden"


def test_the_typing_row_sets_display_so_the_guard_is_load_bearing():
    assert re.search(r"\.typing\s*\{[^}]*display:", CSS, re.S)


# ------------------------------------------------------------------ the engine panel

def test_the_debug_panel_only_reserves_a_column_when_open():
    """With an always-on second column the chat card is auto-placed into it and
    the narrow mood card takes the wide column, which reads backwards."""
    assert ".layout.is-debug" in CSS, "the wide layout must be gated on the panel being open"
    assert re.search(r'grid-template-columns:\s*minmax\(0,\s*1fr\)\s*380px', CSS)
    assert 'classList.toggle("is-debug"' in JS, "app.js must toggle the layout class"


# ------------------------------------------------------------------ no secrets

def test_no_key_or_provider_url_reaches_the_browser():
    """The brief requires that no API key is ever exposed client-side."""
    for name, text in (("index.html", HTML), ("app.js", JS), ("styles.css", CSS)):
        lowered = text.lower()
        for forbidden in ("api_key", "apikey", "generativelanguage", "openrouter.ai", "bearer "):
            assert forbidden not in lowered, f"{name} must not mention {forbidden!r}"
        assert not re.search(r"AIza[0-9A-Za-z_\-]{10,}", text), f"{name} looks like it embeds a Google key"
        assert not re.search(r"sk-or-v1", text), f"{name} looks like it embeds an OpenRouter key"


def test_the_client_never_names_a_model_or_a_provider():
    """Pedagogy lives on the server; the browser only renders what it is told."""
    assert "/api/chat" in JS
    lowered = JS.lower()
    for forbidden in ("gemini", "openrouter", "openai", "gpt-", "anthropic", "claude"):
        assert forbidden not in lowered, f"app.js must not name {forbidden!r}"


def test_the_client_does_not_compute_progression():
    assert not re.search(r"\b(promote|demote|mastery|attempts_at_level)\b", JS, re.I)


# ------------------------------------------------------------------ wording

def test_the_ui_never_tells_a_child_they_are_wrong():
    for name, text in (("index.html", HTML), ("app.js", JS)):
        assert not re.search(r"\bwrong\b", text, re.I), f"{name} must never say 'wrong'"
