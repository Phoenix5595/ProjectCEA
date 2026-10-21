"""Repository facade for the physical sensor registry and soil live/history.

The focused repository mixins live in neighboring modules; this module keeps
the public ``SensorRegistryRepository`` composition and registry read API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from app.middleware.exception_handler import NotFoundError, ValidationAPIError
from app.sensor_registry_models import (
    CanAssignmentView,
    Rs485AssignmentView,
    SensorRegistryRecord,
)

from .sensor_registry_assignment import SensorRegistryAssignmentMixin
from .sensor_registry_channels import FLOWER_ROOM, SensorRegistryChannelsMixin
from .sensor_registry_identity import SensorRegistryIdentityMixin
from .soil_sensor_repository import SoilSensorRepositoryMixin

if TYPE_CHECKING:
    from asyncpg import Pool

    from app.database import DatabaseManager


class SensorRegistryRepository(
    SensorRegistryIdentityMixin,
    SensorRegistryAssignmentMixin,
    SoilSensorRepositoryMixin,
    SensorRegistryChannelsMixin,
):
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
            sr.sensor_uid,
            sr.identity_status,
            sr.pending_candidate_registry_id,
            sr.pending_since,
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
            hardware_address=(
                int(row["hardware_address"]) if row["hardware_address"] is not None else None
            ),
            sensor_uid=row["sensor_uid"],
            identity_status=row["identity_status"],
            pending_candidate_registry_id=row["pending_candidate_registry_id"],
            pending_since=row["pending_since"],
            display_name=row["display_name"],
            status="assigned" if assigned else "unassigned",
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            assignment=assignment,
        )
