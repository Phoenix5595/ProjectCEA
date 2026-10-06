"""Read-only control history and shared-publication repositories."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
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
    RelayTimelineLoadPoint,
    RelayTimelineRange,
    RelayTimelineResponse,
    RelayTimelineTransition,
)
from monitoring_service.relay_timeline import (
    MAX_RELAY_LOAD_ROWS,
    MAX_RELAY_TIMELINE_LIMIT,
    RELAY_1MIN_PID_LOAD_SQL,
    RELAY_5MIN_PID_LOAD_SQL,
    RELAY_CHANNEL_ANCHORS_SQL,
    RELAY_COVERAGE_SUMMARY_SQL,
    RELAY_HEARTBEAT_HISTORY_SQL,
    RELAY_RAW_PID_LOAD_SQL,
    RELAY_TRANSITIONS_AFTER_CURSOR_SQL,
    RELAY_TRANSITIONS_FIRST_PAGE_SQL,
    RELAY_WATERMARK_SQL,
    RelayTimelineCursor,
    aggregate_load_points,
    coverage_from_rows,
    decode_cursor,
    encode_cursor,
    raw_load_points,
)
from monitoring_service.rich_trajectory_models import RichTrajectoryEnvelope
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
    monitoring_rich_trajectory_key,
)

from monitoring_service.query_observation import request_observation
from monitoring_service.sensor_models import (
    MonitoringUnavailableError,
    derive_interval_seconds,
)


class ControlHistoryDatabase(Protocol):
    """The parameterized read capability required for recorded history."""

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> Sequence[Mapping[str, object] | asyncpg.Record]: ...


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
            light_rows = await self._database.fetch(
                sources.light_sql, location, history_range.start, history_range.end
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
                light_rows,
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
class RelayTimelineRepository:
    """Read exact relay facts plus event-time-associated requested PID output."""

    def __init__(self, database: ControlHistoryDatabase) -> None:
        self._database = database

    async def read(
        self,
        location: str,
        history_range: RelayTimelineRange,
        limit: int,
        cursor: str | None = None,
    ) -> RelayTimelineResponse:
        """Return one stable page; coarse aggregates are used only for PID output."""
        if not 1 <= limit <= MAX_RELAY_TIMELINE_LIMIT:
            raise ValueError("relay timeline limit is outside the supported range")
        cursor_state = (
            decode_cursor(cursor, location=location, history_range=history_range)
            if cursor is not None
            else None
        )
        try:
            async with request_observation():
                if cursor_state is None:
                    watermark_rows = await self._database.fetch(RELAY_WATERMARK_SQL)
                    watermark = (
                        _integer_value(watermark_rows[0].get("watermark")) if watermark_rows else 0
                    )
                else:
                    watermark = cursor_state.watermark

                if cursor_state is None:
                    page_rows = await self._database.fetch(
                        RELAY_TRANSITIONS_FIRST_PAGE_SQL,
                        watermark,
                        history_range.start,
                        history_range.end,
                        location,
                        limit + 1,
                    )
                else:
                    page_rows = await self._database.fetch(
                        RELAY_TRANSITIONS_AFTER_CURSOR_SQL,
                        watermark,
                        history_range.start,
                        history_range.end,
                        location,
                        cursor_state.last_at,
                        cursor_state.last_id,
                        limit + 1,
                    )
                has_more = len(page_rows) > limit
                transitions = tuple(_relay_transition(row) for row in page_rows[:limit])

                if cursor_state is None:
                    heartbeat_rows = await self._database.fetch(
                        RELAY_HEARTBEAT_HISTORY_SQL,
                        watermark,
                        history_range.start,
                        history_range.end,
                    )
                    coverage_rows = await self._database.fetch(
                        RELAY_COVERAGE_SUMMARY_SQL,
                        watermark,
                        history_range.start,
                        location,
                        history_range.end,
                    )
                    channel_anchor_rows = await self._database.fetch(
                        RELAY_CHANNEL_ANCHORS_SQL,
                        history_range.start,
                        watermark,
                        location,
                    )
                    channel_anchors = tuple(_relay_transition(row) for row in channel_anchor_rows)
                    prior_heartbeats = [
                        row
                        for row in heartbeat_rows
                        if isinstance(row.get("observed_at"), datetime)
                        and row["observed_at"] < history_range.start
                    ]
                    anchors = channel_anchors
                    if prior_heartbeats:
                        anchors += (_relay_transition(prior_heartbeats[-1]),)
                    load, load_truncated = await self._read_load(location, history_range, watermark)
                    last_heartbeat_at = _latest_heartbeat_at(heartbeat_rows, history_range.end)
                    coverage_complete = (
                        coverage_from_rows(
                            heartbeat_rows,
                            coverage_rows[0] if coverage_rows else None,
                            channel_anchors,
                            history_range,
                        )
                        and not load_truncated
                    )
                else:
                    anchors = ()
                    load = ()
                    last_heartbeat_at = cursor_state.last_heartbeat_at
                    coverage_complete = cursor_state.coverage_complete

                next_cursor = None
                if has_more and transitions:
                    last_transition = transitions[-1]
                    next_cursor = encode_cursor(
                        RelayTimelineCursor(
                            location=location,
                            start=history_range.start,
                            end=history_range.end,
                            last_at=last_transition.observed_at,
                            last_id=last_transition.observation_id,
                            watermark=watermark,
                            last_heartbeat_at=last_heartbeat_at,
                            coverage_complete=coverage_complete,
                        )
                    )
                return RelayTimelineResponse(
                    range=history_range,
                    transitions=transitions,
                    anchors=anchors,
                    load=load,
                    coverage_complete=coverage_complete,
                    last_heartbeat_at=last_heartbeat_at,
                    watermark=watermark,
                    has_more=has_more,
                    next_cursor=next_cursor,
                )
        except MonitoringUnavailableError:
            raise
        except (
            asyncpg.PostgresError,
            ConnectionError,
            OSError,
            RuntimeError,
            TimeoutError,
        ) as exc:
            raise MonitoringUnavailableError("relay timeline database is unavailable") from exc

    async def _read_load(
        self,
        location: str,
        history_range: RelayTimelineRange,
        watermark: int,
    ) -> tuple[tuple[RelayTimelineLoadPoint, ...], bool]:
        """Fetch only the requested-output source selected for this range."""
        duration = history_range.end - history_range.start
        arguments = (
            watermark,
            history_range.start,
            history_range.end,
            location,
        )
        if duration <= timedelta(hours=1):
            rows = await self._database.fetch(
                RELAY_RAW_PID_LOAD_SQL,
                *arguments,
                MAX_RELAY_LOAD_ROWS + 1,
            )
            return raw_load_points(rows)
        if duration < timedelta(hours=24):
            rows = await self._database.fetch(
                RELAY_1MIN_PID_LOAD_SQL,
                *arguments,
                MAX_RELAY_LOAD_ROWS + 1,
            )
            return aggregate_load_points(rows, 60)
        rows = await self._database.fetch(
            RELAY_5MIN_PID_LOAD_SQL,
            *arguments,
            MAX_RELAY_LOAD_ROWS + 1,
        )
        return aggregate_load_points(rows, 300)


def _relay_transition(row: Mapping[str, object]) -> RelayTimelineTransition:
    fields = (
        "observation_id",
        "observed_at",
        "channel",
        "observed_state",
        "reason",
        "session_id",
        "registry_version",
        "device_id",
        "device_name",
        "device_type",
        "location",
        "cluster",
    )
    return RelayTimelineTransition.model_validate({field: row[field] for field in fields})


def _integer_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _latest_heartbeat_at(
    heartbeat_rows: Sequence[Mapping[str, object]], end: datetime
) -> datetime | None:
    observed = [
        row["observed_at"]
        for row in heartbeat_rows
        if isinstance(row.get("observed_at"), datetime) and row["observed_at"] < end
    ]
    return max(observed) if observed else None


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
        payloads = await self._redis.mget(_publication_keys(location))
        current_payload = payloads[0] if len(payloads) > 0 else None
        future_payload = payloads[1] if len(payloads) > 1 else None
        rich_payload = payloads[2] if len(payloads) > 2 else None
        current = _parse_current(current_payload)
        future = _parse_future(future_payload)
        trajectory = _parse_trajectory(rich_payload, current)
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
            projection=ProjectionPublicationResponse(
                quality=Quality.ESTIMATED, value=future, trajectory=trajectory
            ),
        )


def _publication_keys(location: str) -> list[str]:
    return [
        monitoring_current_publication_key(location),
        monitoring_future_publication_key(location),
        monitoring_rich_trajectory_key(location),
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


def _parse_trajectory(
    payload: str | None, current: CurrentSnapshot | None
) -> RichTrajectoryEnvelope | None:
    if payload is None:
        return None
    try:
        trajectory = RichTrajectoryEnvelope.model_validate_json(payload, strict=False)
    except ValidationError:
        return None
    if trajectory.revision_scope != "saved" or current is None:
        return None
    return (
        trajectory
        if trajectory.base_config_revision == f"{current.version.config_version:07x}"
        else None
    )


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

    async def relay_timeline(
        self,
        location: str,
        history_range: RelayTimelineRange,
        limit: int,
        cursor: str | None = None,
    ) -> RelayTimelineResponse:
        """Read physical relay timelines from the owned monitoring database client."""
        database = self._resources.database
        if database is None:
            raise RuntimeError("monitoring database resource is unavailable")
        return await RelayTimelineRepository(database).read(location, history_range, limit, cursor)

    async def publications(self, location: str) -> ControlPublicationResponse:
        """Read shared publications only when the service Redis client is available."""
        redis = self._resources.redis_client
        if redis is None:
            raise RuntimeError("monitoring Redis resource is unavailable")
        return await ControlPublicationRepository(redis).read(location)
