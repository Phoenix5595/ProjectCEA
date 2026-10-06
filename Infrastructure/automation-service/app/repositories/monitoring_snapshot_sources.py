"""Production read sources satisfying the monitoring snapshot builder protocols.

Every adapter is read-only and delegates to the repositories already owned by
``DatabaseManager``; the anchor/predecessor source issues parameterized
latest-before-timestamp queries against the raw monitoring hypertables.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from app.monitoring_publication.current import CurrentPublicationPublisher
from app.monitoring_publication.projection import (
    ProjectionPublicationAction,
    ProjectionPublicationDependencies,
)
from app.monitoring_publication.rich import project_saved_trajectory
from app.monitoring_publication.workers import MonitoringPublicationWorkers
from app.redis.monitoring import RedisCurrentPublicationWriter
from app.repositories.climate_timeline_snapshot import (
    ClimateProfileConfiguration,
    ClimateScheduleConfiguration,
    ClimateScheduleSnapshotBuilder,
)
from app.repositories.monitoring_snapshot_builder import (
    MonitoringSnapshotBuilder,
    MonitoringSnapshotRepositories,
    MonitoringSnapshotRequest,
    VersionSnapshotRepository,
)
from app.repositories.monitoring_snapshot_types import (
    FrozenRow,
    RuntimeSnapshotVersion,
    frozen,
)
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from app.services.future_projection import project_future_intervals
from app.services.light_trajectory import project_light_segments

_PUBLICATION_ROOMS: Final[tuple[tuple[str, str], ...]] = (
    ("Flower Room", "main"),
    ("Veg Room", "main"),
)

LOCAL_TZ = ZoneInfo("America/Toronto")


def _auto_flag(value: Any) -> bool:
    """Mirror the repository's ``(metadata->>'auto_mode_transition')::boolean``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "f", "no", "0"}
    return value is not False


def _phase_order(value: Any) -> int | None:
    """The metadata phase order as the repository's ``::int`` cast reads it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


_SETPOINT_PREDECESSOR_COLUMNS = (
    "timestamp, location, cluster, device_name, mode, "
    "effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint, "
    "effective_co2_setpoint, effective_vpd_setpoint, effective_light_intensity, "
    "nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint, "
    "nominal_co2_setpoint, nominal_vpd_setpoint, nominal_light_intensity, "
    "ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity, "
    "ramp_progress_co2, ramp_progress_vpd, ramp_progress_light"
)

_PROFILE_REVISION_QUERY = "SELECT COALESCE(MAX(version_id), 0) FROM config_versions"


async def _read_parameters_row(
    connection: Any, location: str, cluster: str, mode_id: int, submode_id: int | None
) -> Any:
    """The exact mode_parameters row for one profile identity on a given connection."""
    return await connection.fetchrow(
        "SELECT * FROM mode_parameters WHERE location = $1 AND cluster = $2 "
        "AND mode_id = $3 AND submode_id IS NOT DISTINCT FROM $4",
        location,
        cluster,
        mode_id,
        submode_id,
    )


def _revision_text(version_id: Any) -> str:
    if not isinstance(version_id, int) or isinstance(version_id, bool) or version_id < 0:
        raise RuntimeError("profile configuration revision is unavailable")
    return f"{version_id:07x}"


def _initial_parameters(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Unsaved photoperiod defaults for a profile without a stored parameter row."""
    day_start = time(17, 0)
    night_start = time(11, 0)
    photoperiod_hours = identity.get("photoperiod_hours")
    if identity.get("mode_name") in {"sleep", "drying"}:
        day_start = night_start = time(0, 0)
    elif isinstance(photoperiod_hours, int) and photoperiod_hours > 0:
        night_start = time((17 + photoperiod_hours) % 24, 0)
    return {
        "day_start_time": day_start,
        "night_start_time": night_start,
        "light_ramp_up_minutes": 15,
        "light_ramp_down_minutes": 15,
        "main_light_intensity": 100,
        "supplemental_light_intensity": 0,
    }


def _qualified(columns: str, prefix: str) -> str:
    return ", ".join(f"{prefix}.{name.strip()}" for name in columns.split(","))


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
    """Read calendar events plus the expected mode application for each day.

    Target dates are Toronto local dates while the projection window is emitted
    UTC. Each event row merges its metadata keys to the top level and carries
    the destination identity resolved through the read-only scheduler
    expectation source (``get_expected_transition``), so the pure projection
    consumes exactly the configured transitions the runtime applies, including
    the enabled and opt-out flags. Names are never turned into invented IDs.
    """

    def __init__(self, calendar_repo: Any, scheduler: CalendarModeScheduler) -> None:
        self._calendar = calendar_repo
        self._scheduler = scheduler

    async def read_calendar_events(
        self, location: str, cluster: str, start: datetime, end: datetime
    ) -> Sequence[Mapping[str, Any]]:
        first_day = start.astimezone(LOCAL_TZ).date()
        final_day = end.astimezone(LOCAL_TZ).date()
        events, _ = await self._calendar.list_events(
            first_day, final_day, location=location, limit=500
        )
        room_events = [event for event in events if event.get("cluster") == cluster]
        resolved: dict[Any, Mapping[str, Any]] = {}
        configured: dict[tuple[str, str | None], bool] = {}
        day = first_day
        while day <= final_day:
            transition = await self._scheduler.get_expected_transition(location, cluster, day)
            if transition is not None and isinstance(transition.get("event_id"), int):
                row: dict[str, Any] = {
                    key: transition[key]
                    for key in ("target_mode_id", "target_submode_id")
                    if key in transition
                }
                if "target_mode_id" in row:
                    mode_name = transition.get("target_mode_name")
                    submode_name = transition.get("target_submode_name")
                    names = (
                        mode_name if isinstance(mode_name, str) else "",
                        submode_name if isinstance(submode_name, str) else None,
                    )
                    if names not in configured:
                        configured[names] = await self._scheduler.is_profile_configured(
                            location, cluster, names[0], names[1]
                        )
                    row["destination_configured"] = configured[names]
                resolved[transition["event_id"]] = row
            day += timedelta(days=1)
        return [self._event_row(event, resolved.get(event.get("id"))) for event in room_events]

    def _event_row(
        self, event: Mapping[str, Any], resolved: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        row = dict(event)
        metadata = self._metadata(event.get("metadata"))
        row["auto_mode_transition"] = _auto_flag(metadata.get("auto_mode_transition"))
        row["phase_order"] = _phase_order(metadata.get("phase_order"))
        row["target_mode_name"] = metadata.get("target_mode_name")
        row["target_submode_name"] = metadata.get("target_submode_name")
        if resolved:
            row.update(dict(resolved))
        return row

    def _metadata(self, value: Any) -> dict[str, Any]:
        parse = getattr(self._calendar, "parse_metadata", None)
        try:
            parsed = parse(value) if callable(parse) else None
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}

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


class SavedTrajectorySnapshotSource:
    def __init__(
        self, room_modes: Any, climate_periods: Any, scheduler: CalendarModeScheduler, pool: Any
    ) -> None:
        self._room_modes = room_modes
        self._climate_periods = climate_periods
        self._scheduler = scheduler
        self._pool = pool

    async def read_active_mode(self, location: str, cluster: str) -> Mapping[str, object] | None:
        return await self._room_modes.get_active_mode(location, cluster)

    async def read_calendar_transition(
        self, location: str, cluster: str, on_date: date
    ) -> Mapping[str, object] | None:
        return await self._scheduler.get_expected_transition(location, cluster, on_date)

    async def read_schedule_configuration(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> ClimateScheduleConfiguration | None:
        periods = await self._climate_periods.get_periods_for_room_mode(
            location, cluster, mode_id, submode_id
        )
        async with self._pool.acquire() as connection:
            parameters = await _read_parameters_row(
                connection, location, cluster, mode_id, submode_id
            )
        return (
            None
            if parameters is None
            else ClimateScheduleConfiguration.from_rows(dict(parameters), periods)
        )

    async def read_profile(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> ClimateProfileConfiguration:
        """Read one exact profile aggregate on one read-only repeatable-read transaction."""
        async with (
            self._pool.acquire() as connection,
            connection.transaction(isolation="repeatable_read", readonly=True),
        ):
            identity = await self._room_modes.get_profile_identity_on_connection(
                connection, location, mode_id, submode_id
            )
            parameters = await _read_parameters_row(
                connection, location, cluster, mode_id, submode_id
            )
            periods = await self._climate_periods.get_periods_for_room_mode(
                location, cluster, mode_id, submode_id, conn=connection
            )
            version_id = await connection.fetchval(_PROFILE_REVISION_QUERY)
        stored = dict(parameters) if parameters is not None else _initial_parameters(identity)
        return ClimateProfileConfiguration(
            frozen(identity) or FrozenRow(()),
            ClimateScheduleConfiguration.from_rows(stored, periods),
            _revision_text(version_id),
            parameters is not None,
        )


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
        query = """
            WITH recent AS (
                SELECT DISTINCT device_name, mode
                FROM effective_setpoints
                WHERE location = $1 AND cluster = $2
                  AND timestamp >= $3::timestamptz - INTERVAL '48 hours' AND timestamp < $3::timestamptz
            )
            SELECT DISTINCT ON (r.device_name, r.mode)
                   {_COLUMNS}
            FROM recent r
            CROSS JOIN LATERAL (
                SELECT *
                FROM effective_setpoints e
                WHERE e.location = $1 AND e.cluster = $2
                  AND e.device_name = r.device_name AND e.mode = r.mode
                  AND e.timestamp < $3::timestamptz
                ORDER BY e.timestamp DESC
                LIMIT 1
            ) e
            ORDER BY r.device_name, r.mode
        """.replace("{_COLUMNS}", _qualified(_SETPOINT_PREDECESSOR_COLUMNS, "e"))
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, location, cluster, start)


class AnchorSnapshotSource:
    """Latest-before-timestamp anchors from automation state and photoperiod history."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def read_ramp_anchors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, Any]]:
        query = """
            WITH recent AS (
                SELECT DISTINCT device_name, mode
                FROM effective_setpoints
                WHERE location = $1 AND cluster = $2
                  AND timestamp >= $3::timestamptz - INTERVAL '48 hours' AND timestamp < $3::timestamptz
                    AND effective_light_intensity IS NOT NULL
            )
            SELECT DISTINCT ON (r.device_name, r.mode)
                   {_COLUMNS}
            FROM recent r
            CROSS JOIN LATERAL (
                SELECT *
                FROM effective_setpoints e
                WHERE e.location = $1 AND e.cluster = $2
                  AND e.device_name = r.device_name AND e.mode = r.mode
                  AND e.timestamp < $3::timestamptz
                ORDER BY e.timestamp DESC
                LIMIT 1
            ) e
            ORDER BY r.device_name, r.mode
        """.replace("{_COLUMNS}", _qualified(_SETPOINT_PREDECESSOR_COLUMNS, "e"))
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, location, cluster, start)

    async def read_automation_state_predecessors(
        self, location: str, cluster: str, start: datetime
    ) -> Sequence[Mapping[str, Any]]:
        query = """
            WITH recent AS (
                SELECT DISTINCT device_name
                FROM automation_state
                WHERE location = $1 AND cluster = $2
                  AND timestamp >= $3::timestamptz - INTERVAL '48 hours' AND timestamp < $3::timestamptz
            )
            SELECT DISTINCT ON (r.device_name)
                   a.timestamp, a.location, a.cluster, a.device_name, a.device_state,
                   a.device_mode, a.pid_output, a.duty_cycle_percent, a.control_reason
            FROM recent r
            CROSS JOIN LATERAL (
                SELECT *
                FROM automation_state a
                WHERE a.location = $1 AND a.cluster = $2
                  AND a.device_name = r.device_name AND a.timestamp < $3::timestamptz
                ORDER BY a.timestamp DESC
                LIMIT 1
            ) a
            ORDER BY r.device_name
        """
        async with self._pool.acquire() as connection:
            return await connection.fetch(query, location, cluster, start)

    async def read_photoperiod_predecessor(
        self, location: str, cluster: str, start: datetime
    ) -> Mapping[str, Any] | None:
        query = """
            SELECT observed_at, phase, mode_id, submode_id, runtime_snapshot_version
            FROM monitoring_room_photoperiod
            WHERE location = $1 AND cluster = $2 AND observed_at < $3::timestamptz
            ORDER BY observed_at DESC
            LIMIT 1
        """
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(query, location, cluster, start)
        return dict(row) if row else None


class ConfigVersionSnapshotSource(VersionSnapshotRepository):
    """Expose the persisted configuration version as the change cursor."""

    def __init__(self, version_provider: Callable[[], Awaitable[int | None]]) -> None:
        self._version_provider = version_provider

    async def read_source_versions(
        self, location: str, cluster: str
    ) -> tuple[tuple[str, int | None], ...]:
        del location, cluster
        return (("configuration", await self._version_provider()),)


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
        versions=ConfigVersionSnapshotSource(database.config_repo.get_latest_config_version),
    )
    snapshot_builder = _RegistryVersionBuilder(MonitoringSnapshotBuilder(repositories), registry)
    rich_snapshot_builder = ClimateScheduleSnapshotBuilder(
        SavedTrajectorySnapshotSource(
            database.room_mode_repo,
            database.climate_periods_repo,
            CalendarModeScheduler(database),
            database._pool,
        )
    )

    rooms: list[Any] = []
    for location, cluster in _PUBLICATION_ROOMS:
        publisher = CurrentPublicationPublisher(
            location,
            writer,
            config_version=database.config_repo.get_latest_config_version,
        )
        action = ProjectionPublicationAction(
            location,
            cluster,
            ProjectionPublicationDependencies(
                snapshot_builder=snapshot_builder,
                current_snapshot=_latest_of(publisher),
                writer=writer,
                projector=project_future_intervals,
                rich_snapshot_builder=rich_snapshot_builder,
                rich_projector=lambda snapshot, room=location: project_saved_trajectory(
                    snapshot, room, f"{registry.snapshot.version:07x}"
                ),
                light_projector=lambda snapshot: project_light_segments(
                    snapshot, f"{registry.snapshot.version:07x}"
                ),
                rich_writer=writer,
                complete_writer=writer,
                rich_config_revision=lambda: _config_revision(database),
            ),
        )
        rooms.append(
            RoomPublicationRecord(
                location=location, publisher=publisher, projection_publish=action.publish
            )
        )
    return MonitoringPublicationWorkers(rooms=tuple(rooms))


async def _config_revision(database: Any) -> str:
    version_id = await database.config_repo.get_latest_config_version()
    return f"{version_id or 0:07x}"


class RoomPublicationRecord:
    """One composed room awaiting worker supervision."""

    def __init__(
        self, location: str, publisher: CurrentPublicationPublisher, projection_publish: Any
    ) -> None:
        self.location = location
        self.current_publisher = publisher
        self.projection_publish = projection_publish
