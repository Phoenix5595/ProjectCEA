from __future__ import annotations

from datetime import time as dt_time
from typing import TYPE_CHECKING, Any, cast

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Connection, Pool

# State manager and event bus for cache-aside and invalidation
from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus  # type: ignore
from app.state import get_state_manager  # type: ignore


class ScheduleRepository(BaseRepository):
    """Repository for schedule operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    # ----------------------------
    # Cache helpers (StateManager)
    # ----------------------------
    @staticmethod
    def _cache_key_schedules(location: str | None, cluster: str | None) -> str:
        if location and cluster:
            return f"schedules:loc:{location}:cluster:{cluster}"
        if location:
            return f"schedules:loc:{location}"
        return "schedules:all"

    @staticmethod
    def _cache_key_light(location: str, cluster: str, device_name: str) -> str:
        return f"schedules:loc:{location}:cluster:{cluster}:light:{device_name}"

    @staticmethod
    def _cache_key_room_light(location: str, cluster: str) -> str:
        return f"schedules:loc:{location}:cluster:{cluster}:room_light_schedule"

    async def _publish_schedule_changed(
        self,
        location: str | None,
        cluster: str | None,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        bus = get_event_bus()
        event = ConfigChangeEvent(
            event_type=ConfigEventType.SCHEDULE_CHANGED,
            location=location or "all",
            cluster=cluster or "all",
            config_type="schedules",
            data={"action": action, **(extra or {})},
        )
        await bus.publish(event)

    async def get_schedules(
        self, location: str | None = None, cluster: str | None = None
    ) -> list[dict[str, Any]]:
        """Get schedules, optionally filtered by location/cluster with cache-aside."""
        state = get_state_manager()
        cache_key = self._cache_key_schedules(location, cluster)
        try:
            # Try in-memory Redis-backed cache first
            cached = await state.get(cache_key)
            if cached is not None:
                return cast("list[dict[str, Any]]", cached)

            async with self.pool.acquire() as conn:
                if location and cluster:
                    rows = await conn.fetch(
                        """SELECT id, name, location, cluster, device_name, start_time, end_time,
                                  day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                  ramp_down_duration, updated_at
                           FROM schedules
                           WHERE location = $1 AND cluster = $2
                           ORDER BY start_time""",
                        location,
                        cluster,
                    )
                elif location:
                    rows = await conn.fetch(
                        """SELECT id, name, location, cluster, device_name, start_time, end_time,
                                  day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                  ramp_down_duration, updated_at
                           FROM schedules WHERE location = $1 ORDER BY start_time""",
                        location,
                    )
                else:
                    rows = await conn.fetch(
                        """SELECT id, name, location, cluster, device_name, start_time, end_time,
                                  day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                  ramp_down_duration, updated_at
                           FROM schedules ORDER BY start_time"""
                    )
                result = [dict(row) for row in rows]
                # Populate cache on miss
                await state.set(cache_key, result)
                return result
        except Exception as e:
            logger.error(f"Failed to get schedules: {e}")
            return []

    async def get_light_schedule(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get the active day schedule for a light device."""
        state = get_state_manager()
        cache_key = self._cache_key_light(location, cluster, device_name)
        try:
            cached = await state.get(cache_key)
            if cached is not None:
                return cast(dict[str, Any], cached)
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT start_time, end_time, target_intensity
                    FROM schedules
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                    AND enabled = true AND target_intensity IS NOT NULL
                    AND target_intensity > 0
                    ORDER BY target_intensity DESC, id ASC
                    LIMIT 1
                """,
                    location,
                    cluster,
                    device_name,
                )
                if row:
                    data = {
                        "start_time": str(row["start_time"])[:5],
                        "end_time": str(row["end_time"])[:5],
                        "target_intensity": row["target_intensity"],
                    }
                    await state.set(cache_key, data)
                    return data
        except Exception as e:
            logger.error(f"Failed to get light schedule: {e}")
        return None

    async def get_room_light_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get room-level light period bounds (sun/moon) from light schedules.

        Returns day_start_time, day_end_time (and ramp durations) from light schedules.
        Used for control loop and for anchoring climate mode (DAY slave to sun,
        NIGHT slave to moon). Climate parameters come from get_climate_schedule.
        """
        state = get_state_manager()
        cache_key = self._cache_key_room_light(location, cluster)
        try:
            cached = await state.get(cache_key)
            if cached is not None:
                return cast(dict[str, Any], cached)
            async with self.pool.acquire() as conn:
                # Get any enabled light schedule to determine day/night times
                row = await conn.fetchrow(
                    """
                    SELECT start_time, end_time, ramp_up_duration, ramp_down_duration
                    FROM schedules
                    WHERE location = $1 AND cluster = $2
                      AND device_name LIKE 'light%'
                      AND mode IN ('SUN', 'DAY')
                      AND enabled = true
                    ORDER BY updated_at DESC, id ASC
                    LIMIT 1
                """,
                    location,
                    cluster,
                )

                if row:
                    data = {
                        "day_start_time": str(row["start_time"])[:5],
                        "day_end_time": str(row["end_time"])[:5],
                        "ramp_up_duration": row.get("ramp_up_duration"),
                        "ramp_down_duration": row.get("ramp_down_duration"),
                    }
                    await state.set(cache_key, data)
                    return data
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
        ramp_up_duration: int | None = 30,
        ramp_down_duration: int | None = 30,
        conn: Connection | None = None,
    ) -> int | None:
        """Create a new schedule."""
        try:
            start_parts = [int(p) for p in start_time.split(":")]
            end_parts = [int(p) for p in end_time.split(":")]
            start_time_obj = dt_time(start_parts[0], start_parts[1])
            end_time_obj = dt_time(end_parts[0], end_parts[1])

            schedule_id: int | None = None

            async def do_insert(c: Connection) -> int | None:
                row = await c.fetchrow(
                    """INSERT INTO schedules (name, location, cluster, device_name, start_time, end_time,
                                             day_of_week, enabled, mode, target_intensity, ramp_up_duration,
                                             ramp_down_duration, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
                       RETURNING id""",
                    name,
                    location,
                    cluster,
                    device_name,
                    start_time_obj,
                    end_time_obj,
                    day_of_week,
                    enabled,
                    mode,
                    target_intensity,
                    ramp_up_duration,
                    ramp_down_duration,
                )
                return row["id"] if row else None

            if conn:
                # When called within a transaction, only do the insert
                # Event and cache invalidation happen after transaction commits
                schedule_id = await do_insert(conn)
            else:
                # When managing our own connection, do full operation
                async with self.pool.acquire() as new_conn:
                    schedule_id = await do_insert(cast("Connection", new_conn))

            # Only publish event and invalidate cache if insert succeeded
            # and we're not inside a transaction (conn is None)
            if schedule_id and not conn:
                logger.info(
                    f"Created schedule {schedule_id}: {name} ({location}/{cluster}) "
                    f"device={device_name} mode={mode} start={start_time} end={end_time}"
                )
                await self._publish_schedule_changed(
                    location=location,
                    cluster=cluster,
                    action="created",
                    extra={"schedule_id": schedule_id},
                )
                try:
                    s = get_state_manager()
                    await s.delete(self._cache_key_schedules(location, cluster))
                except Exception as e:
                    logger.debug("schedule cache invalidation skipped: %s", e)

            return schedule_id
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

                updates: list[str] = []
                params: list[Any] = [schedule_id]
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
                    if row:
                        logger.info(
                            f"Updated schedule {schedule_id} ({row['location']}/{row['cluster']}): "
                            f"device={row['device_name']} updates={list(kwargs.keys())}"
                        )
                        # Invalidate cache via event bus on update
                        await self._publish_schedule_changed(
                            location=row["location"],
                            cluster=row["cluster"],
                            action="updated",
                            extra={"schedule_id": row["id"]},
                        )
                        # Invalidate cache directly (following create_schedule pattern)
                        try:
                            s = get_state_manager()
                            await s.delete(
                                self._cache_key_schedules(row["location"], row["cluster"])
                            )
                            await s.delete(
                                self._cache_key_light(
                                    row["location"], row["cluster"], row["device_name"]
                                )
                            )
                        except Exception as e:
                            logger.debug("schedule cache invalidation skipped: %s", e)
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
                logger.info(f"Deleted schedule {schedule_id}")
                # Invalidate cache on delete
                await self._publish_schedule_changed(
                    location=None,
                    cluster=None,
                    action="deleted",
                    extra={"schedule_id": schedule_id},
                )
                return True
        except Exception as e:
            logger.error(f"Failed to delete schedule: {e}")
            return False

    async def delete_schedules_by_device_name(
        self, location: str, cluster: str, device_name: str
    ) -> int:
        """Delete all schedules referencing a specific device_name in a location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM schedules WHERE location = $1 AND cluster = $2 AND device_name = $3",
                    location,
                    cluster,
                    device_name,
                )
                deleted = int(result.split()[-1]) if result else 0
                if deleted:
                    await self._publish_schedule_changed(
                        location=location,
                        cluster=cluster,
                        action="deleted",
                        extra={"device_name": device_name, "count": deleted},
                    )
                return deleted
        except Exception as e:
            logger.error(f"Failed to delete schedules for {device_name}: {e}")
            return 0

    async def delete_schedules_bulk(
        self, schedule_ids: list[int], conn: Connection | None = None
    ) -> int:
        """Delete multiple schedules."""
        try:

            async def do_delete(c: Connection) -> int:
                result = await c.execute("DELETE FROM schedules WHERE id = ANY($1)", schedule_ids)
                return int(result.split()[-1]) if result else 0

            if conn:
                deleted = await do_delete(conn)
            else:
                async with self.pool.acquire() as new_conn:
                    deleted = await do_delete(cast("Connection", new_conn))
            if deleted:
                await self._publish_schedule_changed(
                    location=None,
                    cluster=None,
                    action="bulk_deleted",
                    extra={"schedule_ids": schedule_ids},
                )
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete schedules: {e}")
            return 0

    async def fix_light_schedules_day_of_week(self) -> int:
        """Force light schedules to be daily (day_of_week = NULL).

        Targets schedules where:
        - mode is SUN or DAY (light sun schedule)
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
                    WHERE mode IN ('SUN', 'DAY')
                      AND target_intensity IS NOT NULL
                      AND day_of_week IS NOT NULL
                    RETURNING id
                """)
                fixed = len(rows)
                if fixed:
                    logger.info(f"Updated {fixed} light schedules to daily (day_of_week=NULL)")
                    await self._publish_schedule_changed(
                        location=None,
                        cluster=None,
                        action="fixed_light_weekday",
                        extra={"count": fixed},
                    )
                return fixed
        except Exception as e:
            logger.error(f"Failed to fix light schedules day_of_week: {e}")
            return 0

    async def update_light_schedule_target(
        self, location: str, cluster: str, device_name: str, target_intensity: float
    ) -> int:
        """Update target_intensity for a light device's sun schedule (not moon).

        Returns:
            Number of schedule rows updated (0 if none matched).
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    UPDATE schedules
                    SET target_intensity = $4, updated_at = NOW()
                    WHERE location = $1 AND cluster = $2 AND device_name = $3
                    AND enabled = true AND mode IN ('SUN', 'DAY')
                    RETURNING id
                """,
                    location,
                    cluster,
                    device_name,
                    target_intensity,
                )
                n = len(rows)
                if n:
                    await self._publish_schedule_changed(
                        location=location,
                        cluster=cluster,
                        action="updated",
                        extra={"device_name": device_name},
                    )
                    # Invalidate cache directly (following create_schedule pattern)
                    try:
                        s = get_state_manager()
                        await s.delete(self._cache_key_schedules(location, cluster))
                        await s.delete(self._cache_key_schedules(location, None))
                        # POST /lights/.../target uses get_schedules() with no filter → key schedules:all
                        await s.delete(self._cache_key_schedules(None, None))
                        await s.delete(self._cache_key_light(location, cluster, device_name))
                    except Exception as e:
                        logger.debug("light schedule cache invalidation skipped: %s", e)
                return n
        except Exception as e:
            logger.error(f"Failed to update light schedule target: {e}")
            return 0

    async def get_room_schedule(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get room schedule (day/night times) for a location/cluster.

        DEPRECATED: room_schedule rows are deleted in T10. This method now
        returns None so callers fall back to mode_parameters.
        """
        return None

    async def update_light_schedule_ramp_times(
        self,
        location: str,
        cluster: str,
        ramp_up_minutes: int,
        ramp_down_minutes: int,
    ) -> int:
        """Update ramp times for all DAY light schedules and room_schedule in a location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    """
                    UPDATE schedules
                    SET ramp_up_duration = $3, ramp_down_duration = $4
                    WHERE location = $1 AND cluster = $2
                    AND (
                        device_name LIKE 'light%' AND mode IN ('SUN', 'DAY')
                        OR device_name = 'room_schedule'
                    )
                    """,
                    location,
                    cluster,
                    ramp_up_minutes,
                    ramp_down_minutes,
                )
                count = int(result.split()[-1])
                logger.info(f"Updated {count} light schedules ramp times for {location}/{cluster}")
                if count:
                    # Invalidate cache BEFORE publishing event so consumers get fresh data
                    try:
                        s = get_state_manager()
                        # Clear location-specific keys
                        await s.delete(self._cache_key_schedules(location, cluster))
                        await s.delete(self._cache_key_room_light(location, cluster))
                        # Clear global schedules cache - critical for consistency
                        await s.delete(self._cache_key_schedules(None, None))
                    except Exception as e:
                        logger.debug("ramp-time cache invalidation skipped: %s", e)
                    await self._publish_schedule_changed(
                        location=location,
                        cluster=cluster,
                        action="ramp_times_updated",
                        extra={"count": count},
                    )
                return count
        except Exception as e:
            logger.error(f"Failed to update light schedule ramp times: {e}")
            return 0
