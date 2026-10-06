"""Atomic persistence for the timeline-owned climate configuration aggregate."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, time
import json
from types import TracebackType
from typing import Any, Final, Protocol, TypeAlias, final

from typing_extensions import override

from app.schemas.climate_timeline import TimelineApplyRequest

SqlArgument: TypeAlias = str | int | float | time | None


def _parse_time_text(text: str) -> time:
    """Convert an HH:MM or HH:MM:SS text into a Postgres TIME argument."""
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).time()
        except ValueError:
            continue
    raise ValueError(f"invalid time text: {text!r}")


class TimelineApplyRow(Protocol):
    def __getitem__(self, column: str) -> Any: ...


class TimelineApplyConnection(Protocol):
    """The transaction-scoped asyncpg operations owned by Apply."""

    def transaction(self) -> AbstractAsyncContextManager[None]: ...

    async def execute(self, query: str, *args: SqlArgument) -> str: ...

    async def fetchval(self, query: str, *args: SqlArgument) -> int | None: ...

    async def fetchrow(self, query: str, *args: SqlArgument) -> TimelineApplyRow | None: ...


class AcquiredTimelineConnection(Protocol):
    async def __aenter__(self) -> TimelineApplyConnection: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None: ...


class TimelineApplyPool(Protocol):
    """Acquire one transaction-capable connection for a timeline aggregate."""

    def acquire(self, *, timeout: float | None = None) -> AcquiredTimelineConnection: ...


@final
class TimelineApplyStaleRevisionError(RuntimeError):
    """The reviewed timeline no longer matches the committed configuration revision."""

    def __init__(self, expected: str, current: str) -> None:
        self.expected: str = expected
        self.current: str = current
        super().__init__(expected, current)

    @override
    def __str__(self) -> str:
        return f"timeline revision {self.expected} is stale; current revision is {self.current}"


@dataclass(frozen=True, slots=True)
class TimelineApplyCommit:
    """The one revision produced by a committed timeline aggregate replacement."""

    config_revision: str
    parameters_configured: bool


class RoomModeIdentitySource(Protocol):
    """The exact profile identity validator owned by the mode repository."""

    async def get_profile_identity_on_connection(
        self,
        conn: Any,
        location: str,
        mode_id: int,
        submode_id: int | None,
    ) -> Any: ...


class TimelineScheduleSync(Protocol):
    """The supplied-connection schedule owner used by an affected active save."""

    async def sync_on_connection(
        self,
        conn: Any,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
        *,
        parameters: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


@final
class TimelineApplyRepository:
    """Replace periods and timeline-owned photoperiod fields through one connection."""

    def __init__(
        self,
        pool: TimelineApplyPool,
        room_mode_repo: RoomModeIdentitySource,
        schedule_service: TimelineScheduleSync,
    ) -> None:
        self._pool: TimelineApplyPool = pool
        self._room_mode_repo: RoomModeIdentitySource = room_mode_repo
        self._schedule_service: TimelineScheduleSync = schedule_service

    async def apply(
        self, location: str, cluster: str, request: TimelineApplyRequest
    ) -> TimelineApplyCommit:
        """Check revision and persist the complete reviewed aggregate atomically."""
        async with self._pool.acquire() as connection, connection.transaction():
            _ = await connection.execute("SELECT pg_advisory_xact_lock($1)", _TIMELINE_APPLY_LOCK)
            version_id = await connection.fetchval(_CURRENT_REVISION_QUERY)
            if version_id is None:
                raise RuntimeError("timeline revision is unavailable")
            current_revision = _revision(version_id)
            if request.expected_config_revision != current_revision:
                raise TimelineApplyStaleRevisionError(
                    request.expected_config_revision, current_revision
                )

            # Exact-profile validation before any data changes; metadata
            # SELECTs are allowed, mutations are not.
            _ = await self._room_mode_repo.get_profile_identity_on_connection(
                connection, location, request.mode_id, request.submode_id
            )
            # The active row lock under the timeline advisory lock decides
            # whether this Apply targets the running profile and serializes
            # with the activation owner taking the same two locks.
            active_row = await connection.fetchrow(_LOCK_ACTIVE_ROW_QUERY, location, cluster)
            affected_active = (
                active_row is not None
                and active_row["mode_id"] == request.mode_id
                and active_row["submode_id"] == request.submode_id
            )
            prior_parameters = (
                await connection.fetchrow(
                    _SELECT_PARAMETERS_QUERY,
                    location,
                    cluster,
                    request.mode_id,
                    request.submode_id,
                )
                if affected_active
                else None
            )
            await self._update_or_insert_mode_parameters(connection, location, cluster, request)

            _ = await connection.execute(
                _DELETE_PERIODS_QUERY,
                location,
                cluster,
                request.mode_id,
                request.submode_id,
            )
            for period in request.periods:
                _ = await connection.execute(
                    _INSERT_PERIOD_QUERY,
                    location,
                    cluster,
                    request.mode_id,
                    request.submode_id,
                    period.period_name,
                    datetime.strptime(period.start_time, "%H:%M").time(),
                    datetime.strptime(period.end_time, "%H:%M").time(),
                    period.ramp_minutes,
                    period.heating_setpoint,
                    period.cooling_setpoint,
                    period.vpd_setpoint,
                    period.co2_setpoint,
                    period.details,
                )

            row = await connection.fetchrow(
                _INSERT_VERSION_QUERY,
                "climate_timeline",
                location,
                cluster,
                json.dumps({"mode_id": request.mode_id, "submode_id": request.submode_id}),
            )
            if row is None:
                raise RuntimeError("timeline revision was not recorded")
            config_revision = _revision(row["version_id"])

            # Only a changed photoperiod on the running profile regenerates the
            # derived non-light DAY/NIGHT rows on this same connection before
            # commit; an inactive save never touches schedules, active mode,
            # history or any light/runtime authority.
            if affected_active and self._photoperiod_changed(prior_parameters, request):
                _ = await self._schedule_service.sync_on_connection(
                    connection,
                    location,
                    cluster,
                    request.mode_id,
                    request.submode_id,
                    parameters=self._committed_parameter_row(
                        prior_parameters, location, cluster, request
                    ),
                )
            return TimelineApplyCommit(config_revision, parameters_configured=True)

    @staticmethod
    def _photoperiod_changed(
        prior_parameters: TimelineApplyRow | None, request: TimelineApplyRequest
    ) -> bool:
        """Report whether the committed photoperiod actually changes the stored one."""
        if prior_parameters is None:
            return True
        photoperiod = request.photoperiod
        return (
            prior_parameters["day_start_time"] != _parse_time_text(photoperiod.day_start_time)
            or prior_parameters["night_start_time"]
            != _parse_time_text(photoperiod.night_start_time)
            or prior_parameters["light_ramp_up_minutes"] != photoperiod.ramp_up_minutes
            or prior_parameters["light_ramp_down_minutes"] != photoperiod.ramp_down_minutes
        )

    @staticmethod
    def _committed_parameter_row(
        prior_parameters: TimelineApplyRow | None,
        location: str,
        cluster: str,
        request: TimelineApplyRequest,
    ) -> dict[str, Any]:
        """The exact parameters row Apply commits, reused by the schedule owner."""
        clocks: dict[str, Any] = {
            "day_start_time": _parse_time_text(request.photoperiod.day_start_time),
            "night_start_time": _parse_time_text(request.photoperiod.night_start_time),
            "light_ramp_up_minutes": request.photoperiod.ramp_up_minutes,
            "light_ramp_down_minutes": request.photoperiod.ramp_down_minutes,
        }
        if prior_parameters is not None:
            return {**dict(prior_parameters), **clocks}
        return {
            "location": location,
            "cluster": cluster,
            "mode_id": request.mode_id,
            "submode_id": request.submode_id,
            **clocks,
            "main_light_intensity": 100,
            "supplemental_light_intensity": 0,
        }

    async def _update_or_insert_mode_parameters(
        self,
        connection: TimelineApplyConnection,
        location: str,
        cluster: str,
        request: TimelineApplyRequest,
    ) -> None:
        """Update the exact profile parameter row; insert it when absent.

        An existing row keeps its stored non-timeline values verbatim while the
        timeline-owned clocks and ramps are updated. A missing row is created in
        this transaction with the request's clocks/ramps and the existing
        default 100/0 deprecated intensity columns.
        """
        day_start = _parse_time_text(request.photoperiod.day_start_time)
        night_start = _parse_time_text(request.photoperiod.night_start_time)
        ramps = (request.photoperiod.ramp_up_minutes, request.photoperiod.ramp_down_minutes)
        parameter_id = await connection.fetchval(
            _UPDATE_PHOTOPERIOD_QUERY,
            day_start,
            night_start,
            *ramps,
            location,
            cluster,
            request.mode_id,
            request.submode_id,
        )
        if parameter_id is not None:
            return
        _ = await connection.execute(
            _INSERT_PARAMETER_QUERY,
            location,
            cluster,
            request.mode_id,
            request.submode_id,
            day_start,
            night_start,
            *ramps,
        )


def _revision(version_id: int) -> str:
    """Encode the existing monotonic configuration cursor for API comparison."""
    return f"{version_id:07x}"


_CURRENT_REVISION_QUERY: Final = "SELECT COALESCE(MAX(version_id), 0) FROM config_versions"
_TIMELINE_APPLY_LOCK: Final = 7_281_992

_LOCK_ACTIVE_ROW_QUERY = """
    SELECT mode_id, submode_id FROM room_active_mode
    WHERE location = $1 AND cluster = $2
    FOR UPDATE
"""

_SELECT_PARAMETERS_QUERY = """
    SELECT * FROM mode_parameters
    WHERE location = $1 AND cluster = $2 AND mode_id = $3
      AND submode_id IS NOT DISTINCT FROM $4
"""

_DELETE_PERIODS_QUERY = """
    DELETE FROM climate_periods
    WHERE location = $1 AND cluster = $2 AND mode_id = $3
      AND submode_id IS NOT DISTINCT FROM $4
"""

_INSERT_PERIOD_QUERY = """
    INSERT INTO climate_periods (
        location, cluster, mode_id, submode_id, period_name, start_time, end_time,
        ramp_minutes, heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint, details, updated_at
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, NOW())
"""

_UPDATE_PHOTOPERIOD_QUERY = """
    UPDATE mode_parameters
    SET day_start_time = $1, night_start_time = $2,
        light_ramp_up_minutes = $3, light_ramp_down_minutes = $4, updated_at = NOW()
    WHERE location = $5 AND cluster = $6 AND mode_id = $7
      AND submode_id IS NOT DISTINCT FROM $8
    RETURNING id
"""

_INSERT_PARAMETER_QUERY = """
    INSERT INTO mode_parameters (
        location, cluster, mode_id, submode_id,
        day_start_time, night_start_time,
        light_ramp_up_minutes, light_ramp_down_minutes,
        main_light_intensity, supplemental_light_intensity, updated_at
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 100, 0, NOW())
"""

_INSERT_VERSION_QUERY = """
    INSERT INTO config_versions (timestamp, config_type, location, cluster, changes)
    VALUES (NOW(), $1, $2, $3, $4::jsonb)
    RETURNING version_id
"""
