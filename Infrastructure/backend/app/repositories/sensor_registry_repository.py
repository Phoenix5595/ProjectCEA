"""Repository for the physical sensor registry and the soil live/history facade.

All SQL for ``/api/sensors/registry*`` and ``/api/sensors/soil/*`` lives
here. The registry table (see
``Infrastructure/database/migrate_sensor_registry.sql``) maps every
physical sensor unit to its commissioned room/bed placement;
``device`` / ``sensor`` / ``measurement`` remain the metric and history
store, Redis stays live state only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import math
from typing import TYPE_CHECKING, Any

import asyncpg

from app.middleware.exception_handler import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationAPIError,
)
from app.repositories.sensor_repository import _pick_aggregate_tier
from app.sensor_registry_models import (
    RS485_BEDS,
    SOIL_HISTORY_MAX_POINTS,
    SOIL_HISTORY_MAX_RANGE_SECONDS,
    SOIL_HISTORY_MIN_POINTS,
    SOIL_HISTORY_MIN_RANGE_SECONDS,
    CanAssignmentRequest,
    CanAssignmentView,
    Rs485AssignmentRequest,
    Rs485AssignmentView,
    SensorRegistryRecord,
    SoilHistoryPoint,
    SoilHistoryResponse,
    SoilLiveResponse,
    SoilMetricHistory,
    SoilMetricValue,
    SoilProbeLive,
)
from shared.cluster_topology import known_rooms, sensor_name_like_pattern, sensor_url_clusters_for
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from asyncpg import Pool

    from app.database import DatabaseManager

logger = get_logger(__name__)

FLOWER_ROOM = "Flower Room"
RS485_BED_CAPACITY = 4

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

# Known CAN metric-channel bases whose location suffix is renamed on
# assignment. Anything else on a CAN device is a conflict, not a guess.
CAN_CHANNEL_BASES: tuple[str, ...] = (
    "dry_bulb",
    "wet_bulb",
    "rh",
    "vpd",
    "co2",
    "secondary_temp",
    "secondary_rh",
    "pressure",
    "water_level",
)

# (room, location_in_room) -> rack name created for the CAN position.
CAN_POSITION_RACKS: dict[str, str] = {"front": "Front", "back": "Back", "main": "Main"}

# Aggregate-tier source bucket sizes, keyed by tier name.
_SOURCE_BUCKET_SECONDS: dict[str, int] = {"1min": 60, "5min": 300, "hourly": 3600, "daily": 86400}


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValidationAPIError(message="Timestamps must carry an explicit UTC offset")
    return value.astimezone(UTC)


class SensorRegistryRepository:
    """Registry reads/writes plus the soil live/history facade."""

    def __init__(self, db_manager: DatabaseManager | None = None, pool: Pool | None = None) -> None:
        self._db_manager = db_manager
        self._pool = pool

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        if self._db_manager is not None:
            pool = await self._db_manager._get_pool()
        elif self._pool is not None:
            pool = self._pool
        else:
            raise RuntimeError("Database pool not initialized")
        async with pool.acquire() as conn:
            yield conn

    # ------------------------------------------------------------------
    # Registry reads
    # ------------------------------------------------------------------

    _SELECT_RECORD = """
        SELECT
            sr.registry_id,
            sr.bus,
            sr.hardware_address,
            sr.display_name,
            sr.first_seen,
            sr.last_seen,
            sr.room_id,
            sr.rack_id,
            sr.location_in_room,
            room.name      AS room_name,
            bed.name       AS bed_name,
            bed_room.name  AS bed_room_name
        FROM sensor_registry sr
        LEFT JOIN room room ON room.room_id = sr.room_id
        LEFT JOIN rack bed ON bed.rack_id = sr.rack_id
        LEFT JOIN room bed_room ON bed_room.room_id = bed.room_id
    """

    async def list_records(
        self, status: str = "all", bus: str | None = None
    ) -> tuple[list[SensorRegistryRecord], int]:
        """All records ordered by bus then numeric hardware address."""
        if status not in ("all", "assigned", "unassigned"):
            raise ValidationAPIError(message="status must be one of all|assigned|unassigned")
        if bus not in (None, "can", "rs485"):
            raise ValidationAPIError(message="bus must be one of can|rs485")

        clauses = ["TRUE"]
        params: list[Any] = []
        if status == "assigned":
            clauses.append("(sr.room_id IS NOT NULL OR sr.rack_id IS NOT NULL)")
        elif status == "unassigned":
            clauses.append("(sr.room_id IS NULL AND sr.rack_id IS NULL)")
        if bus is not None:
            params.append(bus)
            clauses.append(f"sr.bus = ${len(params)}")

        sql = f"{self._SELECT_RECORD} WHERE {' AND '.join(clauses)} ORDER BY sr.bus, sr.hardware_address"

        async with self._acquire() as conn:
            rows = await conn.fetch(sql, *params)

        records = [self._record_from_row(row) for row in rows]
        unassigned_count = sum(1 for record in records if record.status == "unassigned")
        return records, unassigned_count

    async def get_record(self, registry_id: int) -> SensorRegistryRecord:
        async with self._acquire() as conn:
            row = await conn.fetchrow(
                f"{self._SELECT_RECORD} WHERE sr.registry_id = $1", registry_id
            )
        if row is None:
            raise NotFoundError(message=f"Registry record {registry_id} not found")
        return self._record_from_row(row)

    def _record_from_row(self, row: Any) -> SensorRegistryRecord:
        assigned = row["room_id"] is not None or row["rack_id"] is not None
        assignment: CanAssignmentView | Rs485AssignmentView | None = None
        if assigned and row["bus"] == "can":
            assignment = CanAssignmentView(
                kind="can",
                room=row["room_name"] or "",
                location_in_room=row["location_in_room"],
            )
        elif assigned:
            assignment = Rs485AssignmentView(
                kind="rs485",
                room=row["bed_room_name"] or FLOWER_ROOM,
                bed=row["bed_name"] or "",
            )

        return SensorRegistryRecord(
            registry_id=int(row["registry_id"]),
            bus=row["bus"],
            hardware_address=int(row["hardware_address"]),
            display_name=row["display_name"],
            status="assigned" if assigned else "unassigned",
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            assignment=assignment,
        )

    # ------------------------------------------------------------------
    # Assignment (one transaction; lock the target rack/position)
    # ------------------------------------------------------------------

    async def assign(
        self, registry_id: int, body: CanAssignmentRequest | Rs485AssignmentRequest
    ) -> SensorRegistryRecord:
        """Validate and apply an assignment atomically.

        * Body discriminator must match the stored bus (422 otherwise).
        * CAN positions must satisfy the shared topology (422 otherwise).
        * A fifth RS-485 probe on a bed → 409 ``bed_capacity``.
        * An occupied CAN room position → 409 ``position_occupied``.
        * CAN channel renames that would collide → 409 ``channel_name_conflict``
          with the assignment left unchanged.
        """
        async with self._acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT registry_id, bus, room_id, rack_id, location_in_room, device_id
                FROM sensor_registry
                WHERE registry_id = $1
                FOR UPDATE
                """,
                registry_id,
            )
            if row is None:
                raise NotFoundError(message=f"Registry record {registry_id} not found")

            stored_bus: str = row["bus"]
            if isinstance(body, CanAssignmentRequest):
                if stored_bus != "can":
                    raise ValidationAPIError(
                        message=f"Registry record {registry_id} is an RS-485 probe; "
                        "a CAN assignment body is not valid for it"
                    )
                await self._assign_can(conn, row, body)
            else:
                assert isinstance(body, Rs485AssignmentRequest)
                if stored_bus != "rs485":
                    raise ValidationAPIError(
                        message=f"Registry record {registry_id} is a CAN node; "
                        "an RS-485 bed assignment body is not valid for it"
                    )
                await self._assign_rs485(conn, row, body)

        return await self.get_record(registry_id)

    async def _assign_can(self, conn: Any, row: Any, body: CanAssignmentRequest) -> None:
        room = body.room
        location = body.location_in_room

        if room not in known_rooms():
            raise ValidationAPIError(
                message=f"Unknown room {room!r}; known rooms: {sorted(known_rooms())}"
            )
        valid_locations = sensor_url_clusters_for(room)
        if location not in valid_locations:
            raise ValidationAPIError(
                message=f"{location!r} is not a controllable position for {room!r}; "
                f"valid options: {list(valid_locations)}"
            )

        rack_id = await conn.fetchval(
            """
            SELECT r.rack_id
            FROM rack r JOIN room f ON f.room_id = r.room_id
            WHERE f.name = $1 AND r.name = $2
            FOR UPDATE OF r
            """,
            room,
            CAN_POSITION_RACKS[location],
        )
        if rack_id is None:
            rack_id = await conn.fetchval(
                """
                INSERT INTO rack (room_id, name)
                SELECT room_id, $2 FROM room WHERE name = $1
                RETURNING rack_id
                """,
                room,
                CAN_POSITION_RACKS[location],
            )
        if rack_id is None:
            raise ValidationAPIError(message=f"Room {room!r} does not exist")

        occupied = await conn.fetchval(
            """
            SELECT registry_id FROM sensor_registry
            WHERE bus = 'can'
              AND room_id = (SELECT room_id FROM room WHERE name = $1)
              AND location_in_room = $2
              AND registry_id <> $3
            """,
            room,
            location,
            row["registry_id"],
        )
        if occupied is not None:
            raise ConflictError(
                message=f"{room!r} position {location!r} is already held by registry record {occupied}",
                error_code="position_occupied",
            )

        device_id = row["device_id"]
        if device_id is not None:
            await self._rename_can_channels(
                conn, int(device_id), self._canonical_suffix(room, location)
            )
            await conn.execute(
                "UPDATE device SET rack_id = $2 WHERE device_id = $1", device_id, rack_id
            )

        await conn.execute(
            """
            UPDATE sensor_registry
            SET room_id = (SELECT room_id FROM room WHERE name = $2),
                location_in_room = $3,
                rack_id = NULL,
                updated_at = NOW()
            WHERE registry_id = $1
            """,
            row["registry_id"],
            room,
            location,
        )

    async def _assign_rs485(self, conn: Any, row: Any, body: Rs485AssignmentRequest) -> None:
        bed = body.bed
        if bed not in RS485_BEDS:
            raise ValidationAPIError(message=f"bed must be one of {list(RS485_BEDS)}")

        bed_rack_id = await conn.fetchval(
            """
            SELECT r.rack_id
            FROM rack r JOIN room f ON f.room_id = r.room_id
            WHERE f.name = $1 AND r.name = $2
            FOR UPDATE OF r
            """,
            FLOWER_ROOM,
            bed,
        )
        if bed_rack_id is None:
            raise ValidationAPIError(message=f"Flower bed {bed!r} does not exist")

        occupied_count = await conn.fetchval(
            """
            SELECT count(*) FROM sensor_registry
            WHERE bus = 'rs485' AND rack_id = $1 AND registry_id <> $2
            """,
            bed_rack_id,
            row["registry_id"],
        )
        if int(occupied_count) >= RS485_BED_CAPACITY:
            raise ConflictError(
                message=f"{bed} already holds {RS485_BED_CAPACITY} probes and is at capacity",
                error_code="bed_capacity",
            )

        if row["device_id"] is not None:
            await conn.execute(
                "UPDATE device SET rack_id = $2 WHERE device_id = $1", row["device_id"], bed_rack_id
            )

        await conn.execute(
            """
            UPDATE sensor_registry
            SET rack_id = $2,
                room_id = NULL,
                location_in_room = NULL,
                updated_at = NOW()
            WHERE registry_id = $1
            """,
            row["registry_id"],
            bed_rack_id,
        )

    @staticmethod
    def _canonical_suffix(room: str, location: str) -> str:
        pattern = sensor_name_like_pattern(room, location)
        if pattern and pattern.startswith("%"):
            return pattern[1:]
        return ""

    async def _rename_can_channels(self, conn: Any, device_id: int, suffix: str) -> None:
        """Rename the device's known metric channels to the canonical suffix.

        Strips an existing ``_f``/``_b``/``_v`` suffix from a known base and
        applies the target suffix. Any channel outside the known base list,
        or a pre-existing same-device channel with the target name, raises
        ``channel_name_conflict`` — the transaction keeps the assignment.
        """
        rows = await conn.fetch(
            "SELECT sensor_id, name FROM sensor WHERE device_id = $1", device_id
        )

        renames: list[tuple[int, str, str]] = []
        for row in rows:
            name: str = row["name"]
            base = self._channel_base(name)
            if base is None:
                raise ConflictError(
                    message=f"Device {device_id} channel {name!r} is outside the canonical "
                    "CAN channel list; refusing to rename",
                    error_code="channel_name_conflict",
                )
            target = f"{base}{suffix}"
            if target != name:
                renames.append((int(row["sensor_id"]), name, target))

        renamed_ids = [sensor_id for sensor_id, _old, _target in renames]
        for _sensor_id, _old, target in renames:
            clash = await conn.fetchval(
                """
                SELECT 1 FROM sensor
                WHERE device_id = $1 AND name = $2
                  AND NOT (sensor_id = ANY($3::int[]))
                """,
                device_id,
                target,
                renamed_ids,
            )
            if clash is not None:
                raise ConflictError(
                    message=f"Device {device_id} already has a channel named {target!r}; "
                    "renaming would merge two metrics",
                    error_code="channel_name_conflict",
                )

        for sensor_id, _old, target in renames:
            await conn.execute(
                "UPDATE sensor SET name = $2 WHERE sensor_id = $1", sensor_id, target
            )

    @staticmethod
    def _channel_base(name: str) -> str | None:
        """Return the known metric base for a channel name, else ``None``."""
        for base in sorted(CAN_CHANNEL_BASES, key=len, reverse=True):
            if name == base:
                return base
            if name.startswith(base) and name[len(base) :] in ("_f", "_b", "_v"):
                return base
        return None

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
        from app.redis_client import get_sensor_timestamp, get_sensor_value

        metrics: dict[str, SoilMetricValue | None] = dict.fromkeys(
            ("temperature", "water_content", "ec", "ph")
        )
        now = datetime.now(UTC)
        for name in sensor_names:
            metric = self._metric_for_channel(name)
            if metric is None:
                continue
            value = await get_sensor_value(name)
            if value is None:
                continue
            ts_ms = await get_sensor_timestamp(name)
            observed_at = (
                datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC) if ts_ms is not None else now
            )
            metrics[metric] = SoilMetricValue(
                value=value,
                unit=SOIL_METRIC_UNITS[metric],
                observed_at=observed_at,
                age_seconds=(now - observed_at).total_seconds(),
            )
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

            series: list[SoilMetricHistory] = []
            for device_row in device_rows:
                device_sensors = [
                    r for r in sensor_rows if r["device_id"] == device_row["device_id"]
                ]
                if not device_sensors:
                    continue
                points_by_channel = await self._bucketed_points(
                    conn, device_sensors, start, end, tier, bucket_seconds
                )
                for wire_key in ("temperature", "humidity", "ec", "ph"):
                    points = points_by_channel.get(wire_key)
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
        device_sensors: list[Any],
        start: datetime,
        end: datetime,
        tier: Any,
        bucket_seconds: int,
    ) -> dict[str, list[SoilHistoryPoint]]:
        """Bucket the tier rows for every metric channel of one device.

        Channels are identified per sensor row by the wire-key suffix of
        ``sensor.name`` so channel identity is explicit per series.
        """
        sensor_ids = [int(r["sensor_id"]) for r in device_sensors]
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
            sql = f"""
                SELECT
                    m.sensor_id,
                    floor(extract(epoch FROM (m.{tier.time_col} - $1)) / $2)::int AS bucket_index,
                    sum(m.avg_value * m.sample_count) / NULLIF(sum(m.sample_count), 0) AS avg_value,
                    min(m.min_value) AS min_value,
                    max(m.max_value) AS max_value,
                    sum(m.sample_count) AS sample_count
                FROM {tier.table} m
                WHERE m.sensor_id = ANY($3::int[])
                  AND m.{tier.time_col} >= $1 AND m.{tier.time_col} < $4
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

        sensor_to_channel: dict[int, str | None] = {
            int(r["sensor_id"]): self._wire_key_for_channel(str(r["name"])) for r in device_sensors
        }

        points_by_channel: dict[str, list[SoilHistoryPoint]] = {}
        for row in rows:
            wire_key = sensor_to_channel.get(int(row["sensor_id"]))
            if wire_key is None:
                continue
            sample_count = int(row["sample_count"] or 0)
            if sample_count <= 0:
                continue
            bucket_start = start + timedelta(seconds=int(row["bucket_index"]) * bucket_seconds)
            points_by_channel.setdefault(wire_key, []).append(
                SoilHistoryPoint(
                    bucket_start=bucket_start,
                    average=float(row["avg_value"]) if row["avg_value"] is not None else None,
                    minimum=float(row["min_value"]) if row["min_value"] is not None else None,
                    maximum=float(row["max_value"]) if row["max_value"] is not None else None,
                    sample_count=sample_count,
                )
            )
        return points_by_channel

    @staticmethod
    def _wire_key_for_channel(name: str) -> str | None:
        for wire_key in SOIL_WIRE_TO_METRIC:
            if name.endswith(f"_{wire_key}"):
                return wire_key
        return None
