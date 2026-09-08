from __future__ import annotations

from datetime import UTC, datetime

from anyio import get_cancelled_exc_class
import pytest

from app.events.operational_models import (
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_stream import OperationalEventDispatcher


class RedisFake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str, bytes, int, int]] = []
        self.closed = False

    async def eval(
        self,
        script: str,
        key_count: int,
        stream: str,
        payload: bytes,
        retention_ms: int,
        maxlen: int,
    ) -> str:
        self.calls.append((script, key_count, stream, payload, retention_ms, maxlen))
        return "1-0"

    async def aclose(self) -> None:
        self.closed = True


class FailingRedisFake(RedisFake):
    async def eval(
        self,
        script: str,
        key_count: int,
        stream: str,
        payload: bytes,
        retention_ms: int,
        maxlen: int,
    ) -> str:
        raise OSError("redis unavailable")


class CancellingRedisFake(RedisFake):
    def __init__(self) -> None:
        super().__init__()
        self._cancellations_remaining = 1

    async def eval(
        self,
        script: str,
        key_count: int,
        stream: str,
        payload: bytes,
        retention_ms: int,
        maxlen: int,
    ) -> str:
        if self._cancellations_remaining:
            self._cancellations_remaining -= 1
            raise get_cancelled_exc_class()()
        return await super().eval(script, key_count, stream, payload, retention_ms, maxlen)


class SecondarySinkFake:
    def __init__(self) -> None:
        self.persisted: list[OperationalEvent] = []

    async def persist(self, event: OperationalEvent) -> None:
        self.persisted.append(event)


def event(severity: EventSeverity = EventSeverity.INFO) -> OperationalEvent:
    return OperationalEvent(
        occurred_at=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        source=EventSource.SYSTEM,
        category=EventCategory.SYSTEM,
        severity=severity,
        event_type="system.started",
        payload=SystemPayload(component="automation-service", state="started"),
    )


@pytest.mark.asyncio
async def test_dispatcher_drops_only_the_overflowing_bounded_queue_and_drains_priority_first() -> (
    None
):
    # Given: routine and priority queues at their fixed capacities.
    redis = RedisFake()
    secondary = SecondarySinkFake()
    dispatcher = OperationalEventDispatcher(redis, secondary)
    for _ in range(1_793):
        dispatcher.emit_nowait(event())
    for _ in range(257):
        dispatcher.emit_nowait(event(EventSeverity.ERROR))

    # When: lifecycle draining publishes every retained item.
    await dispatcher.start()
    await dispatcher.drain()

    # Then: producer overflow is observable and error events precede routine events.
    health = dispatcher.health()
    assert health.dropped_routine == 1
    assert health.dropped_priority == 1
    assert health.published == 2_048
    assert b'"severity":"error"' in redis.calls[0][3]
    assert "XADD" in redis.calls[0][0]
    assert "MINID" in redis.calls[0][0]
    assert "MAXLEN" in redis.calls[0][0]
    assert redis.calls[0][4:] == (86_400_000, 50_000)
    assert len(secondary.persisted) == 2_048


@pytest.mark.asyncio
async def test_dispatcher_records_redis_failure_without_producer_io_and_composes_secondary_sink() -> (
    None
):
    # Given: one unavailable Redis client and one independently configured secondary sink.
    secondary = SecondarySinkFake()
    dispatcher = OperationalEventDispatcher(FailingRedisFake(), secondary)

    # When: a producer emits synchronously and lifecycle draining encounters Redis failure.
    dispatcher.emit_nowait(event())
    await dispatcher.drain()

    # Then: the failure is measured without raising or invoking a sink before stream publication.
    assert dispatcher.health().failed_dispatches == 1
    assert secondary.persisted == []


@pytest.mark.asyncio
async def test_dispatcher_retains_accepted_event_when_publication_is_cancelled_then_retries() -> (
    None
):
    # Given: a Redis call that is cancelled once after the producer accepts an event.
    redis = CancellingRedisFake()
    dispatcher = OperationalEventDispatcher(redis)
    dispatcher.emit_nowait(event())

    # When: the first drain is cancelled and a later lifecycle drain retries it.
    with pytest.raises(get_cancelled_exc_class()):
        await dispatcher.drain()
    await dispatcher.close()

    # Then: cancellation never drops accepted work and close publishes it exactly once.
    assert dispatcher.health().queued_routine == 0
    assert len(redis.calls) == 1
