"""Non-blocking, bounded persistence for exact photoperiod observations."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

import asyncpg

from app.schemas.monitoring import FlushHealth
from app.schemas.monitoring_models import (
    Phase,
    PhotoperiodLoggerHealthProvider,
    PhotoperiodObservationSink,
    RuntimeSnapshotVersion,
)
from shared.infra_logging import get_logger

logger = get_logger(__name__)

QUEUE_CAPACITY: Final = 256
FLUSH_BATCH_SIZE: Final = 64
FLUSH_INTERVAL_SECONDS: Final = 0.1
HEARTBEAT_INTERVAL: Final = timedelta(seconds=60)
BACKOFF_INTERVAL: Final = timedelta(seconds=5)
SOURCE: Final = "photoperiod"


@dataclass(frozen=True, slots=True)
class PhotoperiodObservation:
    """One exact room phase resolved by the control tick."""

    observed_at: datetime
    location: str
    cluster: str
    phase: Phase
    mode_id: int | None
    submode_id: int | None
    runtime_snapshot_version: RuntimeSnapshotVersion


@dataclass(frozen=True, slots=True)
class PhotoperiodLoggerHealth:
    """Route-facing queue and persistence state."""

    dropped_rows: int
    oldest_pending_at: datetime | None
    last_success_at: datetime | None
    healthy: bool


class PhotoperiodHistoryStore(Protocol):
    """Append-only persistence seam, implemented by the database adapter or a fake."""

    async def append(self, rows: tuple[PhotoperiodObservation, ...]) -> None:
        """Persist one bounded batch."""
        ...


class DatabasePhotoperiodHistoryStore:
    """Append observations through the initialized automation database pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, rows: tuple[PhotoperiodObservation, ...]) -> None:
        """Insert an already-bounded observation batch in one database round trip."""
        if not rows:
            return
        async with self._pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO monitoring_room_photoperiod (
                    observed_at, location, cluster, phase, mode_id, submode_id,
                    runtime_snapshot_version, source
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                [
                    (
                        row.observed_at,
                        row.location,
                        row.cluster,
                        row.phase.value,
                        row.mode_id,
                        row.submode_id,
                        int(row.runtime_snapshot_version),
                        SOURCE,
                    )
                    for row in rows
                ],
            )


class PhotoperiodHistoryLogger(PhotoperiodObservationSink, PhotoperiodLoggerHealthProvider):
    """Own the bounded queue because control ticks must never wait on history I/O."""

    def __init__(
        self,
        store: PhotoperiodHistoryStore,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
    ) -> None:
        self._store = store
        self._now = now
        self._queue: asyncio.Queue[PhotoperiodObservation] = asyncio.Queue(maxsize=QUEUE_CAPACITY)
        self._pending_timestamps: deque[datetime] = deque()
        self._last_enqueued_at: dict[tuple[str, str], datetime] = {}
        self._dropped_rows = 0
        self._last_success_at: datetime | None = None
        self._next_retry_at: datetime | None = None
        self._failed_flushes = 0
        self._worker: asyncio.Task[None] | None = None
        self._running = False

    @property
    def pending_count(self) -> int:
        """Return the bounded number of observations awaiting persistence."""
        return self._queue.qsize()

    @property
    def failed_flushes(self) -> int:
        """Return failed persistence attempts after throttling."""
        return self._failed_flushes

    def enqueue_final_phase(
        self, observation: PhotoperiodObservation, *, force: bool = False
    ) -> None:
        """Put a transition or 60-second heartbeat into the fixed queue without I/O."""
        room_key = (observation.location, observation.cluster)
        last_enqueued_at = self._last_enqueued_at.get(room_key)
        if (
            not force
            and last_enqueued_at is not None
            and observation.observed_at - last_enqueued_at < HEARTBEAT_INTERVAL
        ):
            return
        try:
            self._queue.put_nowait(observation)
        except asyncio.QueueFull:
            self._dropped_rows += 1
            return
        self._pending_timestamps.append(observation.observed_at)
        self._last_enqueued_at[room_key] = observation.observed_at

    async def start(self) -> None:
        """Start the independent flush worker before control tasks begin."""
        if self._running:
            return
        self._running = True
        self._worker = asyncio.create_task(self._run(), name="photoperiod-history-logger")

    async def stop(self) -> None:
        """Stop the worker after one bounded final flush."""
        self._running = False
        worker = self._worker
        if worker is not None:
            await worker
        await self.flush_once()
        self._worker = None

    async def flush_once(self) -> None:
        """Flush no more than one 64-row batch, respecting failure backoff."""
        now = self._now()
        if self._next_retry_at is not None and now < self._next_retry_at:
            return
        rows = self._take_batch()
        if not rows:
            return
        try:
            await self._store.append(rows)
        except (asyncpg.PostgresError, ConnectionError, OSError, RuntimeError) as error:
            self._dropped_rows += len(rows)
            self._failed_flushes += 1
            self._next_retry_at = now + BACKOFF_INTERVAL
            logger.warning("Photoperiod history flush unavailable; retry is throttled: %s", error)
            return
        self._last_success_at = now
        self._next_retry_at = None

    def flush_health(self) -> tuple[FlushHealth, ...]:
        """Return the Todo 12-compatible immutable flush status."""
        return (
            FlushHealth(
                source=SOURCE,
                dropped_rows=self._dropped_rows,
                last_flushed_at=self._last_success_at,
                healthy=self._next_retry_at is None,
            ),
        )

    def health_metadata(self) -> PhotoperiodLoggerHealth:
        """Expose drop, oldest-pending, and last-success metadata without mutation."""
        oldest_pending_at = self._pending_timestamps[0] if self._pending_timestamps else None
        return PhotoperiodLoggerHealth(
            dropped_rows=self._dropped_rows,
            oldest_pending_at=oldest_pending_at,
            last_success_at=self._last_success_at,
            healthy=self._next_retry_at is None,
        )

    def _take_batch(self) -> tuple[PhotoperiodObservation, ...]:
        rows: deque[PhotoperiodObservation] = deque()
        while len(rows) < FLUSH_BATCH_SIZE:
            try:
                rows.append(self._queue.get_nowait())
                _ = self._pending_timestamps.popleft()
            except asyncio.QueueEmpty:
                break
        return tuple(rows)

    async def _run(self) -> None:
        while self._running:
            await self.flush_once()
            await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
