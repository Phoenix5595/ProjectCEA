#!/usr/bin/env python3
"""
Event metrics for automation service.
In-memory counters, no external deps.
"""

import threading
from typing import Any


class EventMetrics:
    """
    Simple in-memory event metrics collector.

    Tracks:
      - published counts per event_type
      - consumed counts per event_type
      - per-event processing latencies (ms)
    """

    def __init__(self) -> None:
        self._published: dict[str, int] = {}
        self._consumed: dict[str, int] = {}
        self._latency_ms: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def track_published(self, event_type: str) -> None:
        if not event_type:
            return
        with self._lock:
            self._published[event_type] = self._published.get(event_type, 0) + 1

    def track_consumed(self, event_type: str) -> None:
        if not event_type:
            return
        with self._lock:
            self._consumed[event_type] = self._consumed.get(event_type, 0) + 1

    def track_processing_latency(self, event_type: str, duration_ms: float) -> None:
        if not event_type:
            return
        with self._lock:
            lst = self._latency_ms.setdefault(event_type, [])
            lst.append(float(duration_ms))

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            latency_stats: dict[str, dict[str, Any]] = {}
            for et, durations in self._latency_ms.items():
                if durations:
                    total = sum(durations)
                    n = len(durations)
                    avg = total / n
                    latency_stats[et] = {
                        "count": n,
                        "average_ms": avg,
                        "min_ms": min(durations),
                        "max_ms": max(durations),
                    }
                else:
                    latency_stats[et] = {
                        "count": 0,
                        "average_ms": 0.0,
                        "min_ms": None,
                        "max_ms": None,
                    }
            return {
                "published": dict(self._published),
                "consumed": dict(self._consumed),
                "latency_ms": latency_stats,
            }
