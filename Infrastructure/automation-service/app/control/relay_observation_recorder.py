"""Non-blocking capture and bounded append-only persistence of sampled relay facts."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, Protocol
from uuid import UUID, uuid4

import asyncpg

from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from shared.infra_logging import get_logger
from shared.retry import retry_async

logger = get_logger(__name__)

RelayObservationReason = Literal[
    "initial",
    "state_changed",
    "stale",
    "recovered",
    "assignment_changed",
    "recording_gap",
    "heartbeat",
]

HEARTBEAT_INTERVAL: Final = timedelta(seconds=30)
QUEUE_CAPACITY: Final = 256
FLUSH_BATCH_SIZE: Final = 64
RETRY_ATTEMPTS: Final = 3
_RETRYABLE_DATABASE_ERRORS: Final = (
    asyncpg.PostgresConnectionError,
    ConnectionError,
    OSError,
    TimeoutError,
)


@dataclass(frozen=True, slots=True)
class RelayObservation:
    """One immutable physical sample-time fact, with event-time assignment identity."""

    observed_at: datetime
    session_id: UUID
    channel: int | None
    observed_state: bool | None
    device_id: int | None
    device_name: str | None
    device_type: str | None
    location: str | None
    cluster: str | None
    registry_version: int
    reason: RelayObservationReason


class RelayObservationStore(Protocol):
    """Append a bounded batch of observations without mutating prior rows."""

    async def append(self, rows: tuple[RelayObservation, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class _RelayIdentity:
    device_id: int | None
    device_name: str | None
    device_type: str | None
    location: str | None
    cluster: str | None


_EMPTY_IDENTITY = _RelayIdentity(None, None, None, None, None)


class RelayObservationRecorder:
    """Capture physical samples synchronously and persist them on an owned worker."""

    def __init__(
        self,
        store: RelayObservationStore,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_id: UUID | None = None,
        queue_capacity: int = QUEUE_CAPACITY,
    ) -> None:
        if queue_capacity < 33:
            raise ValueError("queue_capacity must fit a gap, a full baseline, and heartbeat")
        self._store = store
        self._now = now
        self.session_id = session_id or uuid4()
        self._queue: asyncio.Queue[RelayObservation] = asyncio.Queue(maxsize=queue_capacity)
        self._snapshot: RuntimeDeviceSnapshot | None = None
        self._identities: tuple[_RelayIdentity, ...] | None = None
        self._latest_channels: tuple[bool, ...] | None = None
        self._last_offered_channels: tuple[bool, ...] | None = None
        self._has_sampled = False
        self._fresh = False
        self._stale_since: datetime | None = None
        self._needs_recovery = False
        self._recovery_since: datetime | None = None
        self._last_heartbeat_enqueued_at: datetime | None = None
        self._last_persisted_heartbeat_at: datetime | None = None
        self._worker: asyncio.Task[None] | None = None
        self._running = False
        self._accepting = True

    @property
    def pending_count(self) -> int:
        """Number of queued observations awaiting the persistence worker."""
        return self._queue.qsize()

    @property
    def coverage_incomplete(self) -> bool:
        """Whether a lost observation requires a persisted gap and fresh baseline."""
        return self._needs_recovery

    @property
    def last_persisted_heartbeat_at(self) -> datetime | None:
        """Most recent heartbeat confirmed by a successful database append."""
        return self._last_persisted_heartbeat_at

    def on_registry_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Consume the exact immutable snapshot supplied by registry subscription."""
        next_identities = self._identities_for_snapshot(snapshot)
        previous_identities = self._identities
        self._snapshot = snapshot
        self._identities = next_identities

        if previous_identities is None or self._needs_recovery or not self._accepting:
            return

        changed_channels = tuple(
            channel
            for channel, (old, new) in enumerate(
                zip(previous_identities, next_identities, strict=True)
            )
            if old != new
        )
        if not changed_channels:
            return

        observed_at = self._normalize_time(self._now())
        rows = tuple(
            self._channel_row(
                observed_at,
                channel,
                self._latest_channels[channel]
                if self._fresh and self._latest_channels is not None
                else None,
                "assignment_changed",
                next_identities[channel],
                snapshot.version,
            )
            for channel in changed_channels
        )
        self._offer_batch(rows)

    def observe_sample(
        self, channels: tuple[bool, ...] | None, observed_at: datetime
    ) -> None:
        """Offer a successful 16-channel sample or a failed-sample stale boundary."""
        if not self._accepting:
            return
        at = self._normalize_time(observed_at)
        if channels is None or len(channels) != 16:
            self._fresh = False
            if self._stale_since is None:
                self._stale_since = at
                if not self._needs_recovery:
                    rows = self._channel_rows(
                        at,
                        "stale",
                        (None,) * 16,
                    )
                    self._offer_batch(rows)
            return

        sampled_channels = tuple(bool(state) for state in channels)
        self._latest_channels = sampled_channels
        self._fresh = True

        if self._needs_recovery:
            self._offer_recovery(at, sampled_channels)
            self._stale_since = None
            return

        if self._stale_since is not None:
            rows = self._channel_rows(at, "recovered", sampled_channels)
        elif not self._has_sampled:
            rows = self._channel_rows(at, "initial", sampled_channels)
        else:
            previous = self._last_offered_channels
            if previous is None:
                rows = self._channel_rows(at, "initial", sampled_channels)
            else:
                rows = tuple(
                    self._channel_row(
                        at,
                        channel,
                        state,
                        "state_changed",
                        self._identity_at(channel),
                    )
                    for channel, (state, old_state) in enumerate(
                        zip(sampled_channels, previous, strict=True)
                    )
                    if state != old_state
                )

        heartbeat_due = self._heartbeat_due(at)
        if heartbeat_due:
            rows += (self._heartbeat_row(at),)

        accepted = self._offer_batch(rows)
        self._stale_since = None
        if accepted:
            self._last_offered_channels = sampled_channels
            self._has_sampled = True
            if heartbeat_due:
                self._last_heartbeat_enqueued_at = at

    async def start(self) -> None:
        """Start the single ordered writer before relay sampling begins."""
        if self._running:
            return
        self._accepting = True
        self._running = True
        self._worker = asyncio.create_task(self._run(), name="relay-observation-recorder")

    async def stop(self) -> None:
        """Stop accepting rows and drain the bounded queue before database shutdown."""
        self._accepting = False
        self._running = False
        worker = self._worker
        if worker is not None:
            await worker
        self._worker = None

    def _offer_recovery(self, observed_at: datetime, channels: tuple[bool, ...]) -> None:
        identities = self._identities or (_EMPTY_IDENTITY,) * 16
        version = self._registry_version
        marker_at = self._recovery_since or observed_at
        gap_rows = self._channel_rows(
            marker_at,
            "recording_gap",
            (None,) * 16,
            identities=(_EMPTY_IDENTITY,) * 16,
            registry_version=version,
        )
        baseline_rows = self._channel_rows(
            observed_at,
            "recovered",
            channels,
            identities=identities,
            registry_version=version,
        )
        rows = gap_rows + baseline_rows + (self._heartbeat_row(observed_at),)
        if not self._offer_batch(rows):
            return

        self._needs_recovery = False
        self._recovery_since = None
        self._last_offered_channels = channels
        self._has_sampled = True
        self._stale_since = None
        self._last_heartbeat_enqueued_at = observed_at

    def _offer_batch(self, rows: tuple[RelayObservation, ...]) -> bool:
        if not rows:
            return True
        if self._queue.qsize() + len(rows) > self._queue.maxsize:
            self._mark_recording_gap(rows[0].observed_at)
            return False
        for row in rows:
            self._queue.put_nowait(row)
        if any(row.reason == "heartbeat" for row in rows):
            self._last_heartbeat_enqueued_at = next(
                row.observed_at for row in reversed(rows) if row.reason == "heartbeat"
            )
        return True

    def _mark_recording_gap(self, observed_at: datetime) -> None:
        at = self._normalize_time(observed_at)
        if not self._needs_recovery or self._recovery_since is None or at < self._recovery_since:
            self._recovery_since = at
        self._needs_recovery = True
        self._last_heartbeat_enqueued_at = None

    async def _run(self) -> None:
        while self._running or not self._queue.empty():
            try:
                first = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except TimeoutError:
                continue

            batch = [first]
            while len(batch) < FLUSH_BATCH_SIZE:
                try:
                    batch.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            try:
                await retry_async(
                    lambda: self._store.append(tuple(batch)),
                    retry_on=_RETRYABLE_DATABASE_ERRORS,
                    max_attempts=RETRY_ATTEMPTS,
                    base_delay=0.05,
                    max_delay=0.2,
                    label="relay observations",
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._mark_recording_gap(batch[0].observed_at)
                self._discard_queued_rows()
                logger.error("Relay observation batch could not be persisted: %s", error)
            else:
                for row in batch:
                    if row.reason == "heartbeat":
                        self._last_persisted_heartbeat_at = row.observed_at
            finally:
                for _ in batch:
                    self._queue.task_done()

    def _discard_queued_rows(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            else:
                self._queue.task_done()

    @property
    def _registry_version(self) -> int:
        return self._snapshot.version if self._snapshot is not None else 0

    def _identity_at(self, channel: int) -> _RelayIdentity:
        identities = self._identities
        return identities[channel] if identities is not None else _EMPTY_IDENTITY

    def _channel_rows(
        self,
        observed_at: datetime,
        reason: RelayObservationReason,
        states: Sequence[bool | None],
        *,
        identities: Sequence[_RelayIdentity] | None = None,
        registry_version: int | None = None,
    ) -> tuple[RelayObservation, ...]:
        chosen_identities = identities or self._identities or (_EMPTY_IDENTITY,) * 16
        version = self._registry_version if registry_version is None else registry_version
        return tuple(
            self._channel_row(
                observed_at,
                channel,
                states[channel],
                reason,
                chosen_identities[channel],
                version,
            )
            for channel in range(16)
        )

    def _channel_row(
        self,
        observed_at: datetime,
        channel: int,
        state: bool | None,
        reason: RelayObservationReason,
        identity: _RelayIdentity,
        registry_version: int | None = None,
    ) -> RelayObservation:
        return RelayObservation(
            observed_at=observed_at,
            session_id=self.session_id,
            channel=channel,
            observed_state=state,
            device_id=identity.device_id,
            device_name=identity.device_name,
            device_type=identity.device_type,
            location=identity.location,
            cluster=identity.cluster,
            registry_version=self._registry_version
            if registry_version is None
            else registry_version,
            reason=reason,
        )

    def _heartbeat_row(self, observed_at: datetime) -> RelayObservation:
        return RelayObservation(
            observed_at=observed_at,
            session_id=self.session_id,
            channel=None,
            observed_state=None,
            device_id=None,
            device_name=None,
            device_type=None,
            location=None,
            cluster=None,
            registry_version=self._registry_version,
            reason="heartbeat",
        )

    def _heartbeat_due(self, observed_at: datetime) -> bool:
        previous = self._last_heartbeat_enqueued_at
        return previous is None or observed_at - previous >= HEARTBEAT_INTERVAL

    @staticmethod
    def _identities_for_snapshot(
        snapshot: RuntimeDeviceSnapshot,
    ) -> tuple[_RelayIdentity, ...]:
        identities = [_EMPTY_IDENTITY] * 16
        for channel, key in snapshot.by_channel.items():
            if channel < 0 or channel >= 16:
                continue
            info = snapshot.device_info.get(key)
            if info is None:
                continue
            device_id = info.get("device_id")
            identities[channel] = _RelayIdentity(
                device_id=device_id if isinstance(device_id, int) else None,
                device_name=key[2],
                device_type=(
                    str(info["device_type"])
                    if info.get("device_type") is not None
                    else None
                ),
                location=key[0],
                cluster=key[1],
            )
        return tuple(identities)

    @staticmethod
    def _normalize_time(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
