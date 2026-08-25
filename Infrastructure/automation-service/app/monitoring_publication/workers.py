"""Independent supervision for room-scoped monitoring publication work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import anyio
from anyio.abc import TaskGroup

from app.monitoring_publication.current import CurrentPublicationPublisher
from shared.monitoring_contracts import CurrentSnapshot

PublicationAction = Callable[[], Awaitable[bool | None]]
_DEFAULT_INTERVAL: Final = timedelta(seconds=1)
_RECOVERABLE_PUBLICATION_ERRORS = (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError)


@dataclass(frozen=True, slots=True)
class PublicationWorkerHealth:
    """Route-facing state for one independently restarted publication action."""

    running: bool
    last_success_at: datetime | None
    last_success_age_seconds: float | None
    last_error_at: datetime | None
    failed_runs: int
    last_error: str | None
    pending: bool
    replaced_snapshots: int
    failed_publications: int


@dataclass(frozen=True, slots=True)
class RoomPublicationHealth:
    """Current and future publication health attributed to one room."""

    location: str
    current: PublicationWorkerHealth
    projection: PublicationWorkerHealth


@dataclass(frozen=True, slots=True)
class MonitoringPublicationHealth:
    """Keep publication health visibly separate from control-loop health."""

    current: PublicationWorkerHealth
    projection: PublicationWorkerHealth
    rooms: tuple[RoomPublicationHealth, ...]


@dataclass(frozen=True, slots=True)
class RoomPublication:
    """One room's nonblocking current handoff and future rebuild action."""

    location: str
    current_publisher: CurrentPublicationPublisher | None
    projection_publish: PublicationAction


class CurrentPublicationObserver:
    """Offer control snapshots synchronously to independently owned room handoffs."""

    def __init__(self, publishers: tuple[CurrentPublicationPublisher, ...]) -> None:
        self._publishers: tuple[CurrentPublicationPublisher, ...] = publishers

    def offer(self, snapshot: CurrentSnapshot) -> None:
        """Retain only the newest snapshot per room without Redis I/O."""
        for publisher in self._publishers:
            publisher.offer(snapshot)


class _PublicationWorker:
    """Track one restartable action without sharing its failure domain with control."""

    def __init__(
        self, publish: PublicationAction, handoff: CurrentPublicationPublisher | None
    ) -> None:
        self._publish: PublicationAction = publish
        self._handoff: CurrentPublicationPublisher | None = handoff
        self._last_success_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._failed_runs: int = 0
        self._last_error: str | None = None

    async def run(self, interval: timedelta) -> None:
        """Retry recoverable publication failures on the documented one-second cadence."""
        while True:
            try:
                published = await self._publish()
            except _RECOVERABLE_PUBLICATION_ERRORS as error:
                self._record_failure(str(error))
            else:
                if published is True:
                    self._last_success_at = datetime.now(UTC)
                elif published is False:
                    self._record_failure("publication rejected")
            await anyio.sleep(interval.total_seconds())

    def health_at(self, now: datetime, running: bool) -> PublicationWorkerHealth:
        """Sample mutable worker and handoff counters without external I/O."""
        handoff = self._handoff.health if self._handoff is not None else None
        return PublicationWorkerHealth(
            running=running,
            last_success_at=self._last_success_at,
            last_success_age_seconds=(
                None
                if self._last_success_at is None
                else (now - self._last_success_at).total_seconds()
            ),
            last_error_at=self._last_error_at,
            failed_runs=self._failed_runs,
            last_error=self._last_error,
            pending=False if handoff is None else handoff.pending,
            replaced_snapshots=0 if handoff is None else handoff.replaced_snapshots,
            failed_publications=0 if handoff is None else handoff.failed_publications,
        )

    def _record_failure(self, detail: str) -> None:
        self._failed_runs += 1
        self._last_error_at = datetime.now(UTC)
        self._last_error = detail


class MonitoringPublicationWorkers:
    """Own per-room current and future workers outside the control task's failure domain."""

    def __init__(
        self,
        current_publish: PublicationAction | None = None,
        projection_publish: PublicationAction | None = None,
        *,
        rooms: tuple[RoomPublication, ...] = (),
        interval: timedelta = _DEFAULT_INTERVAL,
    ) -> None:
        if interval <= timedelta():
            raise ValueError("publication worker interval must be positive")
        if rooms:
            if current_publish is not None or projection_publish is not None:
                raise ValueError("room workers cannot be mixed with legacy actions")
            room_actions = rooms
        elif current_publish is not None and projection_publish is not None:
            room_actions = (
                RoomPublication(
                    location="default",
                    current_publisher=None,
                    projection_publish=projection_publish,
                ),
            )
        else:
            raise ValueError("current and projection publication actions are required")
        self._room_definitions: tuple[RoomPublication, ...] = room_actions
        self._rooms: tuple[tuple[str, _PublicationWorker, _PublicationWorker], ...] = tuple(
            (
                room.location,
                _PublicationWorker(
                    _current_action(room.current_publisher, current_publish),
                    room.current_publisher,
                ),
                _PublicationWorker(room.projection_publish, None),
            )
            for room in room_actions
        )
        self._interval: timedelta = interval
        self._task_group: TaskGroup | None = None
        self._running: bool = False

    @property
    def current_observer(self) -> CurrentPublicationObserver:
        """Return the synchronous public seam injected into the control engine."""
        return CurrentPublicationObserver(
            tuple(
                room.current_publisher
                for room in self._room_definitions
                if room.current_publisher is not None
            )
        )

    @property
    def health(self) -> MonitoringPublicationHealth:
        """Sample publisher state without coupling it to any control decision."""
        return self.health_at(datetime.now(UTC))

    def health_at(self, now: datetime) -> MonitoringPublicationHealth:
        """Return every room health at one caller-provided instant."""
        sampled_at = now.astimezone(UTC)
        rooms = tuple(
            RoomPublicationHealth(
                location=location,
                current=current.health_at(sampled_at, self._running),
                projection=projection.health_at(sampled_at, self._running),
            )
            for location, current, projection in self._rooms
        )
        first = rooms[0]
        return MonitoringPublicationHealth(
            current=first.current,
            projection=first.projection,
            rooms=rooms,
        )

    async def start(self) -> None:
        """Start restartable workers after control dependencies exist; repeated starts are no-ops."""
        if self._task_group is not None:
            return
        task_group = anyio.create_task_group()
        await task_group.__aenter__()
        self._task_group = task_group
        self._running = True
        for location, current, projection in self._rooms:
            task_group.start_soon(
                current.run, self._interval, name=f"monitoring-current:{location}"
            )
            task_group.start_soon(
                projection.run, self._interval, name=f"monitoring-projection:{location}"
            )

    async def stop(self) -> None:
        """Stop all workers before Redis clients close; repeated stops are no-ops."""
        task_group = self._task_group
        if task_group is None:
            return
        self._running = False
        task_group.cancel_scope.cancel()
        await task_group.__aexit__(None, None, None)
        self._task_group = None


def _current_action(
    publisher: CurrentPublicationPublisher | None, fallback: PublicationAction | None
) -> PublicationAction:
    """Select the room handoff flush or the compatibility action once at construction."""
    if publisher is not None:
        return publisher.flush_once
    if fallback is None:
        raise ValueError("a current publication action is required")
    return fallback
