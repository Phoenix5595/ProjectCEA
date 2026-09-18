"""Bounded, non-blocking publication and reading for operational events."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
import contextlib
from dataclasses import dataclass
from typing import Final, Protocol, TypeAlias

import anyio
from pydantic import ValidationError
from redis.exceptions import RedisError

from app.events.operational_models import (
    OperationalEvent,
    OperationalEventSerializationError,
    serialize_operational_event,
)
from app.events.operational_ports import OperationalEventSecondarySink, OperationalEventSink
from shared.infra_logging import get_logger
from shared.redis_keys import (
    OPERATIONAL_EVENTS_MAXLEN,
    OPERATIONAL_EVENTS_RETENTION_MS,
    OPERATIONAL_EVENTS_STREAM,
)

logger = get_logger(__name__)

ROUTINE_QUEUE_CAPACITY: Final[int] = 1_792
PRIORITY_QUEUE_CAPACITY: Final[int] = 256
READ_BATCH_SIZE: Final[int] = 100
READ_BLOCK_MILLISECONDS: Final[int] = 1_000
READ_FAILURE_BACKOFF_SECONDS: Final[float] = 1.0
EVENT_FIELD: Final[str] = "event"

# Runtime drain loop: producers enqueue synchronously; the loop publishes without
# ever blocking them. It wakes early once the queues hold DRAIN_WAKE_THRESHOLD
# events, otherwise it sweeps on the fixed interval.
DRAIN_INTERVAL_SECONDS: Final[float] = 0.5
DRAIN_WAKE_THRESHOLD: Final[int] = 64
DRAIN_FAILURE_BACKOFF_SECONDS: Final[float] = 5.0

_PUBLISH_OUTCOME_OK: Final[str] = "ok"
_PUBLISH_OUTCOME_DROPPED: Final[str] = "dropped"
_PUBLISH_OUTCOME_TRANSIENT: Final[str] = "transient"

_PUBLISH_SCRIPT: Final[str] = """
local server_time = redis.call('TIME')
local now_ms = tonumber(server_time[1]) * 1000 + math.floor(tonumber(server_time[2]) / 1000)
local min_id = tostring(now_ms - tonumber(ARGV[2])) .. '-0'
local entry_id = redis.call('XADD', KEYS[1], '*', 'event', ARGV[1])
redis.call('XTRIM', KEYS[1], 'MINID', '~', min_id)
redis.call('XTRIM', KEYS[1], 'MAXLEN', '=', ARGV[3])
return entry_id
"""

StreamField: TypeAlias = str | bytes
StreamFields: TypeAlias = Mapping[StreamField, StreamField]
StreamMessage: TypeAlias = tuple[StreamField, StreamFields]
StreamReadResult: TypeAlias = Sequence[tuple[StreamField, Sequence[StreamMessage]]]


class OperationalRedis(Protocol):
    """Redis operations required by the operational event stream."""

    def eval(
        self,
        script: str,
        key_count: int,
        stream: str,
        payload: bytes,
        retention_ms: int,
        maxlen: int,
    ) -> Awaitable[str | bytes]: ...

    def xread(
        self, streams: Mapping[str, str], *, count: int, block: int
    ) -> Awaitable[StreamReadResult]: ...

    def aclose(self) -> Awaitable[None]: ...


@dataclass(frozen=True, slots=True)
class OperationalEventDispatchHealth:
    """Observable state of the bounded publisher queues."""

    queued_routine: int
    queued_priority: int
    dropped_routine: int
    dropped_priority: int
    published: int
    failed_dispatches: int
    secondary_failures: int


@dataclass(frozen=True, slots=True)
class OperationalStreamEntry:
    """A validated operational event with its Redis stream identifier."""

    id: str
    event: OperationalEvent


@dataclass(frozen=True, slots=True)
class OperationalEventStreamHealth:
    """Observable state of the operational stream reader."""

    malformed_entries: int
    read_failures: int


class OperationalEventDispatcher(OperationalEventSink):
    """Owns bounded queues so operational producers never await Redis.

    A runtime drain task (started by :meth:`start`, stopped by :meth:`stop`)
    publishes queued events to Redis continuously, so the stream stays live
    without any shutdown-time flush.
    """

    def __init__(
        self,
        redis: OperationalRedis,
        secondary_sink: OperationalEventSecondarySink | None = None,
    ) -> None:
        self._redis = redis
        self._secondary_sink = secondary_sink
        self._routine: deque[OperationalEvent] = deque(maxlen=ROUTINE_QUEUE_CAPACITY)
        self._priority: deque[OperationalEvent] = deque(maxlen=PRIORITY_QUEUE_CAPACITY)
        self._dropped_routine = 0
        self._dropped_priority = 0
        self._published = 0
        self._failed_dispatches = 0
        self._secondary_failures = 0
        self._started = False
        self._drain_task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._drain_lock = asyncio.Lock()

    async def start(self) -> None:
        """Mark this lifecycle component ready and start the runtime drain loop."""
        self._started = True
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(
                self._drain_loop(), name="operational-event-drain-loop"
            )
            logger.info(
                "Operational event dispatcher drain loop started "
                "(interval=%.1fs, wake_threshold=%d)",
                DRAIN_INTERVAL_SECONDS,
                DRAIN_WAKE_THRESHOLD,
            )

    def emit_nowait(self, event: OperationalEvent) -> None:
        """Enqueue an event synchronously, dropping only when its queue is full."""
        queue, is_priority = self._queue_for(event)
        if len(queue) == queue.maxlen:
            if is_priority:
                self._dropped_priority += 1
            else:
                self._dropped_routine += 1
            return
        queue.append(event)
        if len(self._routine) + len(self._priority) >= DRAIN_WAKE_THRESHOLD:
            self._wake.set()

    async def stop(self) -> None:
        """Cancel the runtime drain task; the caller performs any final drain."""
        task = self._drain_task
        self._drain_task = None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def drain(self) -> None:
        """Publish queued events, always exhausting the priority queue first.

        A transient publish failure stops the pass without popping, so the
        affected event is retried by the next pass; a deterministic rejection
        (e.g. oversized payload) is dropped so it can never block the queue.
        """
        if not self._started:
            await self.start()
        async with self._drain_lock:
            while self._priority or self._routine:
                queue = self._priority if self._priority else self._routine
                event = queue[0]
                outcome = await self._publish(event)
                if outcome == _PUBLISH_OUTCOME_TRANSIENT:
                    return
                queue.popleft()

    async def _drain_loop(self) -> None:
        """Runtime publisher: sweep on the interval, wake early on bursts."""
        while self._started:
            try:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=DRAIN_INTERVAL_SECONDS)
                self._wake.clear()
                failures_before = self._failed_dispatches
                await self.drain()
                if self._failed_dispatches > failures_before:
                    await asyncio.sleep(DRAIN_FAILURE_BACKOFF_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - the loop must survive anything
                logger.error("Operational event drain loop iteration failed: %s", error)
                await asyncio.sleep(DRAIN_FAILURE_BACKOFF_SECONDS)

    async def close(self) -> None:
        """Drain accepted work before releasing the Redis client."""
        await self.drain()
        await self._redis.aclose()

    def health(self) -> OperationalEventDispatchHealth:
        """Return an immutable snapshot suitable for health reporting."""
        return OperationalEventDispatchHealth(
            queued_routine=len(self._routine),
            queued_priority=len(self._priority),
            dropped_routine=self._dropped_routine,
            dropped_priority=self._dropped_priority,
            published=self._published,
            failed_dispatches=self._failed_dispatches,
            secondary_failures=self._secondary_failures,
        )

    def _queue_for(self, event: OperationalEvent) -> tuple[deque[OperationalEvent], bool]:
        is_priority = event.severity.value in {"error", "critical"}
        return (self._priority, True) if is_priority else (self._routine, False)

    async def _publish(self, event: OperationalEvent) -> str:
        try:
            payload = serialize_operational_event(event)
        except OperationalEventSerializationError as error:
            logger.warning(
                "Operational event dispatch rejected (type=%s): %s", event.event_type, error
            )
            self._failed_dispatches += 1
            return _PUBLISH_OUTCOME_DROPPED
        try:
            await self._redis.eval(
                _PUBLISH_SCRIPT,
                1,
                OPERATIONAL_EVENTS_STREAM,
                payload,
                OPERATIONAL_EVENTS_RETENTION_MS,
                OPERATIONAL_EVENTS_MAXLEN,
            )
        except (RedisError, ConnectionError, OSError) as error:
            logger.warning(
                "Operational event dispatch deferred (type=%s): %s", event.event_type, error
            )
            self._failed_dispatches += 1
            return _PUBLISH_OUTCOME_TRANSIENT
        self._published += 1
        if self._secondary_sink is None:
            return _PUBLISH_OUTCOME_OK
        try:
            await self._secondary_sink.persist(event)
        except (RedisError, ConnectionError, OSError):
            self._secondary_failures += 1
        return _PUBLISH_OUTCOME_OK


class OperationalEventStreamReader:
    """Read the global operational stream without consumer-group state."""

    def __init__(
        self,
        redis: OperationalRedis,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    ) -> None:
        self._redis = redis
        self._sleep = sleep
        self._malformed_entries = 0
        self._read_failures = 0

    async def read(self, *, after_id: str = "$") -> tuple[OperationalStreamEntry, ...]:
        """Read one bounded batch of events after a caller-owned stream identifier."""
        try:
            result = await self._redis.xread(
                {OPERATIONAL_EVENTS_STREAM: after_id},
                count=READ_BATCH_SIZE,
                block=READ_BLOCK_MILLISECONDS,
            )
        except (RedisError, ConnectionError, OSError):
            self._read_failures += 1
            await self._sleep(READ_FAILURE_BACKOFF_SECONDS)
            return ()
        return tuple(entry for stream in result for entry in self._entries(stream[1]))

    async def close(self) -> None:
        """Release the Redis client used for stream reads."""
        await self._redis.aclose()

    def health(self) -> OperationalEventStreamHealth:
        """Return an immutable snapshot suitable for health reporting."""
        return OperationalEventStreamHealth(
            malformed_entries=self._malformed_entries,
            read_failures=self._read_failures,
        )

    def _entries(self, messages: Sequence[StreamMessage]) -> tuple[OperationalStreamEntry, ...]:
        entries: list[OperationalStreamEntry] = []
        for message_id, fields in messages:
            payload = fields.get(EVENT_FIELD, fields.get(EVENT_FIELD.encode()))
            if payload is None:
                self._malformed_entries += 1
                continue
            try:
                event = OperationalEvent.model_validate_json(payload)
                entry_id = _as_text(message_id)
            except (UnicodeDecodeError, ValidationError):
                self._malformed_entries += 1
                continue
            entries.append(OperationalStreamEntry(id=entry_id, event=event))
        return tuple(entries)


def _as_text(value: StreamField) -> str:
    return value.decode() if isinstance(value, bytes) else value


OperationalStreamReader = OperationalEventStreamReader

__all__ = [
    "OperationalEventDispatchHealth",
    "OperationalEventDispatcher",
    "OperationalEventStreamHealth",
    "OperationalEventStreamReader",
    "OperationalStreamEntry",
    "OperationalStreamReader",
]
