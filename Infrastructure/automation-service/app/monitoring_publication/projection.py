"""Version-triggered publication of automation-owned future projection facts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Protocol

import anyio
from pydantic import ValidationError
from typing_extensions import override

from shared.monitoring_contracts import (
    ConfigVersion,
    CurrentSnapshot,
    FutureProjection,
    MonitoringPublication,
    PublicationVersion,
    validate_projection_timeline,
)

from ..repositories.climate_timeline_snapshot import ClimateScheduleSnapshot, TimelineWindow
from ..repositories.monitoring_snapshot_builder import MonitoringSnapshotRequest
from ..repositories.monitoring_snapshot_types import MonitoringSnapshot
from ..schemas.climate_timeline import (
    RichTrajectoryEnvelope,
    TimelineWarning,
    UnavailableTrajectorySegment,
)
from ..schemas.monitoring_models import RuntimeSnapshotVersion
from ..services.future_projection import project_future_intervals

_DEFAULT_REDIS_TIMEOUT_SECONDS: Final = 1.0
MAX_RICH_TRAJECTORY_SEGMENTS: Final = 4096


@dataclass(frozen=True, slots=True)
class ProjectionPublicationRequest:
    """One background refresh trigger bound to a configuration/algorithm revision."""

    location: str
    version: PublicationVersion
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ProjectionPublicationError(
                "projection refresh observed_at must be timezone-aware"
            )
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ProjectionPublicationError(ValueError):
    """A factory result cannot safely replace the cached projection authority."""

    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


class ProjectionStore(Protocol):
    """Atomic storage boundary for one location's cached future projection."""

    def read_projection(self, location: str) -> FutureProjection | None: ...

    def replace_projection(self, location: str, projection: FutureProjection) -> None:
        """Atomically replace the complete projection payload for ``location``."""


class ProjectionFactory(Protocol):
    """Authority-preserving future fact builder, invoked only after a cache trigger."""

    def build(self, request: ProjectionPublicationRequest) -> FutureProjection: ...


class ProjectionPublisher:
    """Publish a complete projection only when its cache is missing, stale, or mismatched."""

    def __init__(self, store: ProjectionStore, factory: ProjectionFactory) -> None:
        self._store: ProjectionStore = store
        self._factory: ProjectionFactory = factory

    def publish_if_stale(self, request: ProjectionPublicationRequest) -> FutureProjection:
        """Return the current authority, rebuilding it only for an explicit cache trigger."""
        cached = self._store.read_projection(request.location)
        if cached is not None and projection_is_current(cached, request):
            return cached

        projection = self._factory.build(request)
        _validate_projection(projection, request)
        self._store.replace_projection(request.location, projection)
        return projection


class AsyncSnapshotBuilder(Protocol):
    """Build a frozen projection input from the T25 request contract."""

    async def build(self, request: MonitoringSnapshotRequest) -> MonitoringSnapshot: ...


class FuturePublicationWriter(Protocol):
    """Atomically replace one complete future timeline from a background worker."""

    def write_future(self, location: str, projections: tuple[FutureProjection, ...]) -> bool:
        """Return whether Redis accepted the complete replacement."""
        ...


class FutureProjector(Protocol):
    """Adapt an immutable authority snapshot without I/O."""

    def __call__(self, snapshot: MonitoringSnapshot) -> tuple[FutureProjection, ...]: ...


class RichTrajectoryWriter(Protocol):
    """Persist one complete rich trajectory outside request and control paths."""

    def write_rich_trajectory(self, location: str, trajectory: RichTrajectoryEnvelope) -> bool: ...


class CompletePublicationWriter(Protocol):
    """Atomically persist legacy and rich payloads for one revision."""

    def write_complete(
        self,
        location: str,
        projections: tuple[FutureProjection, ...],
        trajectory: RichTrajectoryEnvelope,
    ) -> bool: ...


class RichTrajectoryProjector(Protocol):
    """Evaluate a saved immutable authority snapshot without I/O."""

    def __call__(self, snapshot: ClimateScheduleSnapshot) -> RichTrajectoryEnvelope | None: ...


class AsyncRichSnapshotBuilder(Protocol):
    async def build_saved(
        self, location: str, cluster: str, window: TimelineWindow
    ) -> ClimateScheduleSnapshot: ...


@dataclass(frozen=True, slots=True)
class ProjectionPublicationDependencies:
    """I/O and pure seams needed to publish one room's future authority."""

    snapshot_builder: AsyncSnapshotBuilder
    current_snapshot: Callable[[], CurrentSnapshot | None]
    writer: FuturePublicationWriter
    projector: FutureProjector = project_future_intervals
    rich_projector: RichTrajectoryProjector | None = None
    rich_writer: RichTrajectoryWriter | None = None
    complete_writer: CompletePublicationWriter | None = None
    rich_config_revision: Callable[[], Awaitable[str]] | None = None
    rich_snapshot_builder: AsyncRichSnapshotBuilder | None = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    redis_timeout_seconds: float = _DEFAULT_REDIS_TIMEOUT_SECONDS


class ProjectionPublicationAction:
    """Rebuild a room timeline only when its current version or expiry requires it."""

    def __init__(
        self, location: str, cluster: str, dependencies: ProjectionPublicationDependencies
    ) -> None:
        self._location: str = location
        self._cluster: str = cluster
        self._dependencies: ProjectionPublicationDependencies = dependencies
        self._last_good: tuple[FutureProjection, ...] | None = None
        self._last_good_rich: RichTrajectoryEnvelope | None = None
        self._generation: int = 0

    @property
    def last_good(self) -> tuple[FutureProjection, ...] | None:
        """Expose the latest successfully published timeline without I/O."""
        return self._last_good

    @property
    def last_good_rich(self) -> RichTrajectoryEnvelope | None:
        """Expose the latest complete rich saved trajectory without I/O."""
        return self._last_good_rich

    async def publish(self) -> bool | None:
        """Build, validate, and atomically replace only a complete compatible future tuple."""
        current = self._dependencies.current_snapshot()
        if current is None:
            return None
        observed_current = current
        now = self._dependencies.now().astimezone(UTC)
        rich_revision = None
        if self._dependencies.rich_config_revision is not None:
            rich_revision = await self._dependencies.rich_config_revision()
        if _timeline_is_current(self._last_good, current.version, now) and (
            self._last_good_rich is None
            or rich_revision is None
            or self._last_good_rich.base_config_revision == rich_revision
        ):
            return None
        self._generation += 1
        generation = self._generation
        snapshot = await self._dependencies.snapshot_builder.build(
            MonitoringSnapshotRequest(
                location=self._location,
                cluster=self._cluster,
                now=now,
                runtime_snapshot_version=RuntimeSnapshotVersion(int(current.version.revision, 16)),
            )
        )
        projections = self._dependencies.projector(snapshot)
        if rich_revision is not None:
            config_version = ConfigVersion(int(rich_revision, 16))
            projections = tuple(
                projection.model_copy(
                    update={
                        "version": projection.version.model_copy(
                            update={"config_version": config_version}
                        )
                    }
                )
                for projection in projections
            )
            current = current.model_copy(
                update={
                    "version": current.version.model_copy(update={"config_version": config_version})
                }
            )
        _ = validate_projection_timeline(projections)
        try:
            _ = MonitoringPublication(current=current, future=projections)
        except ValidationError:
            return False
        rich = await _rich_trajectory(
            self._dependencies.rich_snapshot_builder,
            self._dependencies.rich_projector,
            self._location,
            self._cluster,
            now,
        )
        if rich is not None:
            rich = _bounded_rich_trajectory(rich)
            if rich_revision is not None:
                rich = _with_config_revision(rich, rich_revision)
        if (
            generation != self._generation
            or self._dependencies.current_snapshot() != observed_current
        ):
            return None
        published = False
        with anyio.move_on_after(self._dependencies.redis_timeout_seconds) as scope:
            if rich is not None and self._dependencies.complete_writer is not None:
                complete_writer = self._dependencies.complete_writer
                published = await anyio.to_thread.run_sync(
                    complete_writer.write_complete,
                    self._location,
                    projections,
                    rich,
                    cancellable=True,
                )
            else:
                published = await anyio.to_thread.run_sync(
                    self._dependencies.writer.write_future,
                    self._location,
                    projections,
                    cancellable=True,
                )
        if scope.cancel_called or not published:
            return False
        if rich is not None and self._dependencies.complete_writer is None:
            rich_writer = self._dependencies.rich_writer
            if rich_writer is None:
                return False
            if (
                generation != self._generation
                or self._dependencies.current_snapshot() != observed_current
            ):
                return None
            written = await anyio.to_thread.run_sync(
                rich_writer.write_rich_trajectory,
                self._location,
                rich,
                cancellable=True,
            )
            if not written:
                return False
        self._last_good = projections
        self._last_good_rich = rich
        return True


def projection_is_current(
    projection: FutureProjection | None, request: ProjectionPublicationRequest
) -> bool:
    """Whether a cached projection exactly matches the requested authority and remains valid."""
    return (
        projection is not None
        and projection.version == request.version
        and projection.valid_until > request.observed_at
    )


def _timeline_is_current(
    projections: tuple[FutureProjection, ...] | None,
    version: PublicationVersion,
    now: datetime,
) -> bool:
    """Accept a retained complete cache only while every authority check still holds."""
    return (
        projections is not None
        and all(projection.version == version for projection in projections)
        and (not projections or projections[-1].valid_until > now)
    )


def _validate_projection(
    projection: FutureProjection, request: ProjectionPublicationRequest
) -> None:
    if projection.version != request.version:
        raise ProjectionPublicationError(
            "projection factory returned a mismatched publication version"
        )
    if projection.valid_until <= request.observed_at:
        raise ProjectionPublicationError("projection factory returned an expired projection")


async def _rich_trajectory(
    builder: AsyncRichSnapshotBuilder | None,
    projector: RichTrajectoryProjector | None,
    location: str,
    cluster: str,
    now: datetime,
) -> RichTrajectoryEnvelope | None:
    if builder is None or projector is None:
        return None
    return projector(await builder.build_saved(location, cluster, TimelineWindow.rolling(now)))


def _bounded_rich_trajectory(trajectory: RichTrajectoryEnvelope) -> RichTrajectoryEnvelope:
    if len(trajectory.segments) <= MAX_RICH_TRAJECTORY_SEGMENTS:
        return trajectory
    source = trajectory.segments[0].source
    unavailable = UnavailableTrajectorySegment(
        start=trajectory.window.start,
        end=trajectory.window.end,
        metric="trajectory",
        unit="unavailable",
        trajectory_kind="scheduled",
        quality="unavailable",
        source=source,
        shape="unavailable",
        reason="rich trajectory exceeds the 4096-segment response budget",
    )
    return trajectory.model_copy(
        update={
            "segments": (unavailable,),
            "warnings": trajectory.warnings
            + (
                TimelineWarning(
                    code="trajectory_segment_budget_exceeded",
                    detail="rich trajectory exceeds the 4096-segment response budget",
                ),
            ),
        }
    )


def _with_config_revision(
    trajectory: RichTrajectoryEnvelope, revision: str
) -> RichTrajectoryEnvelope:
    """Align every saved segment source with the authoritative config cursor."""
    return trajectory.model_copy(
        update={
            "base_config_revision": revision,
            "segments": tuple(
                segment.model_copy(
                    update={
                        "source": segment.source.model_copy(update={"config_revision": revision})
                    }
                )
                for segment in trajectory.segments
            ),
        }
    )
