"""Production read sources satisfying the monitoring snapshot builder protocols.

Every adapter is read-only and delegates to the repositories already owned by
``DatabaseManager``; the anchor/predecessor source issues parameterized
latest-before-timestamp queries against the raw monitoring hypertables.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Final

from app.monitoring_publication.current import CurrentPublicationPublisher
from app.monitoring_publication.projection import (
    ProjectionPublicationAction,
    ProjectionPublicationDependencies,
)
from app.monitoring_publication.workers import MonitoringPublicationWorkers
from app.redis.monitoring import RedisCurrentPublicationWriter
from app.repositories.monitoring_snapshot_builder import (
    MonitoringSnapshotBuilder,
    MonitoringSnapshotRepositories,
    MonitoringSnapshotRequest,
    VersionSnapshotRepository,
)
from app.repositories.monitoring_snapshot_types import RuntimeSnapshotVersion
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from app.services.future_projection import project_future_intervals

_PUBLICATION_ROOMS: Final[tuple[tuple[str, str], ...]] = (
    ("Flower Room", "main"),
    ("Veg Room", "main"),
)

_SETPOINT_PREDECESSOR_COLUMNS = """
    timestamp, location, cluster, device_name, mode,
    effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
    effective_co2_setpoint, effective_vpd_setpoint, effective_light_intensity,
    nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
    nominal_co2_setpoint, nominal_vpd_setpoint, nominal_light_intensity,
    ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
    ramp_progress_co2, ramp_progress_vpd, ramp_progress_light
"""


class ModeSnapshotSource:
    """Delegate active-mode and photoperiod-parameter reads to the mode repository."""

    def __init__(self, room_modes: Any) -> None:
        self._room_modes = room_modes

    async def read_active_mode(self, location: str, cluster: str) -> Mapping[str, Any] | None:
        return await self._room_modes.get_active_mode(location, cluster)

    async def read_mode_parameters(
        self, location: str, cluster: str, active_mode: Mapping[str, Any] | None
    ) -> Mapping[str, Any] | None:
        mode_name = (active_mode or {}).get("mode_name")
        if mode_name is None:
            return None
        return await self._room_modes.get_mode_parameters(
            location, cluster, mode_name, (active_mode or {}).get("submode_name")
        )


class CalendarSnapshotSource:
    """Read calendar events plus the expected mode application for each day."""

    def __init__(self, calendar_repo: Any, scheduler: CalendarModeScheduler) -> None:
        self._calendar = calendar_repo
        self._scheduler = scheduler

    async def read_calendar_events(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, Any]]:
        events, _ = await self._calendar.list_events(
            start.date(), end.date(), location=location, limit=500
        )
        return [event for event in events if event.get("cluster") == cluster]

    async def read_calendar_applications(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, Any]]:
        applications: list[Mapping[str, Any]] = []
        day = start.date()
        final_day = end.date()
        while day <= final_day:
            expected = await self._scheduler.get_expected_mode(location, cluster, day)
            applications.append({"date": day.isoformat(), **expected})
            day += timedelta(days=1)
        return applications


class ClimatePeriodSnapshotSource:
    """Delegate climate-period reads to the climate periods repository."""

    def __init__(self, climate_periods: Any) -> None:
        self._climate_periods = climate_periods

    async def read_climate_periods(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, Any]]:
        return await self._climate_periods.get_periods(location, cluster)


class LightSnapshotSource:
    """Resolve light targets, programs, expected devices, and setpoint predecessors."""

    def __init__(
        self,
        targets: Any,
        programs: Any,
        pool: Any,
        registry: Any,
        active_mode_reader: ModeSnapshotSource,
    ) -> None:
        self._targets = targets
        self._programs = programs
        self._pool = pool
        self._registry = registry
        self._active_mode_reader = active_mode_reader

    def _expected_lights(self, location: str, cluster: str) -> list[dict[str, Any]]:
        snapshot = self._registry.snapshot
        room_devices = snapshot.hierarchy.get(location, {}).get(cluster, {})
        return [
            {"device_id": info.get("device_id"), "device_name": name}
            for name, info in sorted(room_devices.items())
            if info.get("device_type") == "light" and info.get("device_id") is not None
        ]

    async def _active_mode_id(self, location: str, cluster: str) -> int | None:
        active = await self._active_mode_reader.read_active_mode(location, cluster)
        mode_id = (active or {}).get("mode_id")
        return int(mode_id) if mode_id is not None else None

    async def read_light_targets(self, location: str, cluster: str) -> Sequence[Mapping[str, Any]]:
        mode_id = await self._active_mode_id(location, cluster)
        if mode_id is None:
            return []
        intensities = await self._targets.get_intensities_for_room(location, cluster, mode_id)
        return [
            {"device_id": device_id, "target_intensity": intensity}
            for device_id, intensity in sorted(intensities.items())
        ]

    async def read_light_programs(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, Any]]:
        del start, end
        mode_id = await self._active_mode_id(location, cluster)
        if mode_id is None:
            return []
        return await self._programs.get_active_programs(location, cluster, mode_id)

    async def read_expected_lights(
        self, location: str, cluster: str
    ) -> Sequence[Mapping[str, Any]]:
        return self._expected_lights(location, cluster)

    async def read_effective_setpoint_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, Any]]:
        query = f"""
            SELECT DISTINCT ON (device_name, mode) {_SETPOINT_PREDECESSOR_COLUMNS}
            FROM effective_setpoints
            WHERE location = $1 AND cluster = $2 AND timestamp < $3
            ORDER BY device_name, mode, timestamp DESC
        """
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, location, cluster, start)


class AnchorSnapshotSource:
    """Latest-before-timestamp anchors from automation state and photoperiod history."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def read_ramp_anchors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, Any]]:
        query = f"""
            SELECT DISTINCT ON (device_name, mode) {_SETPOINT_PREDECESSOR_COLUMNS}
            FROM effective_setpoints
            WHERE location = $1 AND cluster = $2
              AND timestamp < $3 AND effective_light_intensity IS NOT NULL
            ORDER BY device_name, mode, timestamp DESC
        """
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, location, cluster, start)

    async def read_automation_state_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, Any]]:
        query = """
            SELECT DISTINCT ON (device_name)
                   timestamp, location, cluster, device_name, device_state, device_mode,
                   pid_output, duty_cycle_percent, control_reason
            FROM automation_state
            WHERE location = $1 AND cluster = $2 AND timestamp < $3
            ORDER BY device_name, timestamp DESC
        """
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, location, cluster, start)

    async def read_photoperiod_predecessor(
        self, location: str, cluster: str, start: datetime
    ) -> Mapping[str, Any] | None:
        query = """
            SELECT observed_at, phase, mode_id, submode_id, runtime_snapshot_version
            FROM monitoring_room_photoperiod
            WHERE location = $1 AND cluster = $2 AND observed_at < $3
            ORDER BY observed_at DESC
            LIMIT 1
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, location, cluster, start)
        return dict(row) if row else None


class ConfigVersionSnapshotSource(VersionSnapshotRepository):
    """Expose the latest configuration version cursor for projection publication."""

    def __init__(self, config_repo: Any) -> None:
        self._config_repo = config_repo

    async def read_source_versions(
        self, location: str, cluster: str
    ) -> tuple[tuple[str, int | None], ...]:
        del location, cluster
        return (("configuration", await self._config_repo.get_latest_config_version()),)


class _RegistryVersionBuilder:
    """Stamp the current runtime snapshot version onto every build request."""

    def __init__(self, inner: MonitoringSnapshotBuilder, registry: Any) -> None:
        self._inner = inner
        self._registry = registry

    async def build(self, request: MonitoringSnapshotRequest) -> Any:
        stamped = MonitoringSnapshotRequest(
            location=request.location,
            cluster=request.cluster,
            now=request.now,
            runtime_snapshot_version=RuntimeSnapshotVersion(self._registry.snapshot.version),
        )
        return await self._inner.build(stamped)


def _latest_of(publisher: CurrentPublicationPublisher) -> Callable[[], Any]:
    """Return a live callable so the action reads freshness at run time, not build time."""
    return lambda: publisher.latest


def build_monitoring_publication_workers(
    database: Any, automation_redis: Any, registry: Any
) -> MonitoringPublicationWorkers:
    """Compose per-room current publishers and projection actions for production."""
    writer = RedisCurrentPublicationWriter(automation_redis.redis_client)
    mode_source = ModeSnapshotSource(database.room_mode_repo)
    calendar_source = CalendarSnapshotSource(
        database.calendar_repo, CalendarModeScheduler(database)
    )
    light_source = LightSnapshotSource(
        database.light_target_intensity_repo,
        database.light_programs_repo,
        database._pool,
        registry,
        mode_source,
    )
    repositories = MonitoringSnapshotRepositories(
        modes=mode_source,
        calendar=calendar_source,
        climate=ClimatePeriodSnapshotSource(database.climate_periods_repo),
        lights=light_source,
        anchors=AnchorSnapshotSource(database._pool),
        versions=ConfigVersionSnapshotSource(database.config_repo),
    )
    snapshot_builder = _RegistryVersionBuilder(MonitoringSnapshotBuilder(repositories), registry)

    rooms: list[Any] = []
    for location, cluster in _PUBLICATION_ROOMS:
        publisher = CurrentPublicationPublisher(location, writer)
        action = ProjectionPublicationAction(
            location,
            cluster,
            ProjectionPublicationDependencies(
                snapshot_builder=snapshot_builder,
                current_snapshot=_latest_of(publisher),
                writer=writer,
                projector=project_future_intervals,
            ),
        )
        rooms.append(
            RoomPublicationRecord(
                location=location, publisher=publisher, projection_publish=action.publish
            )
        )
    return MonitoringPublicationWorkers(rooms=tuple(rooms))


class RoomPublicationRecord:
    """One composed room awaiting worker supervision."""

    def __init__(
        self, location: str, publisher: CurrentPublicationPublisher, projection_publish: Any
    ) -> None:
        self.location = location
        self.current_publisher = publisher
        self.projection_publish = projection_publish
