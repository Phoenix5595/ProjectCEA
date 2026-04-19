"""Database manager for TimescaleDB operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os

import asyncpg

from app.models import DataPoint
from shared.cluster_topology import sensor_name_like_pattern
from shared.db_credentials import load_postgres_password
from shared.infra_logging import get_logger

logger = get_logger(__name__)


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

    name: str  # short label used in logs ("raw", "1min", ...)
    table: str  # SQL identifier
    time_col: str  # column to use for ORDER BY / WHERE bounds
    value_col: str  # column whose value we expose as `value`


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
    # Walked the whole list (impossible because last entry's min is
    # 0.0) — degrade safely to raw.
    return _AGGREGATE_LADDER[-1][1]


class DatabaseManager:
    """Manages TimescaleDB database connections and queries."""

    MAX_DATA_POINTS = 5000

    def __init__(self, db_config: dict[str, str] | None = None):
        """Initialize database manager.

        Args:
            db_config: Database connection config dict with host, database, user, password.
                      If None, uses environment variables or defaults.
        """
        if db_config is None:
            password = load_postgres_password()
            self.db_config = {
                "host": os.getenv("POSTGRES_HOST", "localhost"),
                "database": os.getenv("POSTGRES_DB", "cea_sensors"),
                "user": os.getenv("POSTGRES_USER", "cea_user"),
                "password": password,
                "port": int(os.getenv("POSTGRES_PORT", "5432")),
            }
        else:
            self.db_config = db_config
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool.

        Raises:
            ConnectionError: If connection pool creation fails
        """
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    host=self.db_config["host"],
                    database=self.db_config["database"],
                    user=self.db_config["user"],
                    password=self.db_config["password"],
                    port=self.db_config["port"],
                    min_size=2,
                    max_size=10,
                    command_timeout=30,  # Query timeout in seconds
                    server_settings={"application_name": "cea_backend"},
                )
            except Exception as e:
                raise ConnectionError(f"Failed to connect to TimescaleDB: {e}") from e
        return self._pool

    async def close(self):
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def get_all_sensors_for_location(
        self, location: str, cluster: str, start_time: datetime, end_time: datetime
    ) -> dict[str, list[DataPoint]]:
        """Get all sensor data for a location/cluster within time range.

        Returns dict mapping sensor_type -> list of DataPoint objects.
        Uses new normalized schema: measurement -> sensor -> device -> rack -> room
        """
        # Calculate duration to identify the caller type
        duration_seconds = (end_time - start_time).total_seconds()
        duration_hours = duration_seconds / 3600

        # Background task queries 60 seconds (1 minute), API should query longer ranges
        if duration_seconds <= 65:  # Background task (60 seconds + small buffer)
            prefix = "🔵 BG_TASK"
        else:
            prefix = "🟢 API_CALL"

        logger.debug(
            f"{prefix}: Querying {location}/{cluster} from {start_time} to {end_time} (duration: {duration_hours:.2f} hours)"
        )

        pool = await self._get_pool()

        logger.debug(
            f"DB: Query params: location={location}, cluster={cluster}, start_time={start_time}, end_time={end_time}"
        )
        patterns = self._sensor_name_patterns(location, cluster)

        # Pick the coarsest aggregate tier that still resolves the range.
        # Phase 5d: replaced the previous 3-step ladder (raw / hourly /
        # daily) — which incidentally referenced a non-existent column
        # (`mh.time` / `md.time` instead of the actual `bucket`) and
        # would have crashed if exercised — with a 5-step ladder driven
        # by `_pick_aggregate_tier`. The coarsest-fit policy means a
        # 30-day chart now reads ~30 daily rows from the daily CAGG
        # instead of millions of raw `measurement` rows.
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

        async with pool.acquire() as conn:
            rows = await conn.fetch(
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

        # Parse and organize data by sensor name
        sensor_data: dict[str, list[DataPoint]] = {}

        processed_count = 0
        skipped_count = 0
        error_count = 0

        for row in rows:
            try:
                # Get data from row
                timestamp = row["time"]
                sensor_name = row["sensor_name"]
                value = row["value"]
                unit = row["sensor_unit"]

                if value is None:
                    skipped_count += 1
                    continue

                # Convert timestamp if needed
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                elif not isinstance(timestamp, datetime):
                    timestamp = (
                        datetime.fromtimestamp(timestamp)
                        if isinstance(timestamp, (int, float))
                        else datetime.now()
                    )

                # Group by sensor name
                if sensor_name not in sensor_data:
                    sensor_data[sensor_name] = []

                sensor_data[sensor_name].append(
                    DataPoint(timestamp=timestamp, value=float(value), unit=unit)
                )

                processed_count += 1

            except (KeyError, ValueError, TypeError) as e:
                error_count += 1
                if error_count <= 5:  # Only print first 5 errors to avoid spam
                    logger.debug(f"DB: Error processing row: {e}")
                continue

        logger.debug(
            f"DB: Processed {processed_count} rows, skipped {skipped_count} rows, errors {error_count} rows"
        )
        logger.debug(f"DB: Created {len(sensor_data)} sensor types with data")
        for sensor_type, data_points in sensor_data.items():
            logger.debug(f"DB:   {sensor_type}: {len(data_points)} data points")

        # Downsample if too many points
        for sensor_type in sensor_data:
            if len(sensor_data[sensor_type]) > self.MAX_DATA_POINTS:
                sensor_data[sensor_type] = self._downsample(
                    sensor_data[sensor_type], self.MAX_DATA_POINTS
                )

        return sensor_data

    def _sensor_name_patterns(self, location: str, cluster: str) -> list[str] | None:
        """Cluster-aware sensor-name filtering for normalized schema queries.

        The ``measurement`` table has no cluster column; cluster identity
        is encoded by suffix conventions on ``sensor.name`` (``_f`` /
        ``_b`` for Flower front/back; ``_v`` for Veg). Lab / Outside have
        no suffix split, so we return ``None`` (= no filter).

        The mapping is owned by ``shared.cluster_topology``; this
        method just adapts it to the asyncpg ``LIKE ANY ($1::text[])``
        shape used in the SQL above.

        Phase 5e: this used to encode the room→suffix table inline and
        silently returned ``[]`` (= match nothing) when callers passed
        the wrong cluster type for the room (e.g. ``Flower Room/main``).
        That swallowed real wiring bugs. The route layer now validates
        the cluster up-front via ``assert_sensor_cluster`` and returns
        a 400 with a hint, so by the time we reach this method the
        ``(location, cluster)`` pair is guaranteed valid.
        """
        pattern = sensor_name_like_pattern(location, cluster)
        return None if pattern is None else [pattern]

    def _get_node_id(self, location: str, cluster: str) -> int:
        """Map location/cluster to CAN node ID.

        Node IDs from v7 NodeMapping.cpp (sensor / CAN clusters, not automation `devices:`):
        - 1: Flower Room, back (telemetry)
        - 2: Flower Room, front (telemetry)
        - 3: Veg Room, main
        """
        mapping = {
            ("Flower Room", "back"): 1,
            ("Flower Room", "front"): 2,
            ("Veg Room", "main"): 3,
            # Add more mappings as needed
            ("Lab", "main"): 4,
            ("Outside", "main"): 5,
        }
        node_id = mapping.get((location, cluster))
        if node_id is None:
            logger.warning(
                f"DB: Unknown location/cluster mapping for node ID: {location}/{cluster}"
            )
            node_id = 1
        logger.debug(f"DB: Mapped {location}/{cluster} -> node_id={node_id}")
        return node_id

    def _extract_sensors(
        self, decoded: dict, message_type: str, location: str, cluster: str
    ) -> list[tuple[str, float, str]]:
        """Extract sensor values from decoded CAN message."""
        sensors = []
        suffix = self._get_sensor_suffix(location, cluster)

        if message_type == "PT100":
            # Dry bulb temperature
            if "temp_dry_c" in decoded:
                if location == "Lab":
                    sensor_key = "lab_temp"
                elif suffix:
                    sensor_key = f"dry_bulb_{suffix}"
                else:
                    sensor_key = "dry_bulb"
                sensors.append((sensor_key, float(decoded["temp_dry_c"]), "°C"))
            # Wet bulb temperature
            if "temp_wet_c" in decoded:
                sensor_key = f"wet_bulb_{suffix}" if suffix else "wet_bulb"
                sensors.append((sensor_key, float(decoded["temp_wet_c"]), "°C"))

        elif message_type == "SCD30":
            # CO2
            if "co2_ppm" in decoded:
                sensor_key = f"co2_{suffix}" if suffix else "co2"
                sensors.append((sensor_key, float(decoded["co2_ppm"]), "ppm"))
            # Secondary temperature
            if "temperature_c" in decoded:
                if location == "Lab":
                    sensor_key = "water_temp"
                elif suffix:
                    sensor_key = f"secondary_temp_{suffix}"
                else:
                    sensor_key = "secondary_temp"
                sensors.append((sensor_key, float(decoded["temperature_c"]), "°C"))
            # Secondary RH
            if "humidity_percent" in decoded:
                sensor_key = f"secondary_rh_{suffix}" if suffix else "secondary_rh"
                sensors.append((sensor_key, float(decoded["humidity_percent"]), "%"))

        elif message_type == "BME280":
            # Pressure
            if "pressure_hpa" in decoded:
                sensor_key = f"pressure_{suffix}" if suffix else "pressure"
                sensors.append((sensor_key, float(decoded["pressure_hpa"]), "hPa"))

        elif message_type == "VL53" or message_type == "VL53L0X":
            # Water level (distance)
            if "distance" in decoded or "distance_mm" in decoded:
                distance_key = "distance_mm" if "distance_mm" in decoded else "distance"
                sensor_key = f"water_level_{suffix}" if suffix else "water_level"
                sensors.append((sensor_key, float(decoded[distance_key]), "mm"))

        # Note: RH and VPD are calculated separately after collecting all data points
        # to ensure we have matching timestamps

        return sensors

    def _get_sensor_suffix(self, location: str, cluster: str) -> str | None:
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

    # NOTE: _calculate_rh / _calculate_vpd were removed in Phase 6's
    # climate-math lift. They were defined here with Bolton coefficients
    # (17.67 / 243.5) but never called from anywhere in the backend — the
    # actual derivation of RH/VPD lives in the can-processor pipeline now,
    # which uses the canonical shared.calculate_rh / shared.calculate_vpd.
    # If a future caller wants to compute these here, import them from
    # ``shared`` rather than re-introducing a divergent local copy.

    def _downsample(self, data: list[DataPoint], target_points: int) -> list[DataPoint]:
        """Downsample data to target number of points."""
        if len(data) <= target_points:
            return data

        step = len(data) / target_points
        indices = [int(i * step) for i in range(target_points)]
        return [data[i] for i in indices if i < len(data)]
