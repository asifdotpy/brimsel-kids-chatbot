"""In-process latency and behaviour metrics.

Deliberately tiny: a bounded ring buffer of the last N turns. Enough to report
honest p50/p95 numbers from real traffic via ``GET /api/metrics`` without
adding a metrics service to a prototype.

Samples are ephemeral by design and contain no child data - only timings, the
mode that ran and whether the fallback replied.
"""

from __future__ import annotations

import threading
from collections import deque


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile. Returns 0.0 for an empty sample."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    rank = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[rank], 1)


class LatencyRecorder:
    def __init__(self, capacity: int = 2000) -> None:
        self._total: deque[float] = deque(maxlen=capacity)
        self._llm: deque[float] = deque(maxlen=capacity)
        self._by_mode: dict[str, deque[float]] = {}
        self._fallbacks = 0
        self._llm_calls = 0
        self._llm_errors = 0
        self._lock = threading.Lock()

    def record(self, total_ms: float, mode: str, llm_ms: float | None = None,
               used_fallback: bool = False, llm_ok: bool = False, llm_called: bool = False) -> None:
        with self._lock:
            self._total.append(float(total_ms))
            if llm_ms is not None:
                self._llm.append(float(llm_ms))
            self._by_mode.setdefault(mode, deque(maxlen=500)).append(float(total_ms))
            if used_fallback:
                self._fallbacks += 1
            if llm_called:
                self._llm_calls += 1
                if not llm_ok:
                    self._llm_errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            totals = list(self._total)
            llm = list(self._llm)
            by_mode = {
                mode: {
                    "count": len(samples),
                    "p50_ms": percentile(list(samples), 50),
                    "p95_ms": percentile(list(samples), 95),
                }
                for mode, samples in sorted(self._by_mode.items())
            }
            count = len(totals)
            fallbacks = self._fallbacks
            llm_calls = self._llm_calls
            llm_errors = self._llm_errors

        return {
            "turns": count,
            "total_ms": {
                "p50": percentile(totals, 50),
                "p95": percentile(totals, 95),
                "min": round(min(totals), 1) if totals else 0.0,
                "max": round(max(totals), 1) if totals else 0.0,
                "mean": round(sum(totals) / count, 1) if count else 0.0,
            },
            "llm_ms": {
                "samples": len(llm),
                "p50": percentile(llm, 50),
                "p95": percentile(llm, 95),
            },
            "llm_calls": llm_calls,
            "llm_errors": llm_errors,
            "fallback_replies": fallbacks,
            "fallback_rate": round(fallbacks / count, 3) if count else 0.0,
            "by_mode": by_mode,
            "note": "In-process sample since this instance started. Empty until turns are served.",
        }


recorder = LatencyRecorder()
