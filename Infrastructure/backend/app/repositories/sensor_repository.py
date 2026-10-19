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
from shared.history_availability import require_local_history

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

    Tiers read the current in-house monitoring read models only:

    * ``monitoring_measurement_1min`` / ``monitoring_measurement_5min``
      (``monitoring_read_models.sql``) bucket the raw hypertable by
      1/5 minutes per sensor.
    * ``monitoring_measurement_1hour`` / ``monitoring_measurement_1day``
      (``monitoring_history_overview.sql``) carry mergeable hourly
      sufficient statistics, with the daily tier rolling up its hourly
      parent, both per sensor.

    Tier ``name`` values (``daily``/``hourly``/``5min``/``1min``/``raw``)
    are part of the public soil-history response contract and stay as
    they were; only the backing relations change.

    Every aggregate exposes the same sufficient-statistics shape:
    ``bucket`` (TIMESTAMPTZ), ``sample_count``, ``value_sum``,
    ``value_sum_squares``, ``min_value``, ``max_value``,
    ``last_observed_at``. The chart value is the sample-count-weighted
    mean ``value_sum / NULLIF(sample_count, 0)`` — never an average of
    averages — cast to double precision like the legacy ``AVG(value)``
    output. Raw `measurement` uses ``time`` and ``value``.

    Native views are ``materialized_only``. A bucket is read from the view
    only when it lies below the CAGG's real watermark and no pending
    invalidation in its refresh chain overlaps it; every other bucket
    (live tail, never-refreshed or late-corrected ranges) is recomputed from
    raw ``measurement`` with the identical ``sum(value::NUMERIC)/count(value)``
    statistic, so results are exact and never silently missing.

    The fields are interpolated into the SQL string via Python f-string
    formatting; they are NEVER reachable from request input — the
    tier list below is the only source — so this is safe (no
    SQL-injection surface).
    """

    name: str
    table: str
    width: str | None = None
    chain: tuple[str, ...] = ()


# Coarsest first. _pick_tier walks this list and returns the first tier
# whose `min_duration_hours` is <= the requested range. Thresholds are
# deliberately conservative (~50–500 buckets per range, never fewer
# than ~50) so charts never look granular-but-empty.
_AGGREGATE_LADDER: list[tuple[float, _AggregateTier]] = [
    (30.0 * 24, _AggregateTier(
        name="daily", table="monitoring_measurement_1day", width="1 day",
        chain=("monitoring_measurement_1day", "monitoring_measurement_1hour"))),
    (7.0 * 24, _AggregateTier(
        name="hourly", table="monitoring_measurement_1hour", width="1 hour",
        chain=("monitoring_measurement_1hour",))),
    (24.0, _AggregateTier(
        name="5min", table="monitoring_measurement_5min", width="5 minutes",
        chain=("monitoring_measurement_5min",))),
    (2.0, _AggregateTier(
        name="1min", table="monitoring_measurement_1min", width="1 minute",
        chain=("monitoring_measurement_1min",))),
    (0.0, _AggregateTier(name="raw", table="measurement")),
]

# Invalidation endpoints below PostgreSQL's minimum finite timestamp map to
# -infinity (same rule as monitoring_history_cagg_watermark).
_MIN_FINITE_INTERNAL = -210866803200000000


def _tier_source_sql(tier: _AggregateTier, start: str = "$2", end: str = "$3") -> str:
    """Return exact per-bucket statistics for the tier.

    Columns: ``time, sensor_id, sample_count, value_sum, min_value, max_value``.
    ``start``/``end`` are the caller's bind placeholders; buckets are kept when
    ``start <= bucket <= end`` and callers apply their own end exclusivity.
    """
    if tier.width is None:
        # Raw keeps the original REAL exactly: REAL->NUMERIC would round.
        return ("SELECT time, sensor_id, 1::bigint AS sample_count, "
                "value::double precision AS value_sum, "
                "value AS min_value, value AS max_value FROM measurement")
    width = f"INTERVAL '{tier.width}'"
    chain = ",".join(f"'{name}'" for name in tier.chain)

    def endpoint(column: str) -> str:
        return (f"CASE WHEN l.{column} < {_MIN_FINITE_INTERNAL} THEN '-infinity'::timestamptz "
                f"ELSE _timescaledb_functions.to_timestamp(l.{column}) END")

    return f"""
        WITH coverage AS (
            SELECT COALESCE(_timescaledb_functions.to_timestamp(
                       _timescaledb_functions.cagg_watermark(c.mat_hypertable_id)),
                       '-infinity'::timestamptz) AS through
              FROM _timescaledb_catalog.continuous_agg c
             WHERE c.user_view_schema = 'public' AND c.user_view_name = '{tier.table}'
        ), stale AS (
            SELECT {endpoint('lowest_modified_value')} AS lo,
                   {endpoint('greatest_modified_value')} AS hi
              FROM _timescaledb_catalog.continuous_agg c
              JOIN LATERAL (
                  SELECT lowest_modified_value, greatest_modified_value
                    FROM _timescaledb_catalog.continuous_aggs_materialization_invalidation_log
                   WHERE materialization_id = c.mat_hypertable_id
                  UNION ALL
                  SELECT lowest_modified_value, greatest_modified_value
                    FROM _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log
                   WHERE hypertable_id = c.raw_hypertable_id
              ) l ON TRUE
             WHERE c.user_view_schema = 'public' AND c.user_view_name IN ({chain})
        )
        SELECT v.bucket AS time, v.sensor_id, v.sample_count, v.value_sum,
               v.min_value, v.max_value
          FROM {tier.table} v, coverage
         WHERE v.bucket >= {start} AND v.bucket <= {end}
           AND v.bucket + {width} <= coverage.through
           AND NOT EXISTS (SELECT 1 FROM stale s
                            WHERE s.lo < v.bucket + {width} AND s.hi >= v.bucket)
        UNION ALL
        SELECT r.bucket, r.sensor_id, r.sample_count, r.value_sum, r.min_value, r.max_value
          FROM (
              SELECT time_bucket({width}, raw.time) AS bucket, raw.sensor_id,
                     count(raw.value) AS sample_count, sum(raw.value::NUMERIC) AS value_sum,
                     min(raw.value) AS min_value, max(raw.value) AS max_value
                FROM measurement raw
               WHERE raw.time >= time_bucket({width}, {start}::timestamptz)
                 AND raw.time < {end}::timestamptz + {width}
               GROUP BY 1, 2
          ) r, coverage
         WHERE r.bucket >= {start} AND r.bucket <= {end}
           AND (r.bucket + {width} > coverage.through
                OR EXISTS (SELECT 1 FROM stale s
                            WHERE s.lo < r.bucket + {width} AND s.hi >= r.bucket))
    """


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
                m.time               AS time,
                s.name               AS sensor_name,
                s.unit               AS sensor_unit,
                (m.value_sum / NULLIF(m.sample_count, 0))::double precision AS value
            FROM ({_tier_source_sql(tier)}) m
            JOIN sensor s ON m.sensor_id = s.sensor_id
            JOIN device d ON s.device_id = d.device_id
            LEFT JOIN rack rk ON d.rack_id = rk.rack_id
            JOIN room r ON rk.room_id = r.room_id
            WHERE r.name = $1
              AND m.time >= $2
              AND m.time <= $3
              AND (
                  $4::text[] IS NULL
                  OR EXISTS (
                      SELECT 1
                        FROM unnest($4::text[]) AS pat
                       WHERE s.name LIKE pat
                  )
              )
            ORDER BY m.time ASC, s.name ASC
        """

        async with (
            self._acquire() as conn,
            conn.transaction(isolation="repeatable_read", readonly=True),
        ):
            await require_local_history(
                conn, room=location, start=start_time, end=end_time,
                source_tables=("measurement",),
            )
            rows = await conn.fetch(sql, location, start_time, end_time, patterns)

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
