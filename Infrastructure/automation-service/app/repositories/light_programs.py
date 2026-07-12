from __future__ import annotations

from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class LightProgramsRepository(BaseRepository):
    """Repository for programmable light schedules (supplemental/override programs)."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_active_programs(
        self, location: str, cluster: str, mode_id: int
    ) -> list[dict[str, Any]]:
        """Get enabled programs for a room/cluster, optionally matching a mode."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, device_id, location, cluster, mode_id, name, program_type,
                              start_time, end_time, cycle_enabled, cycle_on_seconds, cycle_off_seconds,
                              target_intensity, ramp_up_minutes, ramp_down_minutes, day_of_week,
                              enabled, priority, created_at, updated_at
                       FROM light_programs
                       WHERE location = $1 AND cluster = $2
                         AND enabled = TRUE
                         AND (mode_id = $3 OR mode_id IS NULL)
                       ORDER BY priority DESC, id ASC""",
                    location,
                    cluster,
                    mode_id,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get active light programs for {location}/{cluster}: {e}")
            return []

    async def get_programs_for_device(self, device_id: int) -> list[dict[str, Any]]:
        """Get all programs associated with a specific device."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, device_id, location, cluster, mode_id, name, program_type,
                              start_time, end_time, cycle_enabled, cycle_on_seconds, cycle_off_seconds,
                              target_intensity, ramp_up_minutes, ramp_down_minutes, day_of_week,
                              enabled, priority, created_at, updated_at
                       FROM light_programs
                       WHERE device_id = $1
                       ORDER BY priority DESC, id ASC""",
                    device_id,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get light programs for device {device_id}: {e}")
            return []

    async def create_program(
        self,
        *,
        name: str,
        program_type: str,
        start_time: dt_time,
        end_time: dt_time,
        target_intensity: float,
        location: str,
        cluster: str = "main",
        device_id: int | None = None,
        mode_id: int | None = None,
        cycle_enabled: bool = False,
        cycle_on_seconds: int | None = None,
        cycle_off_seconds: int | None = None,
        ramp_up_minutes: int = 0,
        ramp_down_minutes: int = 0,
        day_of_week: int | None = None,
        enabled: bool = True,
        priority: int = 0,
    ) -> dict[str, Any]:
        """Create a new light program and return the created row."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO light_programs (
                           device_id, location, cluster, mode_id, name, program_type,
                           start_time, end_time, cycle_enabled, cycle_on_seconds, cycle_off_seconds,
                           target_intensity, ramp_up_minutes, ramp_down_minutes, day_of_week,
                           enabled, priority, updated_at
                       ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, NOW())
                       RETURNING *""",
                    device_id,
                    location,
                    cluster,
                    mode_id,
                    name,
                    program_type,
                    start_time,
                    end_time,
                    cycle_enabled,
                    cycle_on_seconds,
                    cycle_off_seconds,
                    target_intensity,
                    ramp_up_minutes,
                    ramp_down_minutes,
                    day_of_week,
                    enabled,
                    priority,
                )
                return dict(row)
        except Exception as e:
            logger.error(f"Failed to create light program '{name}': {e}")
            raise

    async def update_program(
        self,
        id: int,
        *,
        name: str | None = None,
        program_type: str | None = None,
        start_time: dt_time | None = None,
        end_time: dt_time | None = None,
        target_intensity: float | None = None,
        device_id: int | None = None,
        mode_id: int | None = None,
        cycle_enabled: bool | None = None,
        cycle_on_seconds: int | None = None,
        cycle_off_seconds: int | None = None,
        ramp_up_minutes: int | None = None,
        ramp_down_minutes: int | None = None,
        day_of_week: int | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> dict[str, Any] | None:
        """Update an existing light program by id."""
        try:
            async with self.pool.acquire() as conn:
                current = await conn.fetchrow("SELECT * FROM light_programs WHERE id = $1", id)
                if not current:
                    return None

                row = await conn.fetchrow(
                    """UPDATE light_programs SET
                           name = COALESCE($1, name),
                           program_type = COALESCE($2, program_type),
                           start_time = COALESCE($3, start_time),
                           end_time = COALESCE($4, end_time),
                           target_intensity = COALESCE($5, target_intensity),
                           device_id = COALESCE($6, device_id),
                           mode_id = COALESCE($7, mode_id),
                           cycle_enabled = COALESCE($8, cycle_enabled),
                           cycle_on_seconds = COALESCE($9, cycle_on_seconds),
                           cycle_off_seconds = COALESCE($10, cycle_off_seconds),
                           ramp_up_minutes = COALESCE($11, ramp_up_minutes),
                           ramp_down_minutes = COALESCE($12, ramp_down_minutes),
                           day_of_week = COALESCE($13, day_of_week),
                           enabled = COALESCE($14, enabled),
                           priority = COALESCE($15, priority),
                           updated_at = NOW()
                       WHERE id = $16
                       RETURNING *""",
                    name,
                    program_type,
                    start_time,
                    end_time,
                    target_intensity,
                    device_id,
                    mode_id,
                    cycle_enabled,
                    cycle_on_seconds,
                    cycle_off_seconds,
                    ramp_up_minutes,
                    ramp_down_minutes,
                    day_of_week,
                    enabled,
                    priority,
                    id,
                )
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Failed to update light program {id}: {e}")
            return None

    async def delete_program(self, id: int) -> bool:
        """Delete a light program by id."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("DELETE FROM light_programs WHERE id = $1", id)
                return result.split()[-1] != "0"
        except Exception as e:
            logger.error(f"Failed to delete light program {id}: {e}")
            return False

    async def get_all_programs(self) -> list[dict[str, Any]]:
        """Get all light programs ordered by priority and id."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, device_id, location, cluster, mode_id, name, program_type,
                              start_time, end_time, cycle_enabled, cycle_on_seconds, cycle_off_seconds,
                              target_intensity, ramp_up_minutes, ramp_down_minutes, day_of_week,
                              enabled, priority, created_at, updated_at
                       FROM light_programs
                       ORDER BY priority DESC, id ASC"""
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all light programs: {e}")
            return []
