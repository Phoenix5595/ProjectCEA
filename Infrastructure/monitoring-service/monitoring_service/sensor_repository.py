"""Read-only, watermark-safe sensor monitoring queries."""

from __future__ import annotations

import re

from collections import defaultdict
from collections.abc import Sequence
from datetime import timedelta, UTC, datetime
import os
from typing import Final, Protocol

import asyncpg

from monitoring_service.sensor_models import (
    LiveSensorValue,
    MonitoringRange,
    MonitoringUnavailableError,
    Node,
    SensorSeries,
    SensorStatistics,
    SeriesPoint,
    Tier,
    UnitFamily,
    compute_tier,
    resolve_room_metadata,
)
from shared.cluster_topology import sensor_name_like_pattern

_LIVE_MAX_AGE: Final[timedelta] = timedelta(
    seconds=float(os.getenv("MONITORING_LIVE_STALENESS_SECONDS", "900"))
)
STATS_CAGG_MIN_DURATION: Final[timedelta] = timedelta(hours=48)

_CAGGS: Final = {
    Tier.ONE_MINUTE: ("monitoring_measurement_1min", "1 minute"),
    Tier.FIVE_MINUTES: ("monitoring_measurement_5min", "5 minutes"),
}


class _SensorDatabase(Protocol):
    async def fetch(
        self, query: str, *args: str | int | float | datetime
    ) -> list[asyncpg.Record]: ...


class _SensorRedis(Protocol):
    async def sensor_values(
        self, pattern: str
    ) -> tuple[tuple[str, str | None, str | None], ...]: ...


class SensorMonitoringRepository:
    """Execute sensor chart queries through the service-owned read clients."""

    def __init__(self, database: _SensorDatabase, redis: _SensorRedis) -> None:
        self._database = database
        self._redis = redis

    async def series(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[Tier, tuple[SensorSeries, ...]]:
        metadata = resolve_room_metadata(room)
        tier = compute_tier(monitoring_range.duration)
        if tier is not Tier.RAW:
            await self._require_cagg_coverage(tier, monitoring_range)
        result: list[SensorSeries] = []
        for node in metadata.nodes:
            pattern = sensor_name_like_pattern(room, node.value) or ""
            if tier is Tier.RAW:
                rows = await self._database.fetch(
                    _RAW_SERIES_SQL, room, pattern, monitoring_range.start, monitoring_range.end
                )
            else:
                rows = await self._database.fetch(
                    _TIERED_STATEMENTS[tier.value],
                    room,
                    pattern,
                    monitoring_range.start,
                    monitoring_range.end,
                )
            result.extend(_series_from_rows(rows, node))
        return tier, tuple(result)

    async def _require_cagg_coverage(self, tier: Tier, monitoring_range: MonitoringRange) -> None:
        relation, _ = _CAGGS[tier]
        rows = await self._database.fetch(_watermark_sql(relation))
        if not rows:
            raise MonitoringUnavailableError(f"required CAGG is unavailable: {relation}")
        watermark = rows[0]["materialization_watermark"]
        if not isinstance(watermark, datetime) or watermark <= monitoring_range.start:
            raise MonitoringUnavailableError(
                f"required CAGG lacks coverage for this range: {relation}"
            )

    async def statistics(
        self, room: str, monitoring_range: MonitoringRange
    ) -> tuple[SensorStatistics, ...]:
        metadata = resolve_room_metadata(room)
        statement = (
            _STATISTICS_CAGG_SQL
            if monitoring_range.duration > STATS_CAGG_MIN_DURATION
            else _STATISTICS_SQL
        )
        result: list[SensorStatistics] = []
        for node in metadata.nodes:
            rows = await self._database.fetch(
                statement,
                room,
                sensor_name_like_pattern(room, node.value) or "",
                monitoring_range.start,
                monitoring_range.end,
            )
            result.extend(_statistics_from_rows(rows, node))
        return tuple(result)

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


def _series_from_rows(rows: Sequence[asyncpg.Record], node: Node) -> tuple[SensorSeries, ...]:
    grouped: defaultdict[tuple[str, str, str], list[SeriesPoint]] = defaultdict(list)
    for row in rows:
        grouped[(row["sensor"], row["unit"], row["data_type"])].append(
            SeriesPoint(
                timestamp=row["bucket"].astimezone(UTC),
                average=float(row["average"]),
                minimum=float(row["minimum"]),
                maximum=float(row["maximum"]),
                sample_count=int(row["sample_count"]),
            )
        )
    return tuple(
        SensorSeries(
            sensor=sensor,
            node=node,
            unit_family=_unit_family(kind),
            unit=unit,
            points=tuple(sorted(points, key=lambda point: point.timestamp)),
        )
        for (sensor, unit, kind), points in sorted(grouped.items())
        if kind in _unit_families()
    )


def _statistics_from_rows(
    rows: Sequence[asyncpg.Record], node: Node
) -> tuple[SensorStatistics, ...]:
    return tuple(
        SensorStatistics(
            sensor=row["sensor"],
            node=node,
            minimum=float(row["minimum"]),
            maximum=float(row["maximum"]),
            average=float(row["average"]),
            stddev_samp=float(row["stddev_samp"]),
            sample_count=int(row["sample_count"]),
        )
        for row in rows
    )


def _unit_families() -> dict[str, UnitFamily]:
    return {
        "temperature": UnitFamily.CELSIUS,
        "humidity": UnitFamily.PERCENT,
        "vpd": UnitFamily.KPA,
        "pressure_deficit": UnitFamily.KPA,
        "co2": UnitFamily.PPM,
        "pressure": UnitFamily.HPA,
        "water_level": UnitFamily.MM,
    }


def _unit_family(kind: str) -> UnitFamily:
    families = _unit_families()
    try:
        return families[kind]
    except KeyError as exc:
        raise MonitoringUnavailableError(f"unsupported monitoring sensor type: {kind}") from exc


def _watermark_sql(relation: str) -> str:
    """Fast per-CAGG watermark: index-backed max(bucket) on the materialized view.

    Replaces the monitoring_cagg_watermark catalog view, whose lateral
    invalidation-log join costs hundreds of milliseconds and duplicates rows.
    ``relation`` comes from the internal _CAGGS constant, never user input.
    """
    if not re.fullmatch(r"[a-z_0-9]+", relation):
        raise MonitoringUnavailableError(f"unsupported CAGG relation: {relation}")
    return f'SELECT max(bucket) AS materialization_watermark FROM "{relation}"'


_RAW_SERIES_SQL: Final = """
SELECT time_bucket(INTERVAL '1 second', measurement.time) AS bucket,
       sensor.name AS sensor, sensor.unit, sensor.data_type,
       avg(measurement.value)::double precision AS average,
       min(measurement.value)::double precision AS minimum,
       max(measurement.value)::double precision AS maximum,
       count(*)::bigint AS sample_count
FROM measurement
JOIN sensor ON sensor.sensor_id = measurement.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
WHERE room.name = $1 AND ($2 = '' OR sensor.name LIKE $2)
  AND measurement.time >= $3 AND measurement.time < $4
GROUP BY bucket, sensor.name, sensor.unit, sensor.data_type
ORDER BY bucket, sensor.name
"""
_TIERED_SERIES_SQL: Final[str] = """
SELECT c.bucket AS bucket, sensor.name AS sensor, sensor.unit, sensor.data_type,
       (c.value_sum / c.sample_count)::double precision AS average,
       c.min_value::double precision AS minimum,
       c.max_value::double precision AS maximum,
       c.sample_count
FROM {relation} c
JOIN sensor ON sensor.sensor_id = c.sensor_id
JOIN device ON device.device_id = sensor.device_id
LEFT JOIN rack ON rack.rack_id = device.rack_id
JOIN room ON room.room_id = rack.room_id
WHERE room.name = $1 AND ($2 = '' OR sensor.name LIKE $2)
  AND c.bucket >= $3 AND c.bucket < $4
  AND c.bucket + INTERVAL '{width}' <= (SELECT max(bucket) FROM {relation})
ORDER BY c.bucket, sensor.name
"""

_TIERED_STATEMENTS: Final[dict[str, str]] = {
    tier.value: _TIERED_SERIES_SQL.format(relation=relation, width=width)
    for tier, (relation, width) in _CAGGS.items()
}

_STATISTICS_SQL: Final = """SELECT sensor.name AS sensor, min(measurement.value)::double precision AS minimum, max(measurement.value)::double precision AS maximum, avg(measurement.value)::double precision AS average, coalesce(stddev_samp(measurement.value),0)::double precision AS stddev_samp, count(*)::bigint AS sample_count FROM measurement JOIN sensor ON sensor.sensor_id=measurement.sensor_id JOIN device ON device.device_id=sensor.device_id LEFT JOIN rack ON rack.rack_id=device.rack_id JOIN room ON room.room_id=rack.room_id WHERE room.name=$1 AND ($2='' OR sensor.name LIKE $2) AND measurement.time >= $3::timestamptz AND measurement.time < $4::timestamptz GROUP BY sensor.name ORDER BY sensor.name"""

# CAGG stddev is an approximation over per-bucket averages; min/max/count remain exact.
_STATISTICS_CAGG_SQL: Final = """SELECT sensor.name AS sensor, min(c.min_value)::double precision AS minimum, max(c.max_value)::double precision AS maximum, (sum(c.value_sum) / sum(c.sample_count))::double precision AS average, coalesce(stddev_samp((c.value_sum / c.sample_count)::double precision),0)::double precision AS stddev_samp, sum(c.sample_count)::bigint AS sample_count FROM monitoring_measurement_5min c JOIN sensor ON sensor.sensor_id=c.sensor_id JOIN device ON device.device_id=sensor.device_id LEFT JOIN rack ON rack.rack_id=device.rack_id JOIN room ON room.room_id=rack.room_id WHERE room.name=$1 AND ($2='' OR sensor.name LIKE $2) AND c.bucket >= $3::timestamptz AND c.bucket < $4::timestamptz GROUP BY sensor.name ORDER BY sensor.name"""
