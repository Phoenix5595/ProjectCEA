"""Independent supervision for monitoring-publication background work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

import anyio

PublicationAction = Callable[[], Awaitable[bool | None]]
_DEFAULT_INTERVAL: Final = timedelta(seconds=1)
_RECOVERABLE_PUBLICATION_ERRORS = (
    ConnectionError,
    OSError,
    RuntimeError,
    TimeoutError,
    ValueError,
)


@dataclass(frozen=True, slots=True)
class PublicationWorkerHealth:
    """Route-facing health for one independently restarted publisher."""

    running: bool
    last_success_at: datetime | None
    last_success_age_seconds: float | None
    last_error_at: datetime | None
    failed_runs: int
    last_error: str | None


@dataclass(frozen=True, slots=True)
class MonitoringPublicationHealth:
    """Keep publication health visibly separate from control-loop health."""

    current: PublicationWorkerHealth
    projection: PublicationWorkerHealth


class _PublicationWorker:
    """Track one restartable background action without control-loop coupling."""

    def __init__(self, publish: PublicationAction) -> None:
        self._publish: PublicationAction = publish
        self._last_success_at: datetime | None = None
        self._last_error_at: datetime | None = None
        self._failed_runs: int = 0
        self._last_error: str | None = None

    async def run(self, interval: timedelta) -> None:
        """Repeat publication after recoverable failures without propagating to control."""
        while True:
            try:
                published = await self._publish()
            except _RECOVERABLE_PUBLICATION_ERRORS as error:
                self._failed_runs += 1
                self._last_error_at = datetime.now(UTC)
                self._last_error = str(error)
            else:
                if published is True:
                    self._last_success_at = datetime.now(UTC)
                elif published is False:
                    self._failed_runs += 1
                    self._last_error_at = datetime.now(UTC)
                    self._last_error = "publication rejected"
            await anyio.sleep(interval.total_seconds())

    def health_at(self, now: datetime, running: bool) -> PublicationWorkerHealth:
        """Return immutable health with a status-sampling age, never performing I/O."""
        last_success_at = self._last_success_at
        return PublicationWorkerHealth(
            running=running,
            last_success_at=last_success_at,
            last_success_age_seconds=(
                None if last_success_at is None else (now - last_success_at).total_seconds()
            ),
            last_error_at=self._last_error_at,
            failed_runs=self._failed_runs,
            last_error=self._last_error,
        )


class MonitoringPublicationWorkers:
    """Own current and projection workers outside the control task's failure domain."""

    def __init__(
        self,
        current_publish: PublicationAction,
        projection_publish: PublicationAction,
        *,
        interval: timedelta = _DEFAULT_INTERVAL,
    ) -> None:
        if interval <= timedelta():
            raise ValueError("publication worker interval must be positive")
        self._current: _PublicationWorker = _PublicationWorker(current_publish)
        self._projection: _PublicationWorker = _PublicationWorker(projection_publish)
        self._interval: timedelta = interval
        self._tasks: tuple[asyncio.Task[None], ...] = ()
        self._running: bool = False

    @property
    def health(self) -> MonitoringPublicationHealth:
        """Sample publisher state without coupling it to any control decision."""
        return self.health_at(datetime.now(UTC))

    def health_at(self, now: datetime) -> MonitoringPublicationHealth:
        """Return current and projection health at one caller-provided instant."""
        sampled_at = now.astimezone(UTC)
        return MonitoringPublicationHealth(
            current=self._current.health_at(sampled_at, self._running),
            projection=self._projection.health_at(sampled_at, self._running),
        )

    async def start(self) -> None:
        """Start both publishers independently of the existing control task group."""
        if self._running:
            return
        self._running = True
        self._tasks = (
            asyncio.create_task(self._current.run(self._interval), name="monitoring-current"),
            asyncio.create_task(self._projection.run(self._interval), name="monitoring-projection"),
        )

    async def stop(self) -> None:
        """Cancel publication work before its Redis/database dependencies close."""
        if not self._tasks:
            return
        self._running = False
        for task in self._tasks:
            _ = task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                continue
        self._tasks = ()
