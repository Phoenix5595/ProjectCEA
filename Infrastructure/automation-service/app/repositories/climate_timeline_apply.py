"""Atomic persistence for the timeline-owned climate configuration aggregate."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, time
import json
from types import TracebackType
from typing import Final, Protocol, TypeAlias, final

from typing_extensions import override

from app.schemas.climate_timeline import TimelineApplyRequest

SqlArgument: TypeAlias = str | int | float | time | None


class TimelineApplyConnection(Protocol):
    """The transaction-scoped asyncpg operations owned by Apply."""

    def transaction(self) -> AbstractAsyncContextManager[None]: ...

    async def execute(self, query: str, *args: SqlArgument) -> str: ...

    async def fetchval(self, query: str, *args: SqlArgument) -> int | None: ...

    async def fetchrow(self, query: str, *args: SqlArgument) -> TimelineVersionRow | None: ...


class TimelineVersionRow(Protocol):
    def __getitem__(self, column: str) -> int: ...


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


@final
class TimelineApplyRepository:
    """Replace periods and timeline-owned photoperiod fields through one connection."""

    def __init__(self, pool: TimelineApplyPool) -> None:
        self._pool: TimelineApplyPool = pool

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

            updated_parameter_id = await connection.fetchval(
                _UPDATE_PHOTOPERIOD_QUERY,
                request.photoperiod.day_start_time,
                request.photoperiod.night_start_time,
                request.photoperiod.ramp_up_minutes,
                request.photoperiod.ramp_down_minutes,
                location,
                cluster,
                request.mode_id,
                request.submode_id,
            )
            if updated_parameter_id is None:
                raise RuntimeError("timeline mode parameters are missing")

            row = await connection.fetchrow(
                _INSERT_VERSION_QUERY,
                "climate_timeline",
                location,
                cluster,
                json.dumps({"mode_id": request.mode_id, "submode_id": request.submode_id}),
            )
            if row is None:
                raise RuntimeError("timeline revision was not recorded")
            return TimelineApplyCommit(_revision(row["version_id"]))


def _revision(version_id: int) -> str:
    """Encode the existing monotonic configuration cursor for API comparison."""
    return f"{version_id:07x}"


_CURRENT_REVISION_QUERY: Final = "SELECT COALESCE(MAX(version_id), 0) FROM config_versions"
_TIMELINE_APPLY_LOCK: Final = 7_281_992

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

_INSERT_VERSION_QUERY = """
    INSERT INTO config_versions (timestamp, config_type, location, cluster, changes)
    VALUES (NOW(), $1, $2, $3, $4::jsonb)
    RETURNING version_id
"""
