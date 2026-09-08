"""Bounded, non-blocking publication and reading for operational events."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
from shared.redis_keys import (
    OPERATIONAL_EVENTS_MAXLEN,
    OPERATIONAL_EVENTS_RETENTION_MS,
    OPERATIONAL_EVENTS_STREAM,
)

ROUTINE_QUEUE_CAPACITY: Final[int] = 1_792
PRIORITY_QUEUE_CAPACITY: Final[int] = 256
READ_BATCH_SIZE: Final[int] = 100
READ_BLOCK_MILLISECONDS: Final[int] = 1_000
READ_FAILURE_BACKOFF_SECONDS: Final[float] = 1.0
EVENT_FIELD: Final[str] = "event"

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
    """Owns bounded queues so operational producers never await Redis."""

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

    async def start(self) -> None:
        """Mark this lifecycle component ready to drain producer queues."""
        self._started = True

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

    async def drain(self) -> None:
        """Publish all queued events, always exhausting the priority queue first."""
        if not self._started:
            await self.start()
        while self._priority or self._routine:
            queue = self._priority if self._priority else self._routine
            await self._publish(queue[0])
            queue.popleft()

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

    async def _publish(self, event: OperationalEvent) -> None:
        try:
            payload = serialize_operational_event(event)
            await self._redis.eval(
                _PUBLISH_SCRIPT,
                1,
                OPERATIONAL_EVENTS_STREAM,
                payload,
                OPERATIONAL_EVENTS_RETENTION_MS,
                OPERATIONAL_EVENTS_MAXLEN,
            )
        except (OperationalEventSerializationError, RedisError, ConnectionError, OSError):
            self._failed_dispatches += 1
            return
        self._published += 1
        if self._secondary_sink is None:
            return
        try:
            await self._secondary_sink.persist(event)
        except (RedisError, ConnectionError, OSError):
            self._secondary_failures += 1


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
