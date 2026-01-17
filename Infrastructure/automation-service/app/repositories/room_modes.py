from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class RoomModeRepository(BaseRepository):
    """Repository for room mode operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_room_modes(self) -> list[dict[str, Any]]:
        """Get all available room modes."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM room_modes ORDER BY id")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get room modes: {e}")
            return []

    async def get_flower_submodes(self) -> list[dict[str, Any]]:
        """Get flower submodes."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("SELECT * FROM flower_submodes ORDER BY id")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get flower submodes: {e}")
            return []

    async def get_active_mode(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get active mode for location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT rm.name as mode_name, fs.name as submode_name, arm.mode_id, arm.submode_id
                       FROM active_room_modes arm
                       JOIN room_modes rm ON rm.id = arm.mode_id
                       LEFT JOIN flower_submodes fs ON fs.id = arm.submode_id
                       WHERE arm.location = $1 AND arm.cluster = $2""",
                    location, cluster
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get active mode: {e}")
        return None

    async def set_active_mode(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None = None
    ) -> bool:
        """Set active mode for location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                mode_row = await conn.fetchrow("SELECT id FROM room_modes WHERE name = $1", mode_name)
                if not mode_row:
                    return False
                mode_id = mode_row["id"]
                submode_id = None
                if submode_name:
                    submode_row = await conn.fetchrow(
                        "SELECT id FROM flower_submodes WHERE name = $1", submode_name
                    )
                    if submode_row:
                        submode_id = submode_row["id"]

                await conn.execute(
                    """INSERT INTO active_room_modes (location, cluster, mode_id, submode_id, updated_at)
                       VALUES ($1, $2, $3, $4, NOW())
                       ON CONFLICT (location, cluster)
                       DO UPDATE SET mode_id = $3, submode_id = $4, updated_at = NOW()""",
                    location, cluster, mode_id, submode_id
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set active mode: {e}")
            return False

    async def get_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None = None
    ) -> dict[str, Any] | None:
        """Get mode parameters."""
        try:
            async with self.pool.acquire() as conn:
                mode_row = await conn.fetchrow("SELECT id FROM room_modes WHERE name = $1", mode_name)
                if not mode_row:
                    return None
                mode_id = mode_row["id"]
                submode_id = None
                if submode_name:
                    submode_row = await conn.fetchrow(
                        "SELECT id FROM flower_submodes WHERE name = $1", submode_name
                    )
                    if submode_row:
                        submode_id = submode_row["id"]

                row = await conn.fetchrow(
                    """SELECT * FROM room_mode_parameters
                       WHERE location = $1 AND cluster = $2 AND mode_id = $3 
                       AND (submode_id = $4 OR ($4 IS NULL AND submode_id IS NULL))""",
                    location, cluster, mode_id, submode_id
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get mode parameters: {e}")
        return None

    async def save_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
        params: dict[str, Any]
    ) -> bool:
        """Save mode parameters."""
        try:
            async with self.pool.acquire() as conn:
                mode_row = await conn.fetchrow("SELECT id FROM room_modes WHERE name = $1", mode_name)
                if not mode_row:
                    return False
                mode_id = mode_row["id"]
                submode_id = None
                if submode_name:
                    submode_row = await conn.fetchrow(
                        "SELECT id FROM flower_submodes WHERE name = $1", submode_name
                    )
                    if submode_row:
                        submode_id = submode_row["id"]

                await conn.execute(
                    """INSERT INTO room_mode_parameters 
                       (location, cluster, mode_id, submode_id, day_temp_setpoint, night_temp_setpoint,
                        day_humidity_setpoint, night_humidity_setpoint, vpd_setpoint, co2_setpoint,
                        day_start, night_start, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
                       ON CONFLICT (location, cluster, mode_id, submode_id)
                       DO UPDATE SET day_temp_setpoint = $5, night_temp_setpoint = $6,
                                    day_humidity_setpoint = $7, night_humidity_setpoint = $8,
                                    vpd_setpoint = $9, co2_setpoint = $10,
                                    day_start = $11, night_start = $12, updated_at = NOW()""",
                    location, cluster, mode_id, submode_id,
                    params.get("day_temp_setpoint"), params.get("night_temp_setpoint"),
                    params.get("day_humidity_setpoint"), params.get("night_humidity_setpoint"),
                    params.get("vpd_setpoint"), params.get("co2_setpoint"),
                    params.get("day_start"), params.get("night_start")
                )
                return True
        except Exception as e:
            logger.error(f"Failed to save mode parameters: {e}")
            return False
