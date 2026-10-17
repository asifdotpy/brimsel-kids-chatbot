"""Versioned curriculum loading.

The curriculum is data, not code. Editing ``data/curriculum.json`` changes what
children are taught without touching the engine. Items are validated on load so
a malformed curriculum fails loudly at startup instead of confusing a child.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_CURRICULUM_PATH = Path(__file__).resolve().parent.parent / "data" / "curriculum.json"

LEVELS = (1, 2, 3)
VALID_KINDS = ("repeat", "open")


@dataclass(frozen=True)
class Item:
    """A single teachable item."""

    id: str
    level: int
    kind: str
    text: str
    ask: str
    hint: str = ""
    expect: str = ""
    accept: tuple[str, ...] = ()

    @property
    def is_open(self) -> bool:
        return self.kind == "open"


@dataclass(frozen=True)
class Level:
    number: int
    name: str
    label: str
    focus: str
    items: tuple[Item, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Curriculum:
    version: str
    levels: dict[int, Level]

    def items(self, level: int) -> tuple[Item, ...]:
        lvl = self.levels.get(level)
        return lvl.items if lvl else ()

    def level(self, number: int) -> Level:
        return self.levels[number]

    def get(self, item_id: str) -> Item | None:
        for lvl in self.levels.values():
            for item in lvl.items:
                if item.id == item_id:
                    return item
        return None


def _fail(msg: str) -> None:
    raise ValueError(f"curriculum.json is invalid: {msg}")


def parse_curriculum(raw: dict) -> Curriculum:
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        _fail("missing string 'version'")

    raw_levels = raw.get("levels")
    if not isinstance(raw_levels, dict):
        _fail("missing 'levels' object")

    levels: dict[int, Level] = {}
    seen_ids: set[str] = set()

    for key, lvl in raw_levels.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            _fail(f"level key {key!r} is not an integer")
        if number not in LEVELS:
            _fail(f"unsupported level {number}")

        if not isinstance(lvl, dict):
            _fail(f"level {number} is not an object")
        raw_items = lvl.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            _fail(f"level {number} has no items")

        items: list[Item] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                _fail(f"level {number} has a non-object item")
            item_id = raw_item.get("id")
            kind = raw_item.get("kind")
            text = raw_item.get("text")
            ask = raw_item.get("ask")

            if not item_id or not isinstance(item_id, str):
                _fail(f"level {number} item is missing a string 'id'")
            if item_id in seen_ids:
                _fail(f"duplicate item id {item_id!r}")
            if kind not in VALID_KINDS:
                _fail(f"item {item_id!r} has kind {kind!r}, expected one of {VALID_KINDS}")
            if not text or not isinstance(text, str):
                _fail(f"item {item_id!r} is missing a string 'text'")

            expect = raw_item.get("expect", "")
            accept = raw_item.get("accept", [])
            if kind == "repeat" and not expect:
                _fail(f"repeat item {item_id!r} needs an 'expect' string")
            if kind == "open" and not isinstance(accept, list):
                _fail(f"open item {item_id!r} needs an 'accept' array")

            seen_ids.add(item_id)
            items.append(
                Item(
                    id=item_id,
                    level=number,
                    kind=kind,
                    text=text.strip(),
                    ask=(ask or text).strip(),
                    hint=(raw_item.get("hint") or "").strip(),
                    expect=expect.strip().lower(),
                    accept=tuple(str(a).strip().lower() for a in accept if str(a).strip()),
                )
            )

        levels[number] = Level(
            number=number,
            name=str(lvl.get("name") or f"L{number}"),
            label=str(lvl.get("label") or ""),
            focus=str(lvl.get("focus") or ""),
            items=tuple(items),
        )

    missing = [n for n in LEVELS if n not in levels]
    if missing:
        _fail(f"missing levels {missing}")

    return Curriculum(version=version, levels=levels)


def load_curriculum(path: str | os.PathLike[str] | None = None) -> Curriculum:
    """Load the curriculum from *path* (or ``$CURRICULUM_PATH``)."""
    resolved = Path(path or os.environ.get("CURRICULUM_PATH") or DEFAULT_CURRICULUM_PATH)
    with open(resolved, "r", encoding="utf-8") as handle:
        return parse_curriculum(json.load(handle))


@lru_cache(maxsize=4)
def _cached(path: str) -> Curriculum:
    return load_curriculum(path)


def get_curriculum(path: str | os.PathLike[str] | None = None) -> Curriculum:
    """Cached curriculum accessor used by the engine and the web app."""
    resolved = Path(path or os.environ.get("CURRICULUM_PATH") or DEFAULT_CURRICULUM_PATH)
    return _cached(str(resolved))
