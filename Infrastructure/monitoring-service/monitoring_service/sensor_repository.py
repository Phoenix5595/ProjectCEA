"""Read-only, watermark-safe sensor monitoring queries."""

from __future__ import annotations

from datetime import timedelta, UTC, datetime
import os
from math import ceil
from typing import Final, Protocol

import asyncpg

from monitoring_service.sensor_models import (
    LiveSensorValue,
    MonitoringRange,
    MonitoringUnavailableError,
    SensorSeries,
    SensorStatistics,
    StddevQuality,
    Tier,
    resolve_tier,
    resolve_interval_seconds,
    resolve_room_metadata,
    source_bucket_seconds,
)
from monitoring_service.cagg_watermarks import CaggWatermarkCache, watermark_sql
from monitoring_service.query_observation import request_observation
from monitoring_service.sensor_rows import node_mapping_args, series_from_rows, statistics_from_rows
from monitoring_service.sensor_sql import (
    CAGGS as _CAGGS,
    RAW_BUCKETED_SERIES_SQL,
    RAW_SERIES_SQL as _RAW_SERIES_SQL,
    STATISTICS_CAGG_SQL as _STATISTICS_CAGG_SQL,
    STATISTICS_SQL as _STATISTICS_SQL,
    TIERED_BUCKETED_STATEMENTS,
    TIERED_STATEMENTS as _TIERED_STATEMENTS,
)
from shared.cluster_topology import sensor_name_like_pattern

_LIVE_MAX_AGE: Final[timedelta] = timedelta(
    seconds=float(os.getenv("MONITORING_LIVE_STALENESS_SECONDS", "900"))
)
STATS_CAGG_MIN_DURATION: Final[timedelta] = timedelta(hours=48)
_CAGG_RELATIONS: Final[frozenset[str]] = frozenset(relation for relation, _ in _CAGGS.values())


class _SensorDatabase(Protocol):
    async def fetch(
        self, query: str, *args: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]: ...


class _SensorRedis(Protocol):
    async def sensor_values(
        self, pattern: str
    ) -> tuple[tuple[str, str | None, str | None], ...]: ...


class SensorMonitoringRepository:
    """Execute sensor chart queries through the service-owned read clients."""

    def __init__(
        self,
        database: _SensorDatabase,
        redis: _SensorRedis,
        watermark_cache: CaggWatermarkCache | None = None,
    ) -> None:
        self._database = database
        self._redis = redis
        self._watermark_cache = (
            CaggWatermarkCache(_CAGG_RELATIONS) if watermark_cache is None else watermark_cache
        )

    async def series(
        self, room: str, monitoring_range: MonitoringRange, max_points: int | None = None
    ) -> tuple[Tier, tuple[SensorSeries, ...]]:
        metadata = resolve_room_metadata(room)
        tier = resolve_tier(monitoring_range.duration, max_points)
        node_mapping = node_mapping_args(room, metadata.nodes)
        async with request_observation(tier=tier.value):
            complete_bucket_upper_bound = (
                None
                if tier is Tier.RAW
                else await self._require_cagg_coverage(tier, monitoring_range)
            )
            interval = (
                None
                if max_points is None
                else timedelta(
                    seconds=resolve_interval_seconds(
                        ceil(monitoring_range.duration.total_seconds()),
                        max_points,
                        source_bucket_seconds(tier),
                    )
                )
            )
            if interval is None and tier is Tier.RAW:
                rows = await self._database.fetch(
                    _RAW_SERIES_SQL,
                    room,
                    *node_mapping,
                    monitoring_range.start,
                    monitoring_range.end,
                )
            elif interval is not None and tier is Tier.RAW:
                rows = await self._database.fetch(
                    RAW_BUCKETED_SERIES_SQL,
                    room,
                    *node_mapping,
                    monitoring_range.start,
                    monitoring_range.end,
                    interval,
                )
            elif interval is None:
                assert complete_bucket_upper_bound is not None
                rows = await self._database.fetch(
                    _TIERED_STATEMENTS[tier.value],
                    room,
                    *node_mapping,
                    monitoring_range.start,
                    monitoring_range.end,
                    complete_bucket_upper_bound,
                )
            else:
                assert complete_bucket_upper_bound is not None
                rows = await self._database.fetch(
                    TIERED_BUCKETED_STATEMENTS[tier.value],
                    room,
                    *node_mapping,
                    monitoring_range.start,
                    monitoring_range.end,
                    interval,
                    complete_bucket_upper_bound,
                )
            return tier, series_from_rows(rows, metadata.nodes)

    async def _require_cagg_coverage(
        self, tier: Tier, monitoring_range: MonitoringRange
    ) -> datetime:
        relation, _ = _CAGGS[tier]

        async def fetch() -> datetime:
            rows = await self._database.fetch(watermark_sql(relation))
            if not rows:
                raise MonitoringUnavailableError(f"required CAGG is unavailable: {relation}")
            watermark = rows[0]["materialization_watermark"]
            if not isinstance(watermark, datetime):
                raise MonitoringUnavailableError(f"required CAGG lacks coverage: {relation}")
            return watermark

        watermark = await self._watermark_cache.get(relation, fetch)
        if watermark <= monitoring_range.start:
            raise MonitoringUnavailableError(
                f"required CAGG lacks coverage for this range: {relation}"
            )
        return watermark - timedelta(seconds=source_bucket_seconds(tier))

    async def statistics(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[SensorStatistics, ...]:
        metadata = resolve_room_metadata(room)
        node_mapping = node_mapping_args(room, metadata.nodes)
        statement = (
            _STATISTICS_CAGG_SQL
            if monitoring_range.duration > STATS_CAGG_MIN_DURATION
            else _STATISTICS_SQL
        )
        stddev_quality = StddevQuality.EXACT
        tier = "5min" if statement == _STATISTICS_CAGG_SQL else "raw"
        async with request_observation(tier=tier):
            rows = await self._database.fetch(
                statement,
                room,
                *node_mapping,
                monitoring_range.start,
                monitoring_range.end,
            )
            return statistics_from_rows(rows, metadata.nodes, stddev_quality)

    async def live(self, room: str, node: str) -> tuple[LiveSensorValue, ...]:
        metadata = resolve_room_metadata(room)
        if node not in {item.value for item in metadata.nodes}:
            from monitoring_service.sensor_models import MonitoringValidationError

            raise MonitoringValidationError(f"node is not valid for {room}: {node}")
        pattern = sensor_name_like_pattern(room, node)
        values = await self._redis.sensor_values(
            "cea:sensor:*:*:*"
            if pattern is None
            else f"cea:sensor:*:*:{pattern.replace('%', '*').replace('_', '?')}"
        )
        result: list[LiveSensorValue] = []
        for key, value, timestamp in values:
            if value is None:
                continue
            try:
                observed_at = (
                    datetime.now(UTC)
                    if timestamp is None
                    else datetime.fromtimestamp(int(timestamp) / 1000, UTC)
                )
                if observed_at < datetime.now(UTC) - _LIVE_MAX_AGE:
                    continue  # stale last-good: sensor considered disconnected
                result.append(
                    LiveSensorValue(
                        sensor=key.rsplit(":", maxsplit=1)[-1],
                        value=float(value),
                        timestamp=observed_at,
                    )
                )
            except (OverflowError, TypeError, ValueError):
                continue
        return tuple(result)
