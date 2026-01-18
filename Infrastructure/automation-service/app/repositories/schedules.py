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

    async def get_schedules(
        self,
        location: str | None = None,
        cluster: str | None = None
    ) -> list[dict[str, Any]]:
        """Get schedules, optionally filtered by location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                if location and cluster:
                    rows = await conn.fetch(
                        """SELECT id, name, location, cluster, device_name, start_time, end_time,
                                  day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                  ramp_down_duration, updated_at
                           FROM schedules 
                           WHERE location = $1 AND cluster = $2
                           ORDER BY start_time""",
                        location, cluster
                    )
                elif location:
                    rows = await conn.fetch(
                        """SELECT id, name, location, cluster, device_name, start_time, end_time,
                                  day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                  ramp_down_duration, updated_at
                           FROM schedules WHERE location = $1 ORDER BY start_time""",
                        location
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT id, name, location, cluster, device_name, start_time, end_time,
                                  day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                  ramp_down_duration, updated_at
                           FROM schedules ORDER BY start_time"""
                    )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get schedules: {e}")
            return []

    async def get_climate_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get climate schedule data (pre-day/pre-night durations) for a location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT pre_day_duration, pre_night_duration
                    FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND (pre_day_duration IS NOT NULL OR pre_night_duration IS NOT NULL)
                    ORDER BY id DESC
                    LIMIT 1
                """, location, cluster)
                
                if row:
                    return {
                        'pre_day_duration': row['pre_day_duration'] or 0,
                        'pre_night_duration': row['pre_night_duration'] or 0
                    }
                
                # If no climate schedule found, return defaults
                return {
                    'pre_day_duration': 0,
                    'pre_night_duration': 0
                }
        except Exception as e:
            logger.error(f"Failed to get climate schedule: {e}")
            return {'pre_day_duration': 0, 'pre_night_duration': 0}

    async def get_light_schedule(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get the active day schedule for a light device."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT start_time, end_time, target_intensity
                    FROM schedules
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                    AND enabled = true AND target_intensity IS NOT NULL
                    AND target_intensity > 0
                    ORDER BY target_intensity DESC
                    LIMIT 1
                """, location, cluster, device_name)
                if row:
                    return {
                        "start_time": str(row["start_time"])[:5],
                        "end_time": str(row["end_time"])[:5],
                        "target_intensity": row["target_intensity"]
                    }
        except Exception as e:
            logger.error(f"Failed to get light schedule: {e}")
        return None

    async def get_room_light_schedule(
        self, location: str, cluster: str
    ) -> dict[str, Any] | None:
        """Get room-level light schedule (day start/end times) for control loop."""
        try:
            async with self.pool.acquire() as conn:
                # Get any enabled light schedule to determine day/night times
                row = await conn.fetchrow("""
                    SELECT start_time, end_time
                    FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND device_name LIKE 'light%'
                      AND mode = 'DAY'
                      AND enabled = true
                    ORDER BY id DESC
                    LIMIT 1
                """, location, cluster)
                
                if row:
                    return {
                        'day_start_time': str(row['start_time'])[:5],
                        'day_end_time': str(row['end_time'])[:5]
                    }
        except Exception as e:
            logger.error(f"Failed to get room light schedule: {e}")
        return None

    async def create_schedule(
        self,
        name: str,
        location: str,
        cluster: str,
        device_name: str,
        start_time: str,
        end_time: str,
        day_of_week: list[int] | None = None,
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

    async def fix_light_schedules_day_of_week(self) -> int:
        """Force light schedules to be daily (day_of_week = NULL).
        
        Targets schedules where:
        - mode = 'DAY'
        - target_intensity IS NOT NULL (light dimming schedule)
        - day_of_week IS NOT NULL (invalid for lights)
        
        Returns:
            Number of schedules updated.
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch("""
                    UPDATE schedules
                    SET day_of_week = NULL
                    WHERE mode = 'DAY'
                      AND target_intensity IS NOT NULL
                      AND day_of_week IS NOT NULL
                    RETURNING id
                """)
                fixed = len(rows)
                if fixed:
                    logger.info(f"Updated {fixed} light schedules to daily (day_of_week=NULL)")
                return fixed
        except Exception as e:
            logger.error(f"Failed to fix light schedules day_of_week: {e}")
            return 0

    async def update_light_schedule_target(
        self, location: str, cluster: str, device_name: str, target_intensity: float
    ) -> bool:
        """Update target_intensity for a light device's active schedule."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE schedules 
                    SET target_intensity = $4, updated_at = NOW()
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                    AND enabled = true AND target_intensity IS NOT NULL
                """, location, cluster, device_name, target_intensity)
                return result != "UPDATE 0"
        except Exception as e:
            logger.error(f"Failed to update light schedule target: {e}")
            return False

    async def update_light_schedule_times(
        self, location: str, cluster: str, device_name: str,
        start_time: str, end_time: str
    ) -> bool:
        """Update start/end times for a light device's day schedule."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute("""
                    UPDATE schedules 
                    SET start_time = $4::time, end_time = $5::time, updated_at = NOW()
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                    AND enabled = true AND target_intensity IS NOT NULL
                    AND target_intensity > 0
                """, location, cluster, device_name, start_time, end_time)
                return result != "UPDATE 0"
        except Exception as e:
            logger.error(f"Failed to update light schedule times: {e}")
            return False
