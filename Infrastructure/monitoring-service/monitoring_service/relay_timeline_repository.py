"""Read-only relay timeline repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import final

import asyncpg

from monitoring_service.cagg_watermarks import CaggWatermarkCache
from monitoring_service.control_history_repository import (
    ControlHistoryDatabase,
    _CONTROL_CAGG_RELATIONS,
    _materialization_watermark,
)
from monitoring_service.control_models import (
    RelayTimelineLoadPoint,
    RelayTimelineRange,
    RelayTimelineResponse,
    RelayTimelineTransition,
)
from monitoring_service.query_observation import request_observation
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
from monitoring_service.sensor_models import MonitoringUnavailableError
from shared.history_availability import local_history_scope


@final
class RelayTimelineRepository:
    """Read exact relay facts plus event-time-associated requested PID output."""

    def __init__(
        self,
        database: ControlHistoryDatabase,
        watermark_cache: CaggWatermarkCache | None = None,
    ) -> None:
        self._database = database
        self._watermark_cache = (
            CaggWatermarkCache(_CONTROL_CAGG_RELATIONS)
            if watermark_cache is None
            else watermark_cache
        )

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
            async with (
                request_observation(),
                local_history_scope(
                    location,
                    history_range.start,
                    history_range.end,
                    ("relay_observation", "automation_state"),
                ),
            ):
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
                            last_at=last_transition.time,
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
            return await self._read_aggregated_load(
                location,
                history_range,
                watermark,
                "monitoring_automation_state_1min",
                RELAY_1MIN_PID_LOAD_SQL,
                60,
            )
        return await self._read_aggregated_load(
            location,
            history_range,
            watermark,
            "monitoring_automation_state_5min",
            RELAY_5MIN_PID_LOAD_SQL,
            300,
        )

    async def _read_aggregated_load(
        self,
        location: str,
        history_range: RelayTimelineRange,
        watermark: int,
        relation: str,
        aggregate_sql: str,
        interval_seconds: int,
    ) -> tuple[tuple[RelayTimelineLoadPoint, ...], bool]:
        frontier = await _materialization_watermark(self._database, self._watermark_cache, relation)
        split = min(history_range.end, max(history_range.start, frontier))
        aggregate_rows: Sequence[Mapping[str, object] | asyncpg.Record] = ()
        raw_rows: Sequence[Mapping[str, object] | asyncpg.Record] = ()
        if history_range.start < split:
            aggregate_rows = await self._database.fetch(
                aggregate_sql,
                watermark,
                history_range.start,
                split,
                location,
                MAX_RELAY_LOAD_ROWS + 1,
            )
        if split < history_range.end:
            raw_rows = await self._database.fetch(
                RELAY_RAW_PID_LOAD_SQL,
                watermark,
                split,
                history_range.end,
                location,
                MAX_RELAY_LOAD_ROWS + 1,
            )
        aggregate_points, aggregate_truncated = aggregate_load_points(
            aggregate_rows, interval_seconds
        )
        raw_points, raw_truncated = raw_load_points(raw_rows)
        points = sorted(
            (*aggregate_points, *raw_points),
            key=lambda point: (
                point.time,
                point.device_id if point.device_id is not None else -1,
                point.device_name,
            ),
        )
        truncated = aggregate_truncated or raw_truncated or len(points) > MAX_RELAY_LOAD_ROWS
        return tuple(points[:MAX_RELAY_LOAD_ROWS]), truncated


def _relay_transition(row: Mapping[str, object]) -> RelayTimelineTransition:
    fields = (
        "observation_id",
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
    values = {field: row[field] for field in fields}
    values["time"] = row["observed_at"]
    return RelayTimelineTransition.model_validate(values)


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
