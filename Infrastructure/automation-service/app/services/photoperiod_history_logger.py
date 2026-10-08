"""Non-blocking, bounded persistence for photoperiod transitions and recorder coverage.

Phase history is changes-only: one initial baseline plus real SUN/MOON transitions.
Sparse availability boundaries are stored separately so an unchanged phase can be
distinguished from a recorder outage without heartbeat rows in the phase-change table.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import time
from typing import Final, Literal, Protocol, TypeAlias

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
BACKOFF_INTERVAL: Final = timedelta(seconds=5)
SHUTDOWN_DEADLINE_SECONDS: Final = 5.0
PHASE_SOURCE: Final = "photoperiod_transition"
SOURCE: Final = "photoperiod"
IN_FLUSH: Final = 0.05
FALLBACK_VERSION: Final = RuntimeSnapshotVersion(0)

CoverageState: TypeAlias = Literal["available", "unavailable"]
CoverageReason: TypeAlias = Literal[
    "initial",
    "started",
    "stopped",
    "control_failure",
    "recording_gap",
    "recovered",
    "unclean_restart",
]

_FLUSH_FAILED_EXC: Final = (asyncpg.PostgresError, ConnectionError, OSError, RuntimeError)

_PHASE_INSERT_SQL: Final = """
INSERT INTO monitoring_room_photoperiod (
    observed_at, location, cluster, phase, mode_id, submode_id,
    runtime_snapshot_version, source
)
SELECT $1, $2, $3, $4, $5, $6, $7, $8
WHERE NOT EXISTS (
    SELECT 1
    FROM monitoring_room_photoperiod AS prior
    WHERE prior.location = $2
      AND prior.cluster = $3
      AND prior.source = $8
      AND prior.observed_at = $1
      AND prior.phase = $4
)
  AND (
    SELECT latest.phase
    FROM monitoring_room_photoperiod AS latest
    WHERE latest.location = $2
      AND latest.cluster = $3
      AND latest.source = $8
    ORDER BY latest.observed_at DESC, latest.id DESC
    LIMIT 1
  ) IS DISTINCT FROM $4
"""

_COVERAGE_INSERT_SQL: Final = """
INSERT INTO monitoring_photoperiod_coverage (
    observed_at, location, cluster, state, reason, runtime_snapshot_version
)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (location, cluster, observed_at, state) DO NOTHING
"""

_PHASE_STATE_SQL: Final = """
SELECT DISTINCT ON (location, cluster)
    location, cluster, phase, observed_at, runtime_snapshot_version
FROM monitoring_room_photoperiod
WHERE source = 'photoperiod_transition'
ORDER BY location, cluster, observed_at DESC, id DESC
"""

_COVERAGE_STATE_SQL: Final = """
SELECT DISTINCT ON (location, cluster)
    location, cluster, state, observed_at, runtime_snapshot_version
FROM monitoring_photoperiod_coverage
ORDER BY location, cluster, observed_at DESC, id DESC
"""


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
class PhotoperiodCoverageObservation:
    """One sparse recorder-availability boundary; never a periodic sample."""

    observed_at: datetime
    location: str
    cluster: str
    state: CoverageState
    reason: CoverageReason
    runtime_snapshot_version: RuntimeSnapshotVersion


@dataclass(frozen=True, slots=True)
class PhotoperiodRecorderState:
    """Latest persisted new-source phase and coverage boundary facts for one room."""

    location: str
    cluster: str
    phase: Phase | None
    phase_observed_at: datetime | None
    coverage_state: CoverageState | None
    coverage_observed_at: datetime | None
    runtime_snapshot_version: RuntimeSnapshotVersion


PhotoperiodHistoryRow: TypeAlias = PhotoperiodObservation | PhotoperiodCoverageObservation


@dataclass(frozen=True, slots=True)
class _DeferredIntent:
    """One accepted buffered fact queued raw until its whole packet materializes."""

    row: PhotoperiodHistoryRow


PacketEntry: TypeAlias = PhotoperiodHistoryRow | _DeferredIntent


@dataclass(frozen=True, slots=True)
class PhotoperiodLoggerHealth:
    """Route-facing queue and persistence state."""

    dropped_rows: int
    oldest_pending_at: datetime | None
    last_success_at: datetime | None
    healthy: bool


class PhotoperiodHistoryStore(Protocol):
    """Append-only persistence seam, implemented by the database adapter or a fake."""

    async def append(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> None:
        """Persist one bounded batch in one database transaction."""
        ...

    async def read_state(self) -> tuple[PhotoperiodRecorderState, ...]:
        """Read the latest persisted transition and coverage boundary per room."""
        ...


class DatabasePhotoperiodHistoryStore:
    """Append rows through the initialized automation database pool, idempotently."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> None:
        """Insert an already-bounded batch in one transaction and never duplicate facts.

        Phase rows are conditional on the latest persisted ``photoperiod_transition``
        phase being absent or different, and on the same room/timestamp/phase row not
        already existing, so restart seeding and retry-after-ambiguous-commit settle
        without duplicates. Coverage rows use the sparse unique key with
        ``ON CONFLICT DO NOTHING``.
        """
        if not rows:
            return
        async with self._pool.acquire() as connection, connection.transaction():
            for row in rows:
                if isinstance(row, PhotoperiodObservation):
                    await connection.execute(
                        _PHASE_INSERT_SQL,
                        row.observed_at,
                        row.location,
                        row.cluster,
                        row.phase.value,
                        row.mode_id,
                        row.submode_id,
                        int(row.runtime_snapshot_version),
                        PHASE_SOURCE,
                    )
                    continue
                await connection.execute(
                    _COVERAGE_INSERT_SQL,
                    row.observed_at,
                    row.location,
                    row.cluster,
                    row.state,
                    row.reason,
                    int(row.runtime_snapshot_version),
                )

    async def read_state(self) -> tuple[PhotoperiodRecorderState, ...]:
        """Read both latest-row queries inside one repeatable-read transaction.

        Old ``source='photoperiod'`` rows are never read here: they were samples from
        the incomplete Sleep/Drying-only producer and must not seed new transition
        continuity.
        """
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            phase_rows = await connection.fetch(_PHASE_STATE_SQL)
            coverage_rows = await connection.fetch(_COVERAGE_STATE_SQL)
        phases = {(row["location"], row["cluster"]): row for row in phase_rows}
        coverage = {(row["location"], row["cluster"]): row for row in coverage_rows}
        states: list[PhotoperiodRecorderState] = []
        for room_key in sorted(set(phases) | set(coverage)):
            phase_row = phases.get(room_key)
            coverage_row = coverage.get(room_key)
            version_row: asyncpg.Record
            if phase_row is not None and coverage_row is not None:
                version_row = (
                    phase_row
                    if phase_row["observed_at"] >= coverage_row["observed_at"]
                    else coverage_row
                )
            else:
                version_row = phase_row if phase_row is not None else coverage_row
            if version_row is None:  # pragma: no cover - both maps are populated
                continue
            states.append(
                PhotoperiodRecorderState(
                    location=room_key[0],
                    cluster=room_key[1],
                    phase=Phase(str(phase_row["phase"])) if phase_row is not None else None,
                    phase_observed_at=phase_row["observed_at"] if phase_row is not None else None,
                    coverage_state=(
                        coverage_row["state"] if coverage_row is not None else None
                    ),
                    coverage_observed_at=(
                        coverage_row["observed_at"] if coverage_row is not None else None
                    ),
                    runtime_snapshot_version=RuntimeSnapshotVersion(
                        int(version_row["runtime_snapshot_version"])
                    ),
                )
            )
        return tuple(states)


@dataclass
class _RoomState:
    """Mutable per-room bookkeeping owned by the single ordered writer."""

    accepted_phase: Phase | None = None
    last_phase_at: datetime | None = None
    seed_version: RuntimeSnapshotVersion | None = None
    coverage_state: CoverageState | None = None
    coverage_observed_at: datetime | None = None
    version: RuntimeSnapshotVersion | None = None
    latest_phase: Phase | None = None
    latest_phase_at: datetime | None = None
    latest_mode_id: int | None = None
    latest_submode_id: int | None = None
    unclosed_prior_run: bool = False
    gap_started_at: datetime | None = None
    gap_pending_version: RuntimeSnapshotVersion | None = None
    buffered_phase: Phase | None = None
    buffered_unavailable: bool = False


class PhotoperiodHistoryLogger(PhotoperiodObservationSink, PhotoperiodLoggerHealthProvider):
    """Own the bounded queue because control ticks must never wait on history I/O.

    One ordered writer peeks at most one batch and removes entries only after a
    successful transaction, so failed appends retain their batch. Producers append
    only when the row-count bound has room, and capacity rejection remembers an
    explicit gap that a later offer closes atomically instead of guessing a phase.
    """

    def __init__(
        self,
        store: PhotoperiodHistoryStore,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
        shutdown_timeout: float = SHUTDOWN_DEADLINE_SECONDS,
    ) -> None:
        self._store = store
        self._now = now
        self._shutdown_timeout = shutdown_timeout
        # One ordered deque holds every queued fact: materialized packets and
        # raw deferred intents alike, counted in rows with a single 256-row
        # capacity bound that includes in-flight entries.
        self._pending: deque[tuple[PacketEntry, ...]] = deque()
        self._pending_row_count = 0
        self._flush_lock = asyncio.Lock()
        self._rooms: dict[tuple[str, str], _RoomState] = {}
        self._dropped_rows = 0
        self._last_success_at: datetime | None = None
        self._next_retry_at: datetime | None = None
        self._failed_flushes = 0
        self._worker: asyncio.Task[None] | None = None
        self._running = False
        self._started = False
        self._seeded = False
        self._closed = False
        self._shutdown_incomplete = False
        self._closure_unsent = False

    @property
    def pending_count(self) -> int:
        """Return the bounded number of queued rows, in-flight rows included."""
        return self._pending_row_count

    @property
    def failed_flushes(self) -> int:
        """Return failed persistence attempts that retained their batch."""
        return self._failed_flushes

    def enqueue_final_phase(self, observation: PhotoperiodObservation) -> None:
        """Queue a resolved phase change without I/O; unchanged phases write nothing."""
        if observation.phase == Phase.UNKNOWN:
            # An unknown phase is an unavailable-coverage notification, never a row.
            self._queue_unavailable(
                (observation.location, observation.cluster),
                observation.observed_at,
                "control_failure",
                observation.runtime_snapshot_version,
            )
            return
        self._offer(observation)

    def mark_unavailable(
        self,
        *,
        location: str,
        cluster: str,
        observed_at: datetime,
        runtime_snapshot_version: RuntimeSnapshotVersion,
    ) -> None:
        """Close room recording once after a control failure or snapshot removal."""
        self._queue_unavailable(
            (location, cluster),
            observed_at,
            "control_failure",
            runtime_snapshot_version,
        )

    async def start(self) -> None:
        """Start the independent flush worker and return immediately."""
        if self._worker is not None or self._closed:
            return
        self._running = True
        self._started = True
        self._worker = asyncio.create_task(self._run(), name="photoperiod-history-logger")

    async def stop(self) -> None:
        """Close room coverage and drain bounded by the total shutdown deadline."""
        if self._closed:
            return
        self._closed = True
        self._running = False
        deadline = time.monotonic() + self._shutdown_timeout
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                await asyncio.wait_for(worker, timeout=max(0.0, deadline - time.monotonic()))
            except TimeoutError:
                logger.warning("Photoperiod history worker did not stop before deadline")
                worker.cancel()
                try:
                    await asyncio.wait_for(
                        worker, timeout=max(0.0, deadline - time.monotonic())
                    )
                except TimeoutError:
                    pass
                except asyncio.CancelledError:  # pragma: no cover - timing dependent
                    pass
        await self._enqueue_shutdown_boundaries(deadline)
        await self._drain(deadline)
        self._shutdown_incomplete = self._pending_row_count > 0
        if self._shutdown_incomplete:
            logger.warning(
                "Photoperiod history shutdown left %d undrained row(s); the next startup "
                "will invalidate the final available interval",
                self._pending_row_count,
            )
        if self._closure_unsent:
            logger.warning(
                "Photoperiod history shutdown could not close still-available room "
                "coverage; the next startup will invalidate the final available interval"
            )

    async def flush_once(self) -> None:
        """Flush whole packets under one lock; nothing persists before seeding succeeds."""
        if not self._seeded:
            return
        async with self._flush_lock:
            # The blocking gate is re-read after lock acquisition because waiting
            # callers may have passed it while another flush was in flight.
            now = self._now()
            if self._next_retry_at is not None and now < self._next_retry_at:
                return
            packets = self._take_batch_packets()
            if not packets:
                return
            rows = tuple(
                entry
                for packet in packets
                for entry in packet
                if not isinstance(entry, _DeferredIntent)
            )
            try:
                await self._store.append(rows)
            except _FLUSH_FAILED_EXC as error:
                # In-flight packets stay at the queue head until the append
                # succeeds, so no observation is rejected and dropped_rows
                # cannot grow from a persistence outage.
                self._failed_flushes += 1
                self._next_retry_at = now + BACKOFF_INTERVAL
                logger.warning(
                    "Photoperiod history flush unavailable; retry is throttled: %s", error
                )
                return
            self._last_success_at = now
            self._next_retry_at = None
            for packet in packets:
                self._pending.popleft()
                self._pending_row_count -= len(packet)

    def _take_batch_packets(self) -> tuple[tuple[PacketEntry, ...], ...]:
        """Peek whole packets up to the batch row bound; never split a packet.

        Leading deferred intents materialize first; a take stops at any intent
        that still cannot materialize so raw facts are never persisted without
        their derived coverage/phase packet.
        """
        self._materialize_leading_intents()
        packets: list[tuple[PacketEntry, ...]] = []
        row_count = 0
        for packet in self._pending:
            if len(packet) == 1 and isinstance(packet[0], _DeferredIntent):
                break
            if row_count + len(packet) > FLUSH_BATCH_SIZE:
                break
            packets.append(packet)
            row_count += len(packet)
            if row_count >= FLUSH_BATCH_SIZE:
                break
        return tuple(packets)

    def flush_health(self) -> tuple[FlushHealth, ...]:
        """Return the route-facing immutable flush status."""
        return (
            FlushHealth(
                source=SOURCE,
                dropped_rows=self._dropped_rows,
                last_flushed_at=self._last_success_at,
                healthy=(
                    self._next_retry_at is None
                    and not self._shutdown_incomplete
                    and not self._closure_unsent
                ),
            ),
        )

    def health_metadata(self) -> PhotoperiodLoggerHealth:
        """Expose drop, oldest-pending, and last-success metadata without mutation."""
        oldest_pending_at: datetime | None = None
        for packet in self._pending:
            if packet:
                entry = packet[0]
                row = entry.row if isinstance(entry, _DeferredIntent) else entry
                oldest_pending_at = row.observed_at
                break
        return PhotoperiodLoggerHealth(
            dropped_rows=self._dropped_rows,
            oldest_pending_at=oldest_pending_at,
            last_success_at=self._last_success_at,
            healthy=(
                self._next_retry_at is None
                and not self._shutdown_incomplete
                and not self._closure_unsent
            ),
        )

    async def _run(self) -> None:
        """Seed persisted continuity before the first append, then flush in order."""
        try:
            while self._running and not self._seeded:
                try:
                    states = await self._store.read_state()
                except Exception as error:  # noqa: BLE001 - seeding must never block hardware
                    logger.warning(
                        "Photoperiod history state read unavailable; appends stay gated "
                        "and control keeps running: %s",
                        error,
                    )
                    await asyncio.sleep(BACKOFF_INTERVAL.total_seconds())
                    continue
                async with self._flush_lock:
                    # Seeding and replay own the queue exclusively so an external
                    # flush can never interleave between buffer drain and repacket.
                    self._seed(states)
                break
            while self._running:
                await self.flush_once()
                await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

    def _seed(self, states: tuple[PhotoperiodRecorderState, ...]) -> None:
        """Adopt persisted boundaries per room before any first append."""
        for record in states:
            state = self._room_state((record.location, record.cluster))
            if record.phase is not None:
                state.accepted_phase = record.phase
                state.last_phase_at = record.phase_observed_at
            if record.coverage_state is not None:
                state.coverage_state = record.coverage_state
                state.coverage_observed_at = record.coverage_observed_at
                state.unclosed_prior_run = record.coverage_state == "available"
            state.seed_version = record.runtime_snapshot_version
            state.version = record.runtime_snapshot_version
        buffered_sequence = [
            (index, entry if not isinstance(entry, _DeferredIntent) else entry.row)
            for index, packet in enumerate(tuple(self._pending))
            for entry in packet
        ]
        self._pending.clear()
        self._pending_row_count = 0
        self._seeded = True
        buffered_sequence.sort(key=lambda item: (item[1].observed_at, item[0]))
        for _, row in buffered_sequence:
            if isinstance(row, PhotoperiodObservation):
                self._offer(row, replay=True)
            else:
                self._process_unavailable(row, replay=True)
        for state in self._rooms.values():
            state.buffered_phase = None
            state.buffered_unavailable = False
        self._closure_unsent = False

    def _materialize_leading_intents(self) -> None:
        """Rebuild leading deferred raw intents into whole packets while they fit.

        Deferred intents sit in the same ordered deque; materializing one pops it
        from the head and re-admits its derived packet at the head so the queue
        keeps emitting facts in their buffered time order without ever exceeding
        the single row-count bound.
        """
        while self._pending:
            entry = self._pending[0]
            if len(entry) != 1 or not isinstance(entry[0], _DeferredIntent):
                return
            row = entry[0].row
            self._pending.popleft()
            self._pending_row_count -= 1
            packet_rows, state, materialize_gap = self._build_packet_for(row)
            if packet_rows and self._pending_row_count + len(packet_rows) > QUEUE_CAPACITY:
                # Not enough room yet: the raw intent stays queued for the next
                # sweep once a flush frees space.
                self._pending.appendleft((_DeferredIntent(row),))
                self._pending_row_count += 1
                return
            if packet_rows:
                self._pending.appendleft(tuple(packet_rows))
                self._pending_row_count += len(packet_rows)
            self._advance_packet_for(row, state, materialize_gap)

    def _build_packet_for(
        self, row: PhotoperiodHistoryRow
    ) -> tuple[tuple[PhotoperiodHistoryRow, ...], _RoomState, bool]:
        """Build the whole derived packet for one accepted buffered row."""
        state = self._room_state((row.location, row.cluster))
        if isinstance(row, PhotoperiodObservation):
            materialize_gap = (
                state.gap_started_at is not None
                and state.coverage_state == "available"
                and row.observed_at >= state.gap_started_at
            )
            return self._build_transition_packet(row, state, materialize_gap), state, materialize_gap
        if state.coverage_state == "unavailable":
            return (), state, False
        return (row,), state, False

    def _advance_packet_for(
        self, row: PhotoperiodHistoryRow, state: _RoomState, materialize_gap: bool
    ) -> None:
        """Advance room bookkeeping for an admitted packet."""
        if isinstance(row, PhotoperiodObservation):
            state.coverage_state = "available"
            state.coverage_observed_at = row.observed_at
            state.unclosed_prior_run = False
            # Only a packet that actually materialized the remembered gap may
            # clear it; earlier buffered observations must never erase a gap.
            if materialize_gap:
                state.gap_started_at = None
                state.gap_pending_version = None
            if state.accepted_phase is None or row.phase != state.accepted_phase:
                state.accepted_phase = row.phase
                state.last_phase_at = row.observed_at
            return
        state.coverage_state = "unavailable"
        state.coverage_observed_at = row.observed_at
        state.unclosed_prior_run = False

    def _offer(self, observation: PhotoperiodObservation, *, replay: bool = False) -> None:
        """Buffer first/change observations until seeding, then apply packet rules."""
        state = self._room_state((observation.location, observation.cluster))
        # The latest observed phase/metadata is a fact of the tick stream: it is
        # kept separately from the last accepted phase and must advance even when
        # capacity rejects the packet for this observation.
        state.version = observation.runtime_snapshot_version
        state.latest_phase = observation.phase
        state.latest_phase_at = observation.observed_at
        state.latest_mode_id = observation.mode_id
        state.latest_submode_id = observation.submode_id
        if not self._seeded:
            if state.buffered_phase != observation.phase and self._retain_raw(observation, state):
                state.buffered_phase = observation.phase
                state.buffered_unavailable = False
            return
        rows = self._build_packet_for(observation)[0]
        if not rows:
            return
        if self._emit(rows):
            self._advance_packet_for(
                observation, state, self._gap_materializes(observation, state)
            )
            return
        if replay and self._defer_intent(observation):
            # Buffered intents from the seed replay must never be dropped by the
            # packet expansion; the raw fact defers in the same deque and its
            # whole packet materializes when a later flush frees space.
            return
        self._reject_with_gap(
            state, observation.observed_at, observation.runtime_snapshot_version
        )

    def _gap_materializes(self, row: PhotoperiodHistoryRow, state: _RoomState) -> bool:
        """Return whether this row should close the room's remembered gap."""
        return (
            state.gap_started_at is not None
            and state.coverage_state == "available"
            and row.observed_at >= state.gap_started_at
        )

    def _build_transition_packet(
        self,
        observation: PhotoperiodObservation,
        state: _RoomState,
        materialize_gap: bool,
    ) -> tuple[PhotoperiodHistoryRow, ...]:
        """Build the whole coverage+phase packet for one resolved offer.

        The packet is all-or-nothing: either every restart/recovery row and the
        required phase change fit the capacity bound, or none is queued and the gap
        stays remembered for the next offer that finds room. Recovery packets keep
        the order [unavailable boundary, required phase change, available coverage].
        """
        rows: list[PhotoperiodHistoryRow] = []
        phase_changed = state.accepted_phase is None or observation.phase != state.accepted_phase

        def _coverage_row(state_value: CoverageState, reason: CoverageReason) -> PhotoperiodCoverageObservation:
            return PhotoperiodCoverageObservation(
                observed_at=observation.observed_at,
                location=observation.location,
                cluster=observation.cluster,
                state=state_value,
                reason=reason,
                runtime_snapshot_version=observation.runtime_snapshot_version,
            )

        if state.coverage_state == "available":
            if state.unclosed_prior_run:
                boundary = self._restart_boundary(state)
                rows.append(
                    PhotoperiodCoverageObservation(
                        observed_at=boundary,
                        location=observation.location,
                        cluster=observation.cluster,
                        state="unavailable",
                        reason="unclean_restart",
                        runtime_snapshot_version=state.seed_version or FALLBACK_VERSION,
                    )
                )
            if materialize_gap:
                rows.append(
                    PhotoperiodCoverageObservation(
                        observed_at=state.gap_started_at,
                        location=observation.location,
                        cluster=observation.cluster,
                        state="unavailable",
                        reason="recording_gap",
                        runtime_snapshot_version=(
                            state.gap_pending_version or FALLBACK_VERSION
                        ),
                    )
                )
                if phase_changed:
                    rows.append(observation)
                rows.append(_coverage_row("available", "recovered"))
            elif state.unclosed_prior_run:
                rows.append(_coverage_row("available", "started"))
                if phase_changed:
                    rows.append(observation)
            elif phase_changed:
                rows.append(observation)
        elif state.coverage_state == "unavailable":
            rows.append(_coverage_row("available", "started"))
            if phase_changed:
                rows.append(observation)
        else:
            rows.append(_coverage_row("available", "initial"))
            if phase_changed:
                rows.append(observation)
        return tuple(rows)

    def _restart_boundary(self, state: _RoomState) -> datetime:
        """Return the later of the last persisted phase and coverage timestamps."""
        boundary = state.coverage_observed_at
        if state.last_phase_at is not None and (boundary is None or state.last_phase_at > boundary):
            boundary = state.last_phase_at
        assert boundary is not None
        return boundary

    def _process_unavailable(
        self, row: PhotoperiodCoverageObservation, *, replay: bool = False
    ) -> None:
        """Record one availability closure; repeated closures stay silent."""
        state = self._room_state((row.location, row.cluster))
        state.version = row.runtime_snapshot_version
        if not self._seeded:
            if not state.buffered_unavailable and self._retain_raw(row, state):
                state.buffered_unavailable = True
                state.buffered_phase = None
            return
        packet_rows, state, _ = self._build_packet_for(row)
        if not packet_rows:
            return
        if not self._emit(packet_rows):
            if replay and self._defer_intent(row):
                return
            self._reject_with_gap(state, row.observed_at, row.runtime_snapshot_version)
            return
        self._advance_packet_for(row, state, False)

    def _queue_unavailable(
        self,
        room_key: tuple[str, str],
        observed_at: datetime,
        reason: CoverageReason,
        runtime_snapshot_version: RuntimeSnapshotVersion,
    ) -> None:
        self._process_unavailable(
            PhotoperiodCoverageObservation(
                observed_at=observed_at,
                location=room_key[0],
                cluster=room_key[1],
                state="unavailable",
                reason=reason,
                runtime_snapshot_version=runtime_snapshot_version,
            )
        )

    async def _enqueue_shutdown_boundaries(self, deadline: float) -> None:
        """Emit one unavailable boundary for each still-available observed room.

        Every await is bounded by the remaining total shutdown deadline; when the
        queue is full, pending phase packets are flushed first so the closure
        packets can be queued entirely. An omitted closure keeps failed health.
        """
        for room_key in sorted(self._rooms):
            state = self._rooms[room_key]
            if state.coverage_state != "available" or state.latest_phase is None:
                continue
            rows = self._closure_rows(room_key, state)
            while (
                self._pending_row_count + len(rows) > QUEUE_CAPACITY
                and self._pending
                and time.monotonic() < deadline
            ):
                before = self._pending_row_count
                try:
                    await asyncio.wait_for(
                        self.flush_once(), timeout=max(0.0, deadline - time.monotonic())
                    )
                except TimeoutError:
                    break
                if self._pending_row_count >= before:
                    await asyncio.sleep(min(IN_FLUSH, max(0.0, deadline - time.monotonic())))
            if not self._emit(rows):
                # Nothing was rejected for counting; the omission keeps failed
                # health and the next startup's unclean-restart handling covers it.
                self._closure_unsent = True
                continue
            state.coverage_state = "unavailable"
            state.coverage_observed_at = rows[-1].observed_at
            state.unclosed_prior_run = False
            state.gap_started_at = None
            state.gap_pending_version = None

    def _closure_rows(
        self, room_key: tuple[str, str], state: _RoomState
    ) -> tuple[PhotoperiodHistoryRow, ...]:
        """Build the stop-time closure packet for one available room."""
        rows: list[PhotoperiodHistoryRow] = []
        if state.unclosed_prior_run:
            rows.append(
                PhotoperiodCoverageObservation(
                    observed_at=self._restart_boundary(state),
                    location=room_key[0],
                    cluster=room_key[1],
                    state="unavailable",
                    reason="unclean_restart",
                    runtime_snapshot_version=state.seed_version or FALLBACK_VERSION,
                )
            )
        if state.gap_started_at is not None:
            rows.append(
                PhotoperiodCoverageObservation(
                    observed_at=state.gap_started_at,
                    location=room_key[0],
                    cluster=room_key[1],
                    state="unavailable",
                    reason="recording_gap",
                    runtime_snapshot_version=state.gap_pending_version or FALLBACK_VERSION,
                )
            )
        rows.append(
            PhotoperiodCoverageObservation(
                observed_at=self._now(),
                location=room_key[0],
                cluster=room_key[1],
                state="unavailable",
                reason="stopped",
                runtime_snapshot_version=(
                    state.version or state.seed_version or FALLBACK_VERSION
                ),
            )
        )
        return tuple(rows)

    async def _drain(self, deadline: float) -> None:
        """Flush everything queued until empty or the deadline expires; all awaits bounded."""
        while self._pending_row_count and time.monotonic() < deadline:
            before = self._pending_row_count
            try:
                await asyncio.wait_for(
                    self.flush_once(), timeout=max(0.0, deadline - time.monotonic())
                )
            except TimeoutError:
                break
            if self._pending_row_count >= before:
                await asyncio.sleep(min(IN_FLUSH, max(0.0, deadline - time.monotonic())))

    def _room_state(self, room_key: tuple[str, str]) -> _RoomState:
        state = self._rooms.get(room_key)
        if state is None:
            state = _RoomState()
            self._rooms[room_key] = state
        return state

    def _emit(self, rows: tuple[PhotoperiodHistoryRow, ...]) -> bool:
        """Append one whole packet only when it fits the row-count bound."""
        if self._pending_row_count + len(rows) > QUEUE_CAPACITY:
            return False
        self._pending.append(tuple(rows))
        self._pending_row_count += len(rows)
        return True

    def _defer_intent(self, row: PhotoperiodHistoryRow) -> bool:
        """Re-queue an accepted buffered intent as one raw row; never drops facts.

        The intent stays in the same ordered deque and counts as one row against
        the single capacity bound; a later sweep materializes its whole packet.
        """
        if self._pending_row_count + 1 > QUEUE_CAPACITY:
            return False
        self._pending.append((_DeferredIntent(row),))
        self._pending_row_count += 1
        return True

    def _reject_with_gap(
        self,
        state: _RoomState,
        observed_at: datetime,
        runtime_snapshot_version: RuntimeSnapshotVersion,
    ) -> None:
        """Count one rejected observation and remember the gap without guessed phases."""
        self._dropped_rows += 1
        if state.gap_started_at is None or observed_at < state.gap_started_at:
            state.gap_started_at = observed_at
            state.gap_pending_version = runtime_snapshot_version

    @staticmethod
    def _intent_cost(state: _RoomState) -> int:
        """Conservative row reservation for one buffered pre-seed intent.

        The first known observation per room reserves the maximum startup packet
        cost (up to three rows: unclean boundary, started coverage, phase), and
        any intent that would restart coverage or close a gap reserves the same
        recovery packet cost; steady later changes reserve one row.
        """
        if state.buffered_phase is None and not state.buffered_unavailable:
            return 3
        if state.buffered_unavailable or state.gap_started_at is not None:
            return 3
        return 1

    def _retain_raw(self, row: PhotoperiodHistoryRow, state: _RoomState) -> bool:
        """Keep first/change intents pre-seeding with their reserved packet cost."""
        cost = self._intent_cost(state)
        if self._pending_row_count + cost > QUEUE_CAPACITY:
            self._reject_with_gap(state, row.observed_at, row.runtime_snapshot_version)
            return False
        self._pending.append((row,))
        self._pending_row_count += cost
        return True
