"""Read-only assembly of immutable authority for one future monitoring window."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.schemas.monitoring_models import (
    AnchorFingerprint,
    MonitoringRange,
    ProjectionRevision,
    Quality,
    RuntimeSnapshotVersion,
)
from shared.monitoring_contracts import ConfigVersion

from .monitoring_snapshot_types import MonitoringSnapshot, fingerprint, frozen, frozen_rows

PROJECTION_HORIZON = timedelta(hours=24)
CONFIGURATION_SOURCE = "configuration"


@dataclass(frozen=True, slots=True)
class MonitoringSnapshotRequest:
    """One room and instant from which the fixed 24-hour projection window begins."""

    location: str
    cluster: str
    now: datetime
    runtime_snapshot_version: RuntimeSnapshotVersion


@dataclass(frozen=True, slots=True)
class MonitoringSnapshotBuildError(ValueError):
    """A caller supplied an instant that cannot safely identify a UTC projection window."""

    detail: str

    def __str__(self) -> str:
        return self.detail


class ModeSnapshotRepository(Protocol):
    """Read mode authority without changing it."""

    async def read_active_mode(
        self, location: str, cluster: str
    ) -> Mapping[str, object] | None: ...

    async def read_mode_parameters(
        self, location: str, cluster: str, active_mode: Mapping[str, object] | None
    ) -> Mapping[str, object] | None: ...


class CalendarSnapshotRepository(Protocol):
    """Read calendar authority that can alter Flower mode selection."""

    async def read_calendar_events(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, object]]: ...

    async def read_calendar_applications(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, object]]: ...


class ClimateSnapshotRepository(Protocol):
    """Read configured climate periods without consulting live controller state."""

    async def read_climate_periods(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, object]]: ...


class LightSnapshotRepository(Protocol):
    """Read light programs, targets, and scheduler predecessors."""

    async def read_light_targets(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, object]]: ...

    async def read_light_programs(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, object]]: ...

    async def read_expected_lights(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, object]]: ...

    async def read_effective_setpoint_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, object]]: ...


class AnchorSnapshotRepository(Protocol):
    """Read existing scheduler anchors, never live device or PID state."""

    async def read_ramp_anchors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, object]]: ...

    async def read_automation_state_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, object]]: ...

    async def read_photoperiod_predecessor(
        self, location: str, cluster: str, start: datetime
    ) -> Mapping[str, object] | None: ...


class VersionSnapshotRepository(Protocol):
    """Read immutable source-version cursors for a room's configuration inputs."""

    async def read_source_versions(
        self, location: str, cluster: str
    ) -> tuple[tuple[str, int | None], ...]: ...


@dataclass(frozen=True, slots=True)
class MonitoringSnapshotRepositories:
    """Injected read-only repository capabilities required by the snapshot builder."""

    modes: ModeSnapshotRepository
    calendar: CalendarSnapshotRepository
    climate: ClimateSnapshotRepository
    lights: LightSnapshotRepository
    anchors: AnchorSnapshotRepository
    versions: VersionSnapshotRepository


class MonitoringSnapshotBuilder:
    """Build one complete, immutable 24-hour input snapshot outside the control tick."""

    def __init__(self, repositories: MonitoringSnapshotRepositories) -> None:
        self._repositories = repositories

    async def build(self, request: MonitoringSnapshotRequest) -> MonitoringSnapshot:
        """Gather configured authority only; absent rows remain absent for pure projectors to expose."""
        if request.now.tzinfo is None:
            raise MonitoringSnapshotBuildError("projection snapshot now must be timezone-aware")
        start = request.now.astimezone(UTC)
        end = start + PROJECTION_HORIZON
        active_mode = await self._repositories.modes.read_active_mode(
            request.location, request.cluster
        )
        mode_parameters = await self._repositories.modes.read_mode_parameters(
            request.location, request.cluster, active_mode
        )
        calendar_events = await self._repositories.calendar.read_calendar_events(
            request.location, request.cluster, start, end
        )
        calendar_applications = await self._repositories.calendar.read_calendar_applications(
            request.location, request.cluster, start, end
        )
        climate_periods = await self._repositories.climate.read_climate_periods(
            request.location, request.cluster
        )
        light_targets = await self._repositories.lights.read_light_targets(
            request.location, request.cluster
        )
        light_programs = await self._repositories.lights.read_light_programs(
            request.location, request.cluster, start, end
        )
        expected_lights = await self._repositories.lights.read_expected_lights(
            request.location, request.cluster
        )
        predecessors = await self._repositories.lights.read_effective_setpoint_predecessors(
            request.location, request.cluster, start
        )
        ramp_anchors = await self._repositories.anchors.read_ramp_anchors(
            request.location, request.cluster, start
        )
        automation_predecessors = (
            await self._repositories.anchors.read_automation_state_predecessors(
                request.location, request.cluster, start
            )
        )
        photoperiod_predecessor = await self._repositories.anchors.read_photoperiod_predecessor(
            request.location, request.cluster, start
        )
        source_cursors = tuple(
            sorted(
                await self._repositories.versions.read_source_versions(
                    request.location, request.cluster
                )
            )
        )
        configuration_version = _configuration_version(source_cursors)
        anchor_data = {
            "ramps": tuple(dict(row) for row in ramp_anchors),
            "light_predecessors": tuple(dict(row) for row in predecessors),
            "automation_predecessors": tuple(dict(row) for row in automation_predecessors),
            "photoperiod_predecessor": None
            if photoperiod_predecessor is None
            else dict(photoperiod_predecessor),
            "sources": source_cursors,
        }
        return MonitoringSnapshot(
            range=MonitoringRange.from_absolute(start, end),
            location=request.location,
            cluster=request.cluster,
            active_mode=frozen(active_mode),
            calendar_events=frozen_rows(calendar_events),
            calendar_applications=frozen_rows(calendar_applications),
            climate_periods=frozen_rows(climate_periods),
            mode_parameters=frozen(mode_parameters),
            light_targets=frozen_rows(light_targets),
            light_programs=frozen_rows(light_programs),
            expected_lights=frozen_rows(expected_lights),
            effective_setpoint_predecessors=frozen_rows(predecessors),
            ramp_anchors=frozen_rows(ramp_anchors),
            automation_state_predecessors=frozen_rows(automation_predecessors),
            photoperiod_predecessor=frozen(photoperiod_predecessor),
            source_cursors=source_cursors,
            projection_revision=ProjectionRevision(f"{int(request.runtime_snapshot_version):07x}"),
            anchor_fingerprint=AnchorFingerprint(fingerprint(anchor_data)),
            anchor_observed_at=start,
            anchor_quality=Quality.EXACT,
            anchor_valid_until=end,
            runtime_snapshot_version=request.runtime_snapshot_version,
            config_version=configuration_version,
        )


def _configuration_version(
    source_cursors: tuple[tuple[str, int | None], ...],
) -> ConfigVersion | None:
    """Return the explicit configuration cursor, or no version when publication is unsafe."""
    version = next(
        (value for source, value in source_cursors if source == CONFIGURATION_SOURCE), None
    )
    return ConfigVersion(version) if isinstance(version, int) and version >= 1 else None
