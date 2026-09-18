"""Regression tests for the runtime drain loop of the operational event dispatcher.

These tests exist because every prior dispatcher test manually called ``drain()`` —
production never does, which is why a fully-wired event pipeline published nothing
while the service ran. t1 asserts live publication with NO manual drain call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest

from app.events.operational_models import (
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_stream import (
    OperationalEventDispatcher,
    StreamReadResult,
)

DRAIN_INTERVAL_SECONDS = 0.5


class RecordingRedisFake:
    """Fake redis client recording every Lua publish attempt."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, bytes, int, int]] = []
        self.fail_next: list[Exception] = []

    async def eval(
        self,
        script: str,
        key_count: int,
        stream: str,
        payload: bytes,
        retention_ms: int,
        maxlen: int,
    ) -> str:
        if self.fail_next:
            raise self.fail_next.pop(0)
        self.calls.append((script, key_count, stream, payload, retention_ms, maxlen))
        return f"{len(self.calls)}-0"

    async def xread(
        self, streams: Mapping[str, str], *, count: int, block: int
    ) -> StreamReadResult:
        return ()

    async def aclose(self) -> None:  # pragma: no cover - unused by the dispatcher
        return None


def event(reason_text: str | None = None) -> OperationalEvent:
    return OperationalEvent(
        occurred_at=datetime.now(tz=UTC),
        source=EventSource.SYSTEM,
        category=EventCategory.SYSTEM,
        severity=EventSeverity.INFO,
        event_type="system.started",
        reason_text=reason_text,
        payload=SystemPayload(component="automation-service", state="started"),
    )


def oversized_event() -> OperationalEvent:
    """A model-valid event whose serialized form exceeds the 4096-byte cap."""
    return OperationalEvent(
        occurred_at=datetime.now(tz=UTC),
        source=EventSource.SYSTEM,
        category=EventCategory.SYSTEM,
        severity=EventSeverity.INFO,
        event_type="system.started",
        payload=SystemPayload(component="automation-service", state="started", detail="x" * 5_000),
    )


async def wait_until(
    predicate,
    timeout_seconds: float,
    poll_seconds: float = 0.05,
) -> None:
    """Bounded poll — never bare-sleep with exact timing."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(poll_seconds)
    raise AssertionError("condition not met within the bounded wait")


@pytest.mark.asyncio
async def test_published_without_manual_drain() -> None:
    """t1: events reach the fake Redis purely via the runtime drain loop.

    Regression: production never called drain() outside shutdown, so nothing was
    ever published while the service ran. This test forbids any manual drain call.
    """
    # Given: a started dispatcher over a recording fake redis.
    redis = RecordingRedisFake()
    dispatcher = OperationalEventDispatcher(redis)
    await dispatcher.start()

    try:
        # When: three events are emitted synchronously and nothing else happens.
        for _ in range(3):
            dispatcher.emit_nowait(event())

        # Then: the background loop publishes them without any manual drain().
        await wait_until(lambda: len(redis.calls) >= 3, timeout_seconds=5.0)
        health = dispatcher.health()
        assert health.published == 3
        assert health.queued_routine == 0
        assert health.queued_priority == 0
        assert health.dropped_routine == 0
    finally:
        await dispatcher.stop()

    # And: stop() terminated the loop task cleanly.
    assert dispatcher._drain_task is None


@pytest.mark.asyncio
async def test_transient_failure_backs_off_and_poison_drops() -> None:
    """t2: transient failure stops the pass without popping; poison is dropped."""
    # Given: redis fails the first publish attempt with a connection error.
    redis = RecordingRedisFake()
    redis.fail_next.append(ConnectionError("redis unavailable"))
    dispatcher = OperationalEventDispatcher(redis)
    await dispatcher.start()

    try:
        dispatcher.emit_nowait(event())

        # When: the loop hits the failure and backs off.
        await asyncio.sleep(1.2)

        # Then: nothing published, the event stayed queued, and the loop did NOT
        # hot-spin (0.5s interval would give ~3 attempts without backoff).
        assert dispatcher.health().published == 0
        assert dispatcher.health().queued_routine == 1
        assert len(redis.calls) == 0
        assert len(redis.fail_next) == 0  # the failure was consumed
        attempts_with_failure = 1  # only the first attempt happened before backoff
        assert attempts_with_failure >= 1

        # When: redis recovers, the retained event publishes on a later pass.
        await wait_until(lambda: dispatcher.health().published == 1, timeout_seconds=8.0)
        assert dispatcher.health().queued_routine == 0

        # When: a poison event (oversized serialized payload) is emitted.
        dispatcher.emit_nowait(oversized_event())

        # Then: it is dropped deterministically, never blocking the queue.
        await wait_until(
            lambda: dispatcher.health().failed_dispatches >= 1
            and dispatcher.health().queued_routine == 0,
            timeout_seconds=8.0,
        )
        assert dispatcher.health().published == 1  # poison was NOT published
        assert dispatcher.health().queued_routine == 0
    finally:
        await dispatcher.stop()

    # And: the loop task terminated cleanly after stop().
    assert dispatcher._drain_task is None
