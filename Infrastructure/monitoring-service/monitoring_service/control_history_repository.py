"""Read-only control history repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Protocol, final

import asyncpg

from monitoring_service.cagg_watermarks import CaggWatermarkCache, watermark_sql
from monitoring_service.control_history_queries import (
    PHOTOPERIOD_COVERAGE_SQL,
    PHOTOPERIOD_LIGHT_PREDECESSORS_SQL,
    PHOTOPERIOD_SQL,
    _RAW_SETPOINTS_SQL,
    _STATE_RAW_SQL,
    select_control_history_sources,
)
from monitoring_service.control_models import (
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlReadScope,
)
from monitoring_service.control_timeline_build import build_control_history_envelope
from monitoring_service.query_observation import request_observation
from monitoring_service.sensor_models import (
    MonitoringUnavailableError,
    derive_interval_seconds,
)
from shared.history_availability import local_history_scope

_CONTROL_CAGG_RELATIONS = frozenset(
    {
        "monitoring_effective_setpoints_1min",
        "monitoring_effective_setpoints_5min",
        "monitoring_automation_state_1min",
        "monitoring_automation_state_5min",
    }
)


class ControlHistoryDatabase(Protocol):
    """The parameterized read capability required for recorded history."""

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> Sequence[Mapping[str, object] | asyncpg.Record]: ...


async def _materialization_watermark(
    database: ControlHistoryDatabase,
    cache: CaggWatermarkCache,
    relation: str,
) -> datetime:
    async def fetch() -> datetime:
        rows = await database.fetch(watermark_sql(relation))
        value = rows[0].get("materialization_watermark") if rows else None
        if not isinstance(value, datetime) or value.utcoffset() is None:
            return datetime.min.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    return await cache.get(relation, fetch)


async def _fetch_watermark_split(
    database: ControlHistoryDatabase,
    cache: CaggWatermarkCache,
    relation: str,
    aggregate_sql: str,
    raw_sql: str,
    location: str,
    start: datetime,
    end: datetime,
) -> list[Mapping[str, object] | asyncpg.Record]:
    watermark = await _materialization_watermark(database, cache, relation)
    split = min(end, max(start, watermark))
    rows: list[Mapping[str, object] | asyncpg.Record] = []
    if start < split:
        rows.extend(await database.fetch(aggregate_sql, location, start, split))
    if split < end:
        rows.extend(await database.fetch(raw_sql, location, split, end))
    return rows


@final
class ControlHistoryRepository:
    """Load recorded control timelines solely from committed read-model facts."""

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
        history_range: ControlHistoryRange,
        max_points: int | None = None,
        scope: ControlReadScope = "all",
    ) -> ControlHistoryEnvelope:
        """Return the selected timelines for the window.

        ``all`` reads climate, light, device, PID, and photoperiod facts; the
        ``lights`` scope reads only recorded light intensity and its
        photoperiod metadata, leaving the climate, device and PID timelines
        empty instead of querying unavailable automation sources.
        """
        sources = select_control_history_sources(history_range, max_points, scope)
        try:
            async with (
                request_observation(),
                local_history_scope(
                    location,
                    history_range.start,
                    history_range.end,
                    (
                        "effective_setpoints",
                        "monitoring_room_photoperiod",
                        "monitoring_photoperiod_coverage",
                    )
                    if scope == "lights"
                    else (
                        "effective_setpoints",
                        "automation_state",
                        "monitoring_room_photoperiod",
                        "monitoring_photoperiod_coverage",
                    ),
                ),
            ):
                setpoint_rows: Sequence[Mapping[str, object] | asyncpg.Record] = ()
                state_rows: Sequence[Mapping[str, object] | asyncpg.Record] = ()
                if sources.setpoints_sql is not None:
                    if sources.setpoints_relation is None:
                        setpoint_rows = await self._database.fetch(
                            sources.setpoints_sql,
                            location,
                            history_range.start,
                            history_range.end,
                        )
                    else:
                        setpoint_rows = await _fetch_watermark_split(
                            self._database,
                            self._watermark_cache,
                            sources.setpoints_relation,
                            sources.setpoints_sql,
                            _RAW_SETPOINTS_SQL,
                            location,
                            history_range.start,
                            history_range.end,
                        )
                light_rows = await self._database.fetch(
                    sources.light_sql, location, history_range.start, history_range.end
                )
                if sources.state_sql is not None:
                    if sources.state_relation is None:
                        state_rows = await self._database.fetch(
                            sources.state_sql,
                            location,
                            history_range.start,
                            history_range.end,
                        )
                    else:
                        state_rows = await _fetch_watermark_split(
                            self._database,
                            self._watermark_cache,
                            sources.state_relation,
                            sources.state_sql,
                            _STATE_RAW_SQL,
                            location,
                            history_range.start,
                            history_range.end,
                        )
                photoperiod_rows = await self._database.fetch(
                    PHOTOPERIOD_SQL, location, history_range.start, history_range.end
                )
                photoperiod_coverage_rows = await self._database.fetch(
                    PHOTOPERIOD_COVERAGE_SQL, location, history_range.start, history_range.end
                )
                photoperiod_light_predecessors = await self._database.fetch(
                    PHOTOPERIOD_LIGHT_PREDECESSORS_SQL, location, history_range.start
                )
                return build_control_history_envelope(
                    history_range,
                    setpoint_rows,
                    light_rows,
                    state_rows,
                    photoperiod_rows,
                    sources.setpoints_are_aggregated,
                    max_points,
                    None
                    if scope == "lights"
                    else derive_interval_seconds(
                        history_range.end - history_range.start,
                        sources.source_interval_seconds,
                        max_points,
                    ),
                    photoperiod_coverage_rows=photoperiod_coverage_rows,
                    photoperiod_light_predecessors=photoperiod_light_predecessors,
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
            raise MonitoringUnavailableError("control history database is unavailable") from exc
