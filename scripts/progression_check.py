#!/usr/bin/env python
"""Drive a live deployment through all three levels to prove progression works.

The unit tests cover the promotion rule in isolation. This script checks the
*integrated* thing: that a real conversation against a running deployment moves
a child from L1 to L2 to L3, that the curriculum content changes with the level,
and that the 3-learning-turn ceiling and the easing rule are visible in a real
session.

    python scripts/progression_check.py https://your-service.run.app
    python scripts/progression_check.py http://127.0.0.1:8080

How it answers
--------------
The session blob returned by ``/api/chat`` carries ``pending_item``: the exact
item the child is expected to answer next. The script looks that id up in the
same ``data/curriculum.json`` the server is using and replies with the expected
text. That is deliberate — an earlier version of this script sent "let's learn"
before each answer, not realising that a message sent while a question is
pending is *scored as an answer to that question*. Every second turn was
therefore recorded as a miss, the 4-of-5 accuracy window never filled, and
promotion (correctly) never fired. Answering the pending item directly is the
only faithful way to drive the engine.

A forced Engagement turn (the 3-learning-turn ceiling) keeps the pending item, so
the script simply answers it again on the next turn.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.request

CURRICULUM_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "curriculum.json"


def post(base: str, path: str, payload: dict, timeout: float = 30.0, attempts: int = 4) -> dict:
    """POST with a retry: sandbox and mobile networks drop the occasional request."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            base.rstrip("/") + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode())
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"giving up on {path} after {attempts} attempts: {last_error}")


def load_items() -> dict[str, dict]:
    data = json.loads(CURRICULUM_PATH.read_text(encoding="utf-8"))
    items: dict[str, dict] = {}
    for number, level in data["levels"].items():
        for item in level["items"]:
            items[item["id"]] = {**item, "level": int(number)}
    return items


def answer_for(item: dict) -> str:
    """The reply a child would give to get this item right."""
    if item.get("kind") == "open":
        accepts = item.get("accept") or []
        if accepts:
            return f"I like {accepts[0]}"
        return "I played outside with my friend"
    return item.get("expect") or item["text"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--max-turns", type=int, default=70)
    args = parser.parse_args()

    items = load_items()
    session = post(args.base_url, "/api/session", {"nickname": "Ana"})["session"]
    print(f"start: level={session['level']}  ({len(items)} curriculum items loaded)\n")

    levels_seen = [session["level"]]
    items_seen: dict[int, set[str]] = {}
    promotions: list[int] = []
    eases = 0

    for turn in range(1, args.max_turns + 1):
        pending = session.get("pending_item")
        if pending and pending in items:
            message = answer_for(items[pending])
        else:
            # Nothing pending: start a lesson.
            message = "let's learn"

        payload = post(args.base_url, "/api/chat", {"session": session, "message": message})
        session = payload["session"]
        debug = payload.get("debug") or {}

        level = int(session["level"])
        if level != levels_seen[-1]:
            levels_seen.append(level)
        # Attribute the item to the level it belongs to, not to the level the
        # child was on after a promotion mid-turn.
        if pending and debug.get("correct") is not None:
            items_seen.setdefault(items[pending]["level"], set()).add(pending)
        if debug.get("promoted"):
            promotions.append(level)
        if debug.get("eased"):
            eases += 1

        flag = ""
        if debug.get("promoted"):
            flag = "   <-- PROMOTED to L%d" % level
        elif debug.get("demoted"):
            flag = "   <-- DEMOTED"
        elif debug.get("eased"):
            flag = "   (eased: hint + weakest item)"
        elif debug.get("mode") == "Engagement":
            flag = "   (break: 3 learning turns reached)"

        print(
            f"t{turn:3}  L{level}  mode={str(debug.get('mode')):12} "
            f"correct={str(debug.get('correct')):5} attempts={debug.get('attempts_at_level'):3} "
            f"answering={pending or '-':18} "
            f"'{message[:34]}'{flag}"
        )

        # Once the child has answered three distinct L3 items, the path is proven.
        if level >= 3 and len(items_seen.get(3, ())) >= 3:
            break

    print()
    print(f"levels visited : {levels_seen}")
    print(f"promotions     : {promotions}")
    print(f"eased turns    : {eases}")
    for lvl in sorted(items_seen):
        sample = sorted(items_seen[lvl])
        print(f"  L{lvl}: {len(sample)} distinct items answered, e.g. {sample[:3]}")

    ok = levels_seen[:3] == [1, 2, 3] and len(items_seen.get(3, set())) >= 3
    print()
    if ok:
        print("PASS: a real conversation promoted the child L1 -> L2 -> L3 and served")
        print("      level-3 content at each step.")
        return 0
    print(f"FAIL: levels visited were {levels_seen}, expected to reach [1, 2, 3] with 3 L3 items.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
