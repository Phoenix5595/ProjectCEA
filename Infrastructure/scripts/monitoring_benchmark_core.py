"""Stdlib-only request execution and aggregation for monitoring benchmarks."""

from __future__ import annotations

import asyncio  # noqa: ANYIO_OK - the required CLI contract specifies asyncio.Semaphore.
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
from math import ceil
import socket
import time
from typing import Final
import urllib.error
import urllib.parse
import urllib.request

TARGETS: Final[tuple[str, ...]] = ("range", "stats", "live", "history", "projection")
WINDOW_HOURS: Final[dict[str, int]] = {"1h": 1, "3h": 3, "24h": 24, "7d": 168}


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Validated benchmark inputs used by the request executor."""

    base_url: str
    location: str
    node: str
    targets: tuple[str, ...]
    window: str
    samples: int
    warmup: int
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class Observation:
    """One finished HTTP attempt, including failures that remain measurable."""

    status: int | None
    error: str | None
    byte_count: int
    latency_ms: float


@dataclass(slots=True)
class Metrics:
    """Mutable accumulator because each request contributes one measurement."""

    status_counts: Counter[str] = field(default_factory=Counter)
    error_counts: Counter[str] = field(default_factory=Counter)
    byte_samples: list[int] = field(default_factory=list)
    latency_samples: list[float] = field(default_factory=list)

    def add(self, observation: Observation) -> None:
        """Record every completed attempt without treating expected failures as fatal."""
        if observation.status is not None:
            self.status_counts[str(observation.status)] += 1
        if observation.error is not None:
            self.error_counts[observation.error] += 1
        self.byte_samples.append(observation.byte_count)
        self.latency_samples.append(observation.latency_ms)

    def as_json(
        self,
    ) -> dict[str, int | float | None | dict[str, int] | dict[str, float | int | None]]:
        """Return the stable machine-readable metric contract."""
        bytes_min = min(self.byte_samples) if self.byte_samples else None
        bytes_max = max(self.byte_samples) if self.byte_samples else None
        bytes_avg = sum(self.byte_samples) / len(self.byte_samples) if self.byte_samples else None
        return {
            "status_counts": dict(sorted(self.status_counts.items())),
            "error_counts": dict(sorted(self.error_counts.items())),
            "bytes_min": bytes_min,
            "bytes_max": bytes_max,
            "bytes_avg": bytes_avg,
            "latency_ms": {
                "p50": nearest_rank_percentile(self.latency_samples, 50),
                "p95": nearest_rank_percentile(self.latency_samples, 95),
                "p99": nearest_rank_percentile(self.latency_samples, 99),
                "samples": len(self.latency_samples),
            },
        }


@dataclass(slots=True)
class FlightCounter:
    """Track executor-side overlap on the single asyncio event loop."""

    current: int = 0
    maximum: int = 0

    def enter(self) -> None:
        """Mark one request as in flight."""
        self.current += 1
        self.maximum = max(self.maximum, self.current)

    def leave(self) -> None:
        """Mark one request as complete."""
        self.current -= 1


def nearest_rank_percentile(samples: list[float], percentile: int) -> float | None:
    """Return the nearest-rank percentile: ceil(p/100*n), using one-based ranks."""
    if not samples:
        return None
    ordered = sorted(samples)
    index = ceil(percentile / 100 * len(ordered)) - 1
    return ordered[index]


def window_bounds(window: str, now: datetime | None = None) -> tuple[str, str]:
    """Compute explicit UTC ISO-8601 query bounds for a supported window."""
    end = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    start = end - timedelta(hours=WINDOW_HOURS[window])
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


def endpoint_url(config: BenchmarkConfig, target: str, bounds: tuple[str, str]) -> str:
    """Build an encoded, read-only monitoring endpoint URL."""
    base = config.base_url.rstrip("/")
    location = urllib.parse.quote(config.location, safe="")
    node = urllib.parse.quote(config.node, safe="")
    match target:
        case "range":
            path = f"/api/sensors/monitoring/range/{location}"
        case "stats":
            path = f"/api/sensors/monitoring/stats/{location}"
        case "live":
            return f"{base}/api/sensors/monitoring/live/{location}/{node}"
        case "history":
            path = f"/api/monitoring/control/{location}/history"
        case "projection":
            path = f"/api/monitoring/control/{location}/projection"
        case _:
            raise RuntimeError(f"unsupported validated target: {target}")
    return f"{base}{path}?{urllib.parse.urlencode({'start': bounds[0], 'end': bounds[1]})}"


def _request(
    opener: urllib.request.OpenerDirector, url: str, timeout_seconds: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, method="GET")
    with opener.open(request, timeout=timeout_seconds) as response:
        return response.status, response.read()


async def fetch(
    opener: urllib.request.OpenerDirector, url: str, timeout_seconds: float
) -> Observation:
    """Issue one GET and classify expected transport, HTTP, and JSON failures."""
    started = time.perf_counter()
    try:
        status, body = await asyncio.to_thread(_request, opener, url, timeout_seconds)
    except urllib.error.HTTPError as error:
        body = error.read()
        error_class = "HTTP-4xx" if 400 <= error.code < 500 else "HTTP-5xx"
        return Observation(
            error.code, error_class, len(body), (time.perf_counter() - started) * 1000
        )
    except TimeoutError:
        return Observation(None, "timeout", 0, (time.perf_counter() - started) * 1000)
    except urllib.error.URLError as error:
        error_class = (
            "timeout" if isinstance(error.reason, (socket.timeout, TimeoutError)) else "connect"
        )
        return Observation(None, error_class, 0, (time.perf_counter() - started) * 1000)
    try:
        json.loads(body)
    except json.JSONDecodeError:
        return Observation(status, "parse", len(body), (time.perf_counter() - started) * 1000)
    return Observation(status, None, len(body), (time.perf_counter() - started) * 1000)


async def sample(config: BenchmarkConfig) -> list[dict[str, object]]:
    """Warm each endpoint then collect its requested measured sample count."""
    bounds = window_bounds(config.window)
    results: list[dict[str, object]] = []
    for target in config.targets:
        opener = urllib.request.build_opener()
        url = endpoint_url(config, target, bounds)
        for _ in range(config.warmup):
            _ = await fetch(opener, url, config.timeout_seconds)
        metrics = Metrics()
        for _ in range(config.samples):
            metrics.add(await fetch(opener, url, config.timeout_seconds))
        results.append({"endpoint": target, "window": config.window, **metrics.as_json()})
    return results


async def soak(config: BenchmarkConfig, viewers: int, seconds: float) -> dict[str, object]:
    """Run exactly ``viewers`` independent bounded sessions with one-Hz live reads."""
    bounds = window_bounds(config.window)
    semaphore = asyncio.Semaphore(viewers)
    flight = FlightCounter()
    aggregate = {target: Metrics() for target in config.targets}
    viewer_metrics = [{target: Metrics() for target in config.targets} for _ in range(viewers)]
    window_targets = tuple(target for target in config.targets if target != "live")

    async def issue(
        opener: urllib.request.OpenerDirector, target: str, metrics: Metrics, total: Metrics
    ) -> None:
        flight.enter()
        try:
            observation = await fetch(
                opener, endpoint_url(config, target, bounds), config.timeout_seconds
            )
            metrics.add(observation)
            total.add(observation)
        finally:
            flight.leave()

    async def viewer(index: int) -> None:
        async with semaphore:
            await asyncio.sleep(index * (0.1 / max(viewers - 1, 1)))
            opener = urllib.request.build_opener()
            metrics = viewer_metrics[index]
            for target in window_targets:
                await issue(opener, target, metrics[target], aggregate[target])
            deadline = time.monotonic() + seconds
            next_live = time.monotonic()
            while next_live < deadline:
                await asyncio.sleep(max(0.0, next_live - time.monotonic()))
                await issue(
                    opener,
                    "live",
                    metrics.setdefault("live", Metrics()),
                    aggregate.setdefault("live", Metrics()),
                )
                next_live += 1.0

    _ = await asyncio.gather(*(viewer(index) for index in range(viewers)))
    endpoint_totals = [
        {"endpoint": target, **metrics.as_json()} for target, metrics in aggregate.items()
    ]
    viewer_totals = [
        {
            "viewer": index + 1,
            "endpoints": [
                {"endpoint": target, **metrics.as_json()} for target, metrics in totals.items()
            ],
        }
        for index, totals in enumerate(viewer_metrics)
    ]
    status_counts = Counter[str]()
    error_counts = Counter[str]()
    for metrics in aggregate.values():
        status_counts.update(metrics.status_counts)
        error_counts.update(metrics.error_counts)
    return {
        "viewers": viewers,
        "seconds": seconds,
        "aggregate": {
            "status_counts": dict(sorted(status_counts.items())),
            "error_counts": dict(sorted(error_counts.items())),
            "max_concurrent_in_flight": flight.maximum,
            "endpoints": endpoint_totals,
        },
        "per_viewer": viewer_totals,
    }
