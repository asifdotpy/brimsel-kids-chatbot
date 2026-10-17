#!/usr/bin/env python
"""Public-URL smoke test and latency measurement.

Drives a scripted child through a realistic conversation against a running
deployment and reports the mode the engine chose, whether the model or the
fallback replied, and end-to-end latency.

    python scripts/smoke.py https://your-service.run.app
    python scripts/smoke.py http://127.0.0.1:8080 --turns 30

Latency is measured client-side, so it includes the network hop. That is
deliberate: it is the number a child actually experiences.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

SCRIPT = [
    ("hello buddy", None),
    ("let's learn", None),
    ("cat", None),
    ("dog", None),
    ("ball", None),
    ("I feel sad", "sad"),
    ("I feel happy", "happy"),
    ("let's play a game", None),
    ("let's learn", None),
    ("the cat is big", None),
    ("the dog is big", None),
    ("I went to the park with my mom", None),
    ("what is your name?", None),
    ("ok", None),
    ("apple", None),
]


def post(base: str, path: str, payload: dict, timeout: float = 30.0) -> tuple[dict, float]:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode())
    return body, (time.perf_counter() - started) * 1000


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as response:
        return json.loads(response.read().decode())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url", help="Base URL of the running app")
    parser.add_argument("--turns", type=int, default=0,
                        help="Also run N repeat turns and report p50/p95")
    parser.add_argument("--json", action="store_true", help="Print raw results as JSON")
    args = parser.parse_args()

    base = args.base_url
    try:
        health = get(base, "/api/health")
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"FAIL  cannot reach {base}: {exc}")
        return 2

    print(f"health: status={health['status']} version={health['version']}")
    print(f"        provider={health['llm']['provider']} model={health['llm']['model']} "
          f"configured={health['llm']['configured']}")
    print(f"        curriculum={health['curriculum_version']} levels={health['levels']}")
    print()

    session = post(base, "/api/session", {"nickname": "Ana"})[0]
    print(f"buddy  {session['reply']}")
    state = session["session"]

    rows = []
    for message, mood in SCRIPT:
        payload, latency = post(base, "/api/chat", {"session": state, "message": message, "mood": mood})
        state = payload["session"]
        debug = payload.get("debug", {})
        used = "fallback" if debug.get("fallback_used") else f"model:{debug.get('llm', {}).get('latency_ms', 0)}ms"
        print(f"child  {message!r}")
        print(f"buddy  {payload['reply']}")
        print(f"       mode={debug.get('mode')} ({debug.get('mode_reason')}) level={state['level']} "
              f"correct={debug.get('correct')} reply={used} total={latency:.0f}ms")
        print()
        rows.append({
            "message": message,
            "mode": debug.get("mode"),
            "reason": debug.get("mode_reason"),
            "level": state["level"],
            "correct": debug.get("correct"),
            "fallback": bool(debug.get("fallback_used")),
            "latency_ms": round(latency, 1),
        })

    if args.turns:
        print(f"--- measuring {args.turns} sequential turns ---")
        timings = []
        for index in range(args.turns):
            message = SCRIPT[index % len(SCRIPT)][0]
            payload, latency = post(base, "/api/chat", {"session": state, "message": message})
            state = payload["session"]
            timings.append(latency)
        timings.sort()
        p50 = statistics.median(timings)
        p95 = timings[min(len(timings) - 1, int(round(0.95 * (len(timings) - 1))))]
        print(f"samples={len(timings)} p50={p50:.0f}ms p95={p95:.0f}ms "
              f"min={timings[0]:.0f}ms max={timings[-1]:.0f}ms")
        rows.append({"measurement": {"samples": len(timings), "p50_ms": round(p50, 1), "p95_ms": round(p95, 1)}})

    metrics = get(base, "/api/metrics")
    print()
    print("server metrics:")
    print(json.dumps(metrics, indent=2)[:1200])

    if args.json:
        print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
