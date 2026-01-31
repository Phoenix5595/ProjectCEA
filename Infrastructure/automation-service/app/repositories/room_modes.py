from __future__ import annotations

from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class RoomModeRepository(BaseRepository):
    """Repository for room mode operations.

    Handles mode_parameters table with 34 columns including:
    - Time settings (day/night start, ramp durations)
    - Pre-transition settings (pre_day/pre_night phases)
    - Climate setpoints (heat/cool temps, VPD, CO2, leaf delta)
    - Light settings (main/supplemental intensity, ramp durations)
    """

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
                    """SELECT arm.location, arm.cluster, rm.name as mode_name, fs.name as submode_name, arm.mode_id, arm.submode_id
                       FROM room_active_mode arm
                       JOIN room_modes rm ON rm.id = arm.mode_id
                       LEFT JOIN flower_submodes fs ON fs.id = arm.submode_id
                       WHERE arm.location = $1 AND arm.cluster = $2""",
                    location,
                    cluster,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get active mode: {e}")
        return None

    async def set_active_mode(
        self, location: str, cluster: str, mode_name: str, submode_name: str | None = None
    ) -> bool:
        """Set active mode for location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                mode_row = await conn.fetchrow(
                    "SELECT id FROM room_modes WHERE name = $1", mode_name
                )
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
                    """INSERT INTO room_active_mode (location, cluster, mode_id, submode_id, activated_at)
                       VALUES ($1, $2, $3, $4, NOW())
                       ON CONFLICT (location, cluster)
                       DO UPDATE SET mode_id = $3, submode_id = $4, activated_at = NOW()""",
                    location,
                    cluster,
                    mode_id,
                    submode_id,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set active mode: {e}")
            return False

    async def get_mode_parameters(
        self, location: str, cluster: str, mode_name: str, submode_name: str | None = None
    ) -> dict[str, Any] | None:
        """Get mode parameters from mode_parameters table.

        Returns all 34 columns including time settings, pre-transition phases,
        climate setpoints, and light settings.
        """
        try:
            async with self.pool.acquire() as conn:
                mode_row = await conn.fetchrow(
                    "SELECT id FROM room_modes WHERE name = $1", mode_name
                )
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

                if submode_id:
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM mode_parameters
                        WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id = $4
                    """,
                        location,
                        cluster,
                        mode_id,
                        submode_id,
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT * FROM mode_parameters
                        WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id IS NULL
                    """,
                        location,
                        cluster,
                        mode_id,
                    )

                if row:
                    result = dict(row)
                    # Format time fields as HH:MM strings
                    result["day_start_time"] = (
                        str(result["day_start_time"])[:5]
                        if result.get("day_start_time")
                        else "06:00"
                    )
                    result["night_start_time"] = (
                        str(result["night_start_time"])[:5]
                        if result.get("night_start_time")
                        else "18:00"
                    )
                    return result
        except Exception as e:
            logger.error(f"Failed to get mode parameters: {e}")
        return None

    async def save_mode_parameters(
        self,
        location: str,
        cluster: str,
        mode_name: str,
        submode_name: str | None,
        params: dict[str, Any],
    ) -> bool:
        """Save mode parameters to mode_parameters table.

        Handles all 34 columns with proper defaults:
        - Time settings: day_start_time, night_start_time, ramp durations
        - Pre-transition: pre_day/pre_night ramp and phase durations
        - Light ramps: light_ramp_up_minutes, light_ramp_down_minutes
        - Day climate: heat/cool temps, VPD, CO2, leaf delta
        - Night climate: heat/cool temps, VPD, CO2, leaf delta
        - Pre-day climate: heat/cool temps, VPD, CO2
        - Pre-night climate: heat/cool temps, VPD, CO2
        - Light settings: main/supplemental intensity
        """
        try:
            async with self.pool.acquire() as conn:
                mode_row = await conn.fetchrow(
                    "SELECT id FROM room_modes WHERE name = $1", mode_name
                )
                if not mode_row:
                    logger.error(f"Mode '{mode_name}' not found")
                    return False
                mode_id = mode_row["id"]

                submode_id = None
                if submode_name:
                    submode_row = await conn.fetchrow(
                        "SELECT id FROM flower_submodes WHERE name = $1", submode_name
                    )
                    if submode_row:
                        submode_id = submode_row["id"]

                # Parse time strings to time objects
                day_start = params.get("day_start_time", "06:00")
                night_start = params.get("night_start_time", "18:00")
                if isinstance(day_start, str):
                    parts = day_start.split(":")
                    day_start = dt_time(int(parts[0]), int(parts[1]))
                if isinstance(night_start, str):
                    parts = night_start.split(":")
                    night_start = dt_time(int(parts[0]), int(parts[1]))

                # Check if record exists
                existing = await conn.fetchval(
                    """
                    SELECT id FROM mode_parameters
                    WHERE location = $1 AND cluster = $2 AND mode_id = $3
                    AND COALESCE(submode_id, -1) = COALESCE($4, -1)
                """,
                    location,
                    cluster,
                    mode_id,
                    submode_id,
                )

                if existing:
                    # UPDATE existing record
                    await conn.execute(
                        """
                        UPDATE mode_parameters SET
                            day_start_time = $1, night_start_time = $2, ramp_up_minutes = $3, ramp_down_minutes = $4,
                            pre_day_ramp_minutes = $5, pre_night_ramp_minutes = $6,
                            pre_day_minutes = $7, pre_night_minutes = $8,
                            light_ramp_up_minutes = $9, light_ramp_down_minutes = $10,
                            day_heat_temp = $11, day_cool_temp = $12, day_vpd = $13, day_co2 = $14, day_leaf_delta = $15,
                            night_heat_temp = $16, night_cool_temp = $17, night_vpd = $18, night_co2 = $19, night_leaf_delta = $20,
                            pre_day_heat_temp = $21, pre_day_cool_temp = $22, pre_day_vpd = $23, pre_day_co2 = $24,
                            pre_night_heat_temp = $25, pre_night_cool_temp = $26, pre_night_vpd = $27, pre_night_co2 = $28,
                            main_light_intensity = $29, supplemental_light_intensity = $30, updated_at = NOW()
                        WHERE id = $31
                    """,
                        day_start,
                        night_start,
                        params.get("ramp_up_minutes", 30),
                        params.get("ramp_down_minutes", 30),
                        params.get("pre_day_ramp_minutes", 30),
                        params.get("pre_night_ramp_minutes", 30),
                        params.get("pre_day_minutes", 30),
                        params.get("pre_night_minutes", 30),
                        params.get("light_ramp_up_minutes", 15),
                        params.get("light_ramp_down_minutes", 15),
                        params.get("day_heat_temp", 24.0),
                        params.get("day_cool_temp", 28.0),
                        params.get("day_vpd", 1.2),
                        params.get("day_co2", 800),
                        params.get("day_leaf_delta", -2.0),
                        params.get("night_heat_temp", 20.0),
                        params.get("night_cool_temp", 24.0),
                        params.get("night_vpd", 1.2),
                        params.get("night_co2", 600),
                        params.get("night_leaf_delta", -1.0),
                        params.get("pre_day_heat_temp", 22.0),
                        params.get("pre_day_cool_temp", 26.0),
                        params.get("pre_day_vpd", 1.2),
                        params.get("pre_day_co2", 700),
                        params.get("pre_night_heat_temp", 22.0),
                        params.get("pre_night_cool_temp", 26.0),
                        params.get("pre_night_vpd", 1.2),
                        params.get("pre_night_co2", 700),
                        params.get("main_light_intensity", 100),
                        params.get("supplemental_light_intensity", 0),
                        existing,
                    )
                else:
                    # INSERT new record
                    await conn.execute(
                        """
                        INSERT INTO mode_parameters (
                            location, cluster, mode_id, submode_id,
                            day_start_time, night_start_time, ramp_up_minutes, ramp_down_minutes,
                            pre_day_ramp_minutes, pre_night_ramp_minutes,
                            pre_day_minutes, pre_night_minutes,
                            light_ramp_up_minutes, light_ramp_down_minutes,
                            day_heat_temp, day_cool_temp, day_vpd, day_co2, day_leaf_delta,
                            night_heat_temp, night_cool_temp, night_vpd, night_co2, night_leaf_delta,
                            pre_day_heat_temp, pre_day_cool_temp, pre_day_vpd, pre_day_co2,
                            pre_night_heat_temp, pre_night_cool_temp, pre_night_vpd, pre_night_co2,
                            main_light_intensity, supplemental_light_intensity, updated_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31, $32, $33, $34, NOW())
                    """,
                        location,
                        cluster,
                        mode_id,
                        submode_id,
                        day_start,
                        night_start,
                        params.get("ramp_up_minutes", 30),
                        params.get("ramp_down_minutes", 30),
                        params.get("pre_day_ramp_minutes", 30),
                        params.get("pre_night_ramp_minutes", 30),
                        params.get("pre_day_minutes", 30),
                        params.get("pre_night_minutes", 30),
                        params.get("light_ramp_up_minutes", 15),
                        params.get("light_ramp_down_minutes", 15),
                        params.get("day_heat_temp", 24.0),
                        params.get("day_cool_temp", 28.0),
                        params.get("day_vpd", 1.2),
                        params.get("day_co2", 800),
                        params.get("day_leaf_delta", -2.0),
                        params.get("night_heat_temp", 20.0),
                        params.get("night_cool_temp", 24.0),
                        params.get("night_vpd", 1.2),
                        params.get("night_co2", 600),
                        params.get("night_leaf_delta", -1.0),
                        params.get("pre_day_heat_temp", 22.0),
                        params.get("pre_day_cool_temp", 26.0),
                        params.get("pre_day_vpd", 1.2),
                        params.get("pre_day_co2", 700),
                        params.get("pre_night_heat_temp", 22.0),
                        params.get("pre_night_cool_temp", 26.0),
                        params.get("pre_night_vpd", 1.2),
                        params.get("pre_night_co2", 700),
                        params.get("main_light_intensity", 100),
                        params.get("supplemental_light_intensity", 0),
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to save mode parameters: {e}")
            return False
