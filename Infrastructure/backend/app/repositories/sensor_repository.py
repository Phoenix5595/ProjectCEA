"""Sensor data repository — TimescaleDB queries for historical sensor data."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.models import DataPoint
from app.repositories.base import BaseRepository, logger
from shared.cluster_topology import sensor_name_like_pattern

if TYPE_CHECKING:
    from asyncpg import Pool

    from app.database import DatabaseManager


@dataclass(frozen=True)
class _AggregateTier:
    """One step in the time-series aggregate-tier ladder.

    The backend reads from the *coarsest* tier whose buckets still
    give the requested time range a useful resolution. This keeps long
    look-backs (weeks/months) from scanning the raw `measurement`
    hypertable, which is the single hottest table on the Pi.

    Each CAGG (created by Alembic migration 005_phase5a_reconcile)
    exposes the same value shape: ``bucket`` (TIMESTAMPTZ),
    ``avg_value``, ``min_value``, ``max_value``, ``sample_count``.
    Raw `measurement` uses ``time`` and ``value``. Both are normalised
    here so the rest of the query body is identical.

    The fields are interpolated into the SQL string via Python f-string
    formatting; they are NEVER reachable from request input — the
    tier list below is the only source — so this is safe (no
    SQL-injection surface).
    """

    name: str
    table: str
    time_col: str
    value_col: str


# Coarsest first. _pick_tier walks this list and returns the first tier
# whose `min_duration_hours` is <= the requested range. Thresholds are
# deliberately conservative (~50–500 buckets per range, never fewer
# than ~50) so charts never look granular-but-empty.
_AGGREGATE_LADDER: list[tuple[float, _AggregateTier]] = [
    (
        30.0 * 24,
        _AggregateTier(
            name="daily", table="measurement_daily", time_col="bucket", value_col="avg_value"
        ),
    ),
    (
        7.0 * 24,
        _AggregateTier(
            name="hourly", table="measurement_hourly", time_col="bucket", value_col="avg_value"
        ),
    ),
    (
        24.0,
        _AggregateTier(
            name="5min", table="measurement_5min", time_col="bucket", value_col="avg_value"
        ),
    ),
    (
        2.0,
        _AggregateTier(
            name="1min", table="measurement_1min", time_col="bucket", value_col="avg_value"
        ),
    ),
    (0.0, _AggregateTier(name="raw", table="measurement", time_col="time", value_col="value")),
]


def _pick_aggregate_tier(duration_hours: float) -> _AggregateTier:
    """Return the coarsest tier appropriate for the requested range."""
    for min_h, tier in _AGGREGATE_LADDER:
        if duration_hours >= min_h:
            return tier
    return _AGGREGATE_LADDER[-1][1]


class SensorRepository(BaseRepository):
    """Repository for sensor data queries against TimescaleDB."""

    MAX_DATA_POINTS = 5000

    def __init__(self, db_manager: DatabaseManager | None = None, pool: Pool | None = None) -> None:
        super().__init__(pool)
        self._db_manager = db_manager

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        if self._db_manager is not None:
            pool = await self._db_manager._get_pool()
            async with pool.acquire() as conn:
                yield conn
        else:
            async with self.pool.acquire() as conn:
                yield conn

    async def get_sensor_data(
        self,
        location: str,
        cluster: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, list[DataPoint]]:
        """Get all sensor data for a location/cluster within time range.

        Returns dict mapping sensor_type -> list of DataPoint objects.
        Uses aggregate tier selection for performance on long ranges.
        """
        duration_seconds = (end_time - start_time).total_seconds()
        duration_hours = duration_seconds / 3600

        prefix = "🔵 BG_TASK" if duration_seconds <= 65 else "\U0001f7e2 API_CALL"

        logger.debug(
            f"{prefix}: Querying {location}/{cluster} from {start_time} to {end_time} (duration: {duration_hours:.2f} hours)"
        )

        logger.debug(
            f"DB: Query params: location={location}, cluster={cluster}, start_time={start_time}, end_time={end_time}"
        )
        patterns = self._sensor_name_patterns(location, cluster)

        tier = _pick_aggregate_tier(duration_hours)
        logger.debug(
            "DB: range=%.2fh -> tier=%s (table=%s)",
            duration_hours,
            tier.name,
            tier.table,
        )

        # SAFETY: every interpolated identifier comes from the hard-coded
        # `_AGGREGATE_LADDER`; nothing here is reachable from request
        # input, so no SQL-injection surface.
        sql = f"""
            SELECT
                m.{tier.time_col}    AS time,
                s.name               AS sensor_name,
                s.unit               AS sensor_unit,
                m.{tier.value_col}   AS value
            FROM {tier.table} m
            JOIN sensor s ON m.sensor_id = s.sensor_id
            JOIN device d ON s.device_id = d.device_id
            LEFT JOIN rack rk ON d.rack_id = rk.rack_id
            JOIN room r ON rk.room_id = r.room_id
            WHERE r.name = $1
              AND m.{tier.time_col} >= $2
              AND m.{tier.time_col} <= $3
              AND (
                  $4::text[] IS NULL
                  OR EXISTS (
                      SELECT 1
                        FROM unnest($4::text[]) AS pat
                       WHERE s.name LIKE pat
                  )
              )
            ORDER BY m.{tier.time_col} ASC, s.name ASC
        """

        rows = await self._execute(
            sql,
            location,
            start_time,
            end_time,
            patterns,
        )

        logger.debug(f"DB: Found {len(rows)} rows in database")

        if not rows:
            logger.debug(f"DB: No data found for {location}/{cluster}")
            return {}

        sensor_data: dict[str, list[DataPoint]] = {}

        processed_count = 0
        skipped_count = 0
        error_count = 0

        for row in rows:
            try:
                timestamp = row["time"]
                sensor_name = row["sensor_name"]
                value = row["value"]
                unit = row["sensor_unit"]

                if value is None:
                    skipped_count += 1
                    continue

                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                elif not isinstance(timestamp, datetime):
                    timestamp = (
                        datetime.fromtimestamp(timestamp)
                        if isinstance(timestamp, (int, float))
                        else datetime.now()
                    )

                if sensor_name not in sensor_data:
                    sensor_data[sensor_name] = []

                sensor_data[sensor_name].append(
                    DataPoint(timestamp=timestamp, value=float(value), unit=unit)
                )

                processed_count += 1

            except (KeyError, ValueError, TypeError) as e:
                error_count += 1
                if error_count <= 5:
                    logger.debug(f"DB: Error processing row: {e}")
                continue

        logger.debug(
            f"DB: Processed {processed_count} rows, skipped {skipped_count} rows, errors {error_count} rows"
        )
        logger.debug(f"DB: Created {len(sensor_data)} sensor types with data")
        for sensor_type, data_points in sensor_data.items():
            logger.debug(f"DB:   {sensor_type}: {len(data_points)} data points")

        for sensor_type in sensor_data:
            if len(sensor_data[sensor_type]) > self.MAX_DATA_POINTS:
                sensor_data[sensor_type] = self._downsample(
                    sensor_data[sensor_type], self.MAX_DATA_POINTS
                )

        return sensor_data

    async def get_live_sensors(self, location: str, cluster: str) -> list[str]:
        """Return the sensor type names expected for a location/cluster.

        Used by the live endpoint to enumerate which Redis keys to read.
        """
        suffix = _get_sensor_suffix(location, cluster)

        if location == "Lab":
            return ["lab_temp", "water_temperature"]

        base_types = [
            "dry_bulb",
            "wet_bulb",
            "co2",
            "rh",
            "vpd",
            "pressure",
            "secondary_temp",
            "secondary_rh",
            "water_level",
        ]
        if suffix:
            return [f"{bt}_{suffix}" for bt in base_types]
        return list(base_types)

    def _sensor_name_patterns(self, location: str, cluster: str) -> list[str] | None:
        """Cluster-aware sensor-name filtering for normalized schema queries.

        The ``measurement`` table has no cluster column; cluster identity
        is encoded by suffix conventions on ``sensor.name`` (``_f`` /
        ``_b`` for Flower front/back; ``_v`` for Veg). Lab / Outside have
        no suffix split, so we return ``None`` (= no filter).

        The mapping is owned by ``shared.cluster_topology``; this
        method just adapts it to the asyncpg ``LIKE ANY ($1::text[])``
        shape used in the SQL above.
        """
        pattern = sensor_name_like_pattern(location, cluster)
        return None if pattern is None else [pattern]

    def _downsample(self, data: list[DataPoint], target_points: int) -> list[DataPoint]:
        if len(data) <= target_points:
            return data

        step = len(data) / target_points
        indices = [int(i * step) for i in range(target_points)]
        return [data[i] for i in indices if i < len(data)]


def _get_sensor_suffix(location: str, cluster: str) -> str | None:
    if location == "Flower Room":
        if cluster == "front":
            return "f"
        if cluster == "back":
            return "b"
        return None
    elif location == "Veg Room":
        return "v"
    elif location == "Lab":
        return ""
    return None
