from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Any

import asyncpg

from app.middleware.exception_handler import ServiceUnavailableError, ValidationAPIError
from app.repositories.sensor_repository import _pick_aggregate_tier, _tier_source_sql
from app.sensor_registry_models import (
    SOIL_HISTORY_MAX_POINTS,
    SOIL_HISTORY_MAX_RANGE_SECONDS,
    SOIL_HISTORY_MIN_POINTS,
    SOIL_HISTORY_MIN_RANGE_SECONDS,
    SoilHistoryPoint,
    SoilHistoryResponse,
    SoilLiveResponse,
    SoilMetricHistory,
    SoilMetricValue,
    SoilProbeLive,
)
from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Ingestion wire key -> operator-facing metric name (`humidity` stays the
# ingestion key; the API exposes `water_content`).
SOIL_WIRE_TO_METRIC: dict[str, str] = {
    "temperature": "temperature",
    "humidity": "water_content",
    "ec": "ec",
    "ph": "ph",
}
SOIL_METRIC_UNITS: dict[str, str] = {
    "temperature": "°C",
    "water_content": "%",
    "ec": "µS/cm",
    "ph": "pH",
}
# Aggregate-tier source bucket sizes, keyed by tier name.
_SOURCE_BUCKET_SECONDS: dict[str, int] = {"1min": 60, "5min": 300, "hourly": 3600, "daily": 86400}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationAPIError(message="Timestamps must carry an explicit UTC offset")
    return value.astimezone(UTC)


class SoilSensorRepositoryMixin:
    # ------------------------------------------------------------------
    # Soil live
    # ------------------------------------------------------------------

    _SELECT_BED_PROBE = """
        SELECT
            sr.registry_id,
            sr.hardware_address,
            sr.display_name,
            sr.last_seen,
            bed.name      AS bed,
            sr.device_id
        FROM sensor_registry sr
        JOIN rack bed ON bed.rack_id = sr.rack_id
        WHERE sr.bus = 'rs485' AND sr.rack_id IS NOT NULL
          AND sr.hardware_address IS NOT NULL
        ORDER BY sr.hardware_address
    """

    async def soil_live(self) -> SoilLiveResponse:
        """Live values for assigned RS-485 probes, ordered by hardware address."""
        async with self._acquire() as conn:
            rows = await conn.fetch(self._SELECT_BED_PROBE)
            probes: list[SoilProbeLive] = []
            for row in rows:
                device_id = row["device_id"]
                sensor_names: list[str] = []
                if device_id is not None:
                    sensor_rows = await conn.fetch(
                        "SELECT name FROM sensor WHERE device_id = $1", device_id
                    )
                    sensor_names = [r["name"] for r in sensor_rows]
                probes.append(await self._probe_live(row, sensor_names))

        return SoilLiveResponse(generated_at=datetime.now(UTC), probes=probes)

    async def _probe_live(self, row: Any, sensor_names: list[str]) -> SoilProbeLive:
        from app.redis_client import get_redis_client
        from shared.redis_keys import sensor_full, sensor_full_ts

        metrics: dict[str, SoilMetricValue | None] = dict.fromkeys(
            ("temperature", "water_content", "ec", "ph")
        )
        now = datetime.now(UTC)
        channels = [
            (name, metric)
            for name in sensor_names
            if (metric := self._metric_for_channel(name)) is not None
        ]
        if channels:
            try:
                client = await get_redis_client()
                if client is not None:
                    keys: list[str] = []
                    for name, _ in channels:
                        keys.append(sensor_full("Flower Room", "main", name))
                        keys.append(sensor_full_ts("Flower Room", "main", name))
                    values = await client.mget(keys)

                    for (_, metric), raw_value, raw_timestamp in zip(
                        channels, values[::2], values[1::2], strict=True
                    ):
                        if raw_value is None:
                            continue
                        try:
                            value = float(raw_value)
                        except (TypeError, ValueError):
                            continue

                        observed_at = now
                        if raw_timestamp is not None:
                            try:
                                observed_at = datetime.fromtimestamp(
                                    int(raw_timestamp) / 1000.0, tz=UTC
                                )
                            except (TypeError, ValueError, OverflowError):
                                observed_at = now
                        metrics[metric] = SoilMetricValue(
                            value=value,
                            unit=SOIL_METRIC_UNITS[metric],
                            observed_at=observed_at,
                            age_seconds=(now - observed_at).total_seconds(),
                        )
            except Exception as exc:
                logger.warning("Failed to read live soil values from Redis: %s", exc)
                metrics = dict.fromkeys(("temperature", "water_content", "ec", "ph"))

        return SoilProbeLive(
            registry_id=int(row["registry_id"]),
            hardware_address=int(row["hardware_address"]),
            display_name=row["display_name"],
            bed=row["bed"],
            last_seen=row["last_seen"],
            metrics=metrics,  # type: ignore[arg-type]
        )

    @staticmethod
    def _metric_for_channel(name: str) -> str | None:
        for wire_key, metric in SOIL_WIRE_TO_METRIC.items():
            if name.endswith(f"_{wire_key}"):
                return metric
        return None

    # ------------------------------------------------------------------
    # Soil history
    # ------------------------------------------------------------------

    async def soil_history(
        self,
        start: datetime,
        end: datetime,
        max_points: int,
    ) -> SoilHistoryResponse:
        """Truthful envelope history for assigned RS-485 probes.

        Selects the coarsest aggregate tier from the shared backend ladder,
        then buckets half-open ``[start, end)`` relative to ``start`` into
        ``target_interval = ceil(range / max_points)`` windows rounded up to
        whole source-bucket multiples. Aggregate rows combine with
        sample-count weights (never an average of averages).
        """
        start = _aware_utc(start)
        end = _aware_utc(end)
        if end <= start:
            raise ValidationAPIError(message="end must be after start")
        range_seconds = (end - start).total_seconds()
        if range_seconds < SOIL_HISTORY_MIN_RANGE_SECONDS:
            raise ValidationAPIError(
                message=f"History range must be at least {SOIL_HISTORY_MIN_RANGE_SECONDS} seconds"
            )
        if range_seconds > SOIL_HISTORY_MAX_RANGE_SECONDS:
            raise ValidationAPIError(
                message=f"History range must not exceed 7 days ({SOIL_HISTORY_MAX_RANGE_SECONDS} seconds)"
            )
        if not SOIL_HISTORY_MIN_POINTS <= max_points <= SOIL_HISTORY_MAX_POINTS:
            raise ValidationAPIError(
                message=f"max_points must be between {SOIL_HISTORY_MIN_POINTS} "
                f"and {SOIL_HISTORY_MAX_POINTS}"
            )

        tier = _pick_aggregate_tier(range_seconds / 3600.0)
        target_seconds = int(math.ceil(range_seconds / max_points))
        if tier.table == "measurement":
            bucket_seconds = max(target_seconds, 1)
        else:
            source_bucket = _SOURCE_BUCKET_SECONDS[tier.name]
            bucket_seconds = max(
                int(math.ceil(target_seconds / source_bucket)) * source_bucket, source_bucket
            )

        async with self._acquire() as conn:
            device_rows = await conn.fetch(self._SELECT_BED_PROBE)
            if not device_rows:
                return SoilHistoryResponse(
                    start=start,
                    end=end,
                    max_points=max_points,
                    tier=tier.name,
                    bucket_seconds=bucket_seconds,
                    series=[],
                )

            device_ids = [int(r["device_id"]) for r in device_rows if r["device_id"] is not None]
            sensor_rows = (
                await conn.fetch(
                    "SELECT sensor_id, device_id, name FROM sensor WHERE device_id = ANY($1::int[])",
                    device_ids,
                )
                if device_ids
                else []
            )

            points_by_sensor = (
                await self._bucketed_points(conn, sensor_rows, start, end, tier, bucket_seconds)
                if sensor_rows
                else {}
            )
            sensors_by_device: dict[int, list[Any]] = {}
            for sensor_row in sensor_rows:
                sensors_by_device.setdefault(int(sensor_row["device_id"]), []).append(sensor_row)

            series: list[SoilMetricHistory] = []
            for device_row in device_rows:
                device_id = device_row["device_id"]
                device_sensors = (
                    sensors_by_device.get(int(device_id), []) if device_id is not None else []
                )
                if not device_sensors:
                    continue
                for wire_key in ("temperature", "humidity", "ec", "ph"):
                    channel_sensor_ids = [
                        int(sensor_row["sensor_id"])
                        for sensor_row in device_sensors
                        if self._wire_key_for_channel(str(sensor_row["name"])) == wire_key
                    ]
                    points = [
                        point
                        for _, point in sorted(
                            (
                                (sensor_id, point)
                                for sensor_id in channel_sensor_ids
                                for point in points_by_sensor.get(sensor_id, [])
                            ),
                            key=lambda item: (item[1].bucket_start, item[0]),
                        )
                    ]
                    if not points:
                        continue
                    metric = SOIL_WIRE_TO_METRIC[wire_key]
                    series.append(
                        SoilMetricHistory(
                            registry_id=int(device_row["registry_id"]),
                            hardware_address=int(device_row["hardware_address"]),
                            display_name=device_row["display_name"],
                            bed=device_row["bed"],
                            metric=metric,  # type: ignore[arg-type]
                            unit=SOIL_METRIC_UNITS[metric],
                            points=points,
                        )
                    )
        return SoilHistoryResponse(
            start=start,
            end=end,
            max_points=max_points,
            tier=tier.name,
            bucket_seconds=bucket_seconds,
            series=series,
        )

    async def _bucketed_points(
        self,
        conn: Any,
        sensor_rows: list[Any],
        start: datetime,
        end: datetime,
        tier: Any,
        bucket_seconds: int,
    ) -> dict[int, list[SoilHistoryPoint]]:
        """Bucket tier rows for all assigned soil sensors in one query."""
        sensor_ids = [int(r["sensor_id"]) for r in sensor_rows]
        if not sensor_ids:
            return {}
        if tier.table == "measurement":
            sql = """
                SELECT
                    m.sensor_id,
                    floor(extract(epoch FROM (m.time - $1)) / $2)::int AS bucket_index,
                    avg(m.value) AS avg_value,
                    min(m.value) AS min_value,
                    max(m.value) AS max_value,
                    count(*)     AS sample_count
                FROM measurement m
                WHERE m.sensor_id = ANY($3::int[])
                  AND m.time >= $1 AND m.time < $4
                GROUP BY bucket_index, m.sensor_id
                ORDER BY bucket_index, m.sensor_id
            """
        else:
            # Native views are materialized-only; the shared source serves
            # unrefreshed/invalidated buckets exactly from raw measurement.
            sql = f"""
                SELECT
                    m.sensor_id,
                    floor(extract(epoch FROM (m.time - $1)) / $2)::int AS bucket_index,
                    (sum(m.value_sum) / NULLIF(sum(m.sample_count), 0))::double precision AS avg_value,
                    min(m.min_value) AS min_value,
                    max(m.max_value) AS max_value,
                    sum(m.sample_count) AS sample_count
                FROM ({_tier_source_sql(tier, start="$1", end="$4")}) m
                WHERE m.sensor_id = ANY($3::int[])
                  AND m.time >= $1 AND m.time < $4
                GROUP BY bucket_index, m.sensor_id
                ORDER BY bucket_index, m.sensor_id
            """

        try:
            rows = await conn.fetch(sql, start, bucket_seconds, sensor_ids, end)
        except asyncpg.UndefinedTableError as exc:
            logger.error("Soil history tier %s missing: %s", tier.name, exc)
            raise ServiceUnavailableError(
                message=f"Aggregate tier {tier.name!r} is unavailable in the database",
                error_code="AGGREGATE_TIER_UNAVAILABLE",
            ) from exc

        points_by_sensor: dict[int, list[SoilHistoryPoint]] = {}
        for row in rows:
            sensor_id = int(row["sensor_id"])
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                continue
            bucket_start = start + timedelta(seconds=int(row["bucket_index"]) * bucket_seconds)
            points_by_sensor.setdefault(sensor_id, []).append(
                SoilHistoryPoint(
                    bucket_start=bucket_start,
                    average=float(row["avg_value"]) if row["avg_value"] is not None else None,
                    minimum=float(row["min_value"]) if row["min_value"] is not None else None,
                    maximum=float(row["max_value"]) if row["max_value"] is not None else None,
                    sample_count=sample_count,
                )
            )
        return points_by_sensor

    @staticmethod
    def _wire_key_for_channel(name: str) -> str | None:
        for wire_key in SOIL_WIRE_TO_METRIC:
            if name.endswith(f"_{wire_key}"):
                return wire_key
        return None
