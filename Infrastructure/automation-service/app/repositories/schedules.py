from __future__ import annotations

from datetime import time as dt_time
from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool, Connection


class ScheduleRepository(BaseRepository):
    """Repository for schedule operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_schedules(self, location: str, cluster: str) -> list[dict[str, Any]]:
        """Get all schedules for location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT id, name, location, cluster, device_name, start_time, end_time,
                              day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                              ramp_down_duration, updated_at
                       FROM schedules 
                       WHERE location = $1 AND cluster = $2
                       ORDER BY start_time""",
                    location, cluster
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get schedules: {e}")
            return []

    async def get_climate_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get climate schedule for location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM schedules 
                       WHERE location = $1 AND cluster = $2 AND mode = 'climate'
                       ORDER BY updated_at DESC LIMIT 1""",
                    location, cluster
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get climate schedule: {e}")
        return None

    async def get_light_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get light schedule for location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM schedules 
                       WHERE location = $1 AND cluster = $2 AND mode = 'light'
                       ORDER BY updated_at DESC LIMIT 1""",
                    location, cluster
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get light schedule: {e}")
        return None

    async def create_schedule(
        self,
        name: str,
        location: str,
        cluster: str,
        device_name: str,
        start_time: str,
        end_time: str,
        day_of_week: list[int],
        enabled: bool = True,
        mode: str = "light",
        target_intensity: float | None = None,
        ramp_up_duration: int = 30,
        ramp_down_duration: int = 30,
        conn: Connection | None = None
    ) -> int | None:
        """Create a new schedule."""
        try:
            start_parts = [int(p) for p in start_time.split(":")]
            end_parts = [int(p) for p in end_time.split(":")]
            start_time_obj = dt_time(start_parts[0], start_parts[1])
            end_time_obj = dt_time(end_parts[0], end_parts[1])

            async def do_insert(c: Connection) -> int | None:
                row = await c.fetchrow(
                    """INSERT INTO schedules (name, location, cluster, device_name, start_time, end_time,
                                             day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                             ramp_down_duration, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
                       RETURNING id""",
                    name, location, cluster, device_name, start_time_obj, end_time_obj,
                    day_of_week, enabled, mode, target_intensity, ramp_up_duration, ramp_down_duration
                )
                return row["id"] if row else None

            if conn:
                return await do_insert(conn)
            async with self.pool.acquire() as new_conn:
                return await do_insert(new_conn)
        except Exception as e:
            logger.error(f"Failed to create schedule: {e}")
            return None

    async def update_schedule(self, schedule_id: int, **kwargs: Any) -> dict[str, Any] | None:
        """Update a schedule."""
        try:
            async with self.pool.acquire() as conn:
                current = await conn.fetchrow("SELECT * FROM schedules WHERE id = $1", schedule_id)
                if not current:
                    return None

                updates = []
                params = [schedule_id]
                param_idx = 2

                for key, value in kwargs.items():
                    if value is not None and key not in ("expected_version",):
                        if key == "start_time" and isinstance(value, str):
                            parts = [int(p) for p in value.split(":")]
                            value = dt_time(parts[0], parts[1])
                        elif key == "end_time" and isinstance(value, str):
                            parts = [int(p) for p in value.split(":")]
                            value = dt_time(parts[0], parts[1])
                        updates.append(f"{key} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                if updates:
                    query = f"UPDATE schedules SET {', '.join(updates)}, updated_at = NOW() WHERE id = $1 RETURNING *"
                    row = await conn.fetchrow(query, *params)
                    return dict(row) if row else None
                return dict(current)
        except Exception as e:
            logger.error(f"Failed to update schedule: {e}")
            return None

    async def delete_schedule(self, schedule_id: int) -> bool:
        """Delete a schedule."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("DELETE FROM schedules WHERE id = $1", schedule_id)
                return True
        except Exception as e:
            logger.error(f"Failed to delete schedule: {e}")
            return False

    async def delete_schedules_bulk(self, schedule_ids: list[int], conn: Connection | None = None) -> int:
        """Delete multiple schedules."""
        try:
            async def do_delete(c: Connection) -> int:
                result = await c.execute("DELETE FROM schedules WHERE id = ANY($1)", schedule_ids)
                return int(result.split()[-1]) if result else 0

            if conn:
                return await do_delete(conn)
            async with self.pool.acquire() as new_conn:
                return await do_delete(new_conn)
        except Exception as e:
            logger.error(f"Failed to delete schedules: {e}")
            return 0
