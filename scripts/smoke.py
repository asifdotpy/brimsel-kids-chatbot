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


def post(base: str, path: str, payload: dict, timeout: float = 30.0,
         attempts: int = 3) -> tuple[dict, float]:
    """POST and time it. Retries on a dropped connection, not on a slow one.

    Only the successful attempt is timed, so a retry cannot inflate the latency
    sample; a request that fails three times is raised rather than silently
    recorded as fast.
    """
    data = json.dumps(payload).encode()
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            base.rstrip("/") + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode())
            return body, (time.perf_counter() - started) * 1000
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001 - retried, then reported
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"network gave up on {path} after {attempts} attempts: {last_error}")


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as response:
        return json.loads(response.read().decode())


def percentile_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "p50": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))],
        "min": ordered[0],
        "max": ordered[-1],
    }


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
              f"correct={debug.get('correct')} reply={used} "
              f"server={payload.get('server_ms', 0)}ms total={latency:.0f}ms")
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
        # Three numbers, deliberately separate:
        #   client  - what the child's device waits for, network hop included
        #   server  - what the engine reports for itself (X-Response-Time-Ms)
        #   network - the difference, i.e. the hop the app cannot control
        # Reporting only the client number would hide how much of it is the
        # network; reporting only the server number would overstate the app.
        client: list[float] = []
        server: list[float] = []
        fallbacks = 0
        for index in range(args.turns):
            message = SCRIPT[index % len(SCRIPT)][0]
            payload, latency = post(base, "/api/chat", {"session": state, "message": message})
            state = payload["session"]
            client.append(latency)
            server.append(float(payload.get("server_ms") or 0.0))
            fallbacks += bool((payload.get("debug") or {}).get("fallback_used"))

        summary = {
            "samples": len(client),
            "fallbacks": fallbacks,
            "client": percentile_summary(client),
            "server": percentile_summary(server),
            "network": percentile_summary([c - s for c, s in zip(client, server)]),
        }
        print(f"samples={summary['samples']}  fallbacks={fallbacks}")
        for name in ("client", "server", "network"):
            stat = summary[name]
            print(f"  {name:8} p50={stat['p50']:.0f}ms  p95={stat['p95']:.0f}ms  "
                  f"min={stat['min']:.0f}ms  max={stat['max']:.0f}ms")

        # A bare GET isolates the hop from the turn: `/api/health` does no model
        # work and no state work, so its round trip is essentially network time.
        # That is the number to subtract before blaming the app for a slow turn.
        hop: list[float] = []
        for _ in range(20):
            started = time.perf_counter()
            try:
                get(base, "/api/health")
            except Exception:  # noqa: BLE001 - a failed probe is just a dropped sample
                continue
            hop.append((time.perf_counter() - started) * 1000)
        if hop:
            hop_stat = percentile_summary(hop)
            summary["hop_baseline"] = hop_stat
            print(f"  hop      p50={hop_stat['p50']:.0f}ms  p95={hop_stat['p95']:.0f}ms  "
                  f"min={hop_stat['min']:.0f}ms  max={hop_stat['max']:.0f}ms  "
                  f"(20 bare GET /api/health calls)")

        rows.append({"measurement": summary})

    metrics = get(base, "/api/metrics")
    print()
    print("server metrics:")
    print(json.dumps(metrics, indent=2)[:1200])

    if args.json:
        print(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
