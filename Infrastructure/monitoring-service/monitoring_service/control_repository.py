"""Read-only control history and shared-publication repositories."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, final

import asyncpg
from pydantic import ValidationError

from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.redis_resources import RedisReadClient
from monitoring_service.control_history_queries import (
    PHOTOPERIOD_SQL,
    select_control_history_sources,
)
from monitoring_service.control_models import (
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlPublicationResponse,
    CurrentPublicationResponse,
    ProjectionPublicationResponse,
)
from monitoring_service.control_timeline_build import build_control_history_envelope
from shared.monitoring_contracts import (
    CurrentSnapshot,
    FutureProjection,
    MonitoringContractViolation,
    Quality,
    validate_projection_timeline,
)
from shared.redis_keys import (
    monitoring_current_publication_key,
    monitoring_future_publication_key,
)

from monitoring_service.query_observation import request_observation
from monitoring_service.sensor_models import derive_interval_seconds


class ControlHistoryDatabase(Protocol):
    """The parameterized read capability required for recorded history."""

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> Sequence[Mapping[str, str | float | int | datetime | None] | asyncpg.Record]: ...


class PublicationRedis(Protocol):
    """The atomic multi-key read capability required for shared publications."""

    def mget(self, keys: list[str]) -> Awaitable[list[str | None]]: ...


@final
class ControlHistoryRepository:
    """Load recorded control timelines solely from committed read-model facts."""

    def __init__(self, database: ControlHistoryDatabase) -> None:
        self._database = database

    async def read(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        """Return climate, light, device, PID, and photoperiod timelines for the window."""
        sources = select_control_history_sources(history_range, max_points)
        async with request_observation():
            setpoint_rows = await self._database.fetch(
                sources.setpoints_sql, location, history_range.start, history_range.end
            )
            state_rows = await self._database.fetch(
                sources.state_sql, location, history_range.start, history_range.end
            )
            photoperiod_rows = await self._database.fetch(
                PHOTOPERIOD_SQL, location, history_range.start, history_range.end
            )
            return build_control_history_envelope(
                history_range,
                setpoint_rows,
                state_rows,
                photoperiod_rows,
                sources.setpoints_are_aggregated,
                max_points,
                derive_interval_seconds(
                    history_range.end - history_range.start,
                    sources.source_interval_seconds,
                    max_points,
                ),
            )


@final
class ControlPublicationRepository:
    """Read paired current and future facts without recalculating automation state."""

    def __init__(
        self, redis: PublicationRedis, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._redis = redis
        self._clock = clock or (lambda: datetime.now(UTC))

    async def read(self, location: str) -> ControlPublicationResponse:
        """Return publications only when both authorities parse, share one version, and are valid."""
        current_payload, future_payload = await self._redis.mget(_publication_keys(location))
        current = _parse_current(current_payload)
        future = _parse_future(future_payload)
        if (
            current is None
            or future is None
            or any(item.version != current.version for item in future)
        ):
            return _unavailable_publication()
        now = self._clock()
        if current.valid_until <= now or any(item.valid_until <= now for item in future):
            return _unavailable_publication()
        return ControlPublicationResponse(
            current=CurrentPublicationResponse(quality=Quality.EXACT, value=current),
            projection=ProjectionPublicationResponse(quality=Quality.ESTIMATED, value=future),
        )


def _publication_keys(location: str) -> list[str]:
    return [
        monitoring_current_publication_key(location),
        monitoring_future_publication_key(location),
    ]


def _parse_current(payload: str | None) -> CurrentSnapshot | None:
    if payload is None:
        return None
    try:
        return CurrentSnapshot.model_validate_json(payload)
    except ValidationError:
        return None


def _parse_future(payload: str | None) -> tuple[FutureProjection, ...] | None:
    """Normalize legacy single-object or versioned array payloads into a timeline."""
    if payload is None:
        return None
    try:
        decoded = json.loads(payload)
    except ValueError:
        return None
    try:
        items = decoded if isinstance(decoded, list) else [decoded]
        projections = tuple(
            FutureProjection.model_validate_json(json.dumps(item)) for item in items
        )
        return validate_projection_timeline(projections)
    except (ValidationError, MonitoringContractViolation):
        return None


def _unavailable_publication() -> ControlPublicationResponse:
    return ControlPublicationResponse(
        current=CurrentPublicationResponse(quality=Quality.UNAVAILABLE, value=None),
        projection=ProjectionPublicationResponse(quality=Quality.UNAVAILABLE, value=()),
    )


class RuntimeReadResources(Protocol):
    """Expose owned read clients while the application lifespan is active."""

    database: ReadOnlyDatabase | None
    redis_client: RedisReadClient | None


@final
class RuntimeControlReads:
    """Connect control repositories to the monitoring service's owned read clients."""

    def __init__(self, resources: RuntimeReadResources) -> None:
        self._resources = resources

    async def history(
        self, location: str, history_range: ControlHistoryRange, max_points: int | None = None
    ) -> ControlHistoryEnvelope:
        """Read recorded history only when the service database client is available."""
        database = self._resources.database
        if database is None:
            raise RuntimeError("monitoring database resource is unavailable")
        return await ControlHistoryRepository(database).read(location, history_range, max_points)

    async def publications(self, location: str) -> ControlPublicationResponse:
        """Read shared publications only when the service Redis client is available."""
        redis = self._resources.redis_client
        if redis is None:
            raise RuntimeError("monitoring Redis resource is unavailable")
        return await ControlPublicationRepository(redis).read(location)
