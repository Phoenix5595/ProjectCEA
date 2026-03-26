from __future__ import annotations

from datetime import time as dt_time
import time
from typing import TYPE_CHECKING, Any, cast

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Connection, Pool


class RoomModeRepository(BaseRepository):
    """Repository for room mode operations.

    Handles mode_parameters table with photoperiod/light settings only:
    - Time settings (day/night start)
    - Light settings (main/supplemental intensity, ramp durations)
    """

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)
        # Cache for mode_id and submode_id lookups with 60-second TTL
        self._mode_id_cache: dict[str, tuple[int, float]] = {}
        self._submode_id_cache: dict[str, tuple[int, float]] = {}

    async def _get_mode_id_cached(self, conn: Any, mode_name: str) -> int | None:
        conn = cast("Connection", conn)
        cache_key = mode_name
        if cache_key in self._mode_id_cache:
            mode_id, ts = self._mode_id_cache[cache_key]
            if time.time() - ts < 60:
                return mode_id

        result = await conn.fetchrow("SELECT id FROM room_modes WHERE name = $1", mode_name)
        if result:
            self._mode_id_cache[cache_key] = (result["id"], time.time())
            return result["id"]
        return None

    async def _get_submode_id_cached(
        self, conn: Any, mode_name: str, submode_name: str
    ) -> int | None:
        conn = cast("Connection", conn)
        cache_key = f"{mode_name}:{submode_name}"
        if cache_key in self._submode_id_cache:
            submode_id, ts = self._submode_id_cache[cache_key]
            if time.time() - ts < 60:
                return submode_id

        result = await conn.fetchrow("SELECT id FROM flower_submodes WHERE name = $1", submode_name)
        if result:
            self._submode_id_cache[cache_key] = (result["id"], time.time())
            return result["id"]
        return None

    async def get_room_modes(self) -> list[dict[str, Any]]:
        """Get all available room modes."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                rows = await conn.fetch("SELECT * FROM room_modes ORDER BY id")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get room modes: {e}")
            return []

    async def get_flower_submodes(self) -> list[dict[str, Any]]:
        """Get flower submodes."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                rows = await conn.fetch("SELECT * FROM flower_submodes ORDER BY id")
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get flower submodes: {e}")
            return []

    async def get_active_mode(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get active mode for location/cluster."""
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
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
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                mode_id = await self._get_mode_id_cached(conn, mode_name)
                if not mode_id:
                    return False

                submode_id = None
                if submode_name:
                    submode_id = await self._get_submode_id_cached(conn, mode_name, submode_name)

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

        Returns photoperiod/light settings only (6 operational fields):
        day_start_time, night_start_time, light_ramp_up_minutes, light_ramp_down_minutes,
        main_light_intensity, supplemental_light_intensity.
        """
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                mode_id = await self._get_mode_id_cached(conn, mode_name)
                if not mode_id:
                    return None

                submode_id = None
                if submode_name:
                    submode_id = await self._get_submode_id_cached(conn, mode_name, submode_name)

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

        Handles photoperiod/light settings only (6 operational columns):
        - Time settings: day_start_time, night_start_time
        - Light ramps: light_ramp_up_minutes, light_ramp_down_minutes
        - Light settings: main/supplemental intensity
        """
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                mode_id = await self._get_mode_id_cached(conn, mode_name)
                if not mode_id:
                    logger.error(f"Mode '{mode_name}' not found")
                    return False

                submode_id = None
                if submode_name:
                    submode_id = await self._get_submode_id_cached(conn, mode_name, submode_name)

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
                            day_start_time = $1, night_start_time = $2,
                            light_ramp_up_minutes = $3, light_ramp_down_minutes = $4,
                            main_light_intensity = $5, supplemental_light_intensity = $6,
                            updated_at = NOW()
                        WHERE id = $7
                    """,
                        day_start,
                        night_start,
                        params.get("light_ramp_up_minutes", 15),
                        params.get("light_ramp_down_minutes", 15),
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
                            day_start_time, night_start_time,
                            light_ramp_up_minutes, light_ramp_down_minutes,
                            main_light_intensity, supplemental_light_intensity,
                            updated_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                    """,
                        location,
                        cluster,
                        mode_id,
                        submode_id,
                        day_start,
                        night_start,
                        params.get("light_ramp_up_minutes", 15),
                        params.get("light_ramp_down_minutes", 15),
                        params.get("main_light_intensity", 100),
                        params.get("supplemental_light_intensity", 0),
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to save mode parameters: {e}")
            return False

    async def set_mode_with_transaction(
        self,
        location: str,
        cluster: str,
        new_mode: str,
        new_submode: str | None = None,
        save_current_params: bool = True,
    ) -> dict[str, Any] | None:
        """Set active mode with all operations in a single transaction.

        This method batches the following operations in a single transaction:
        1. Get current active mode
        2. Save current mode parameters (if exists and save_current_params=True)
        3. Set new active mode

        Args:
            location: Room location
            cluster: Room cluster
            new_mode: Name of new mode to activate
            new_submode: Optional submode name
            save_current_params: Whether to save current mode parameters before switching

        Returns:
            Dict with new active mode data, or None if mode not found
        """
        start_time = time.perf_counter()
        try:
            async with self.pool.acquire() as conn_raw:
                conn: Connection = cast("Connection", conn_raw)
                async with conn.transaction():
                    # Step 1: Get current active mode
                    current = await conn.fetchrow(
                        """SELECT arm.location, arm.cluster, rm.name as mode_name, fs.name as submode_name, arm.mode_id, arm.submode_id
                           FROM room_active_mode arm
                           JOIN room_modes rm ON rm.id = arm.mode_id
                           LEFT JOIN flower_submodes fs ON fs.id = arm.submode_id
                           WHERE arm.location = $1 AND arm.cluster = $2""",
                        location,
                        cluster,
                    )

                    # Step 2: Save current mode parameters if requested and current mode exists
                    if save_current_params and current:
                        current_mode_id = current["mode_id"]
                        current_submode_id = current.get("submode_id")

                        # Get current mode parameters
                        if current_submode_id:
                            params_row = await conn.fetchrow(
                                """
                                SELECT * FROM mode_parameters
                                WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id = $4
                            """,
                                location,
                                cluster,
                                current_mode_id,
                                current_submode_id,
                            )
                        else:
                            params_row = await conn.fetchrow(
                                """
                                SELECT * FROM mode_parameters
                                WHERE location = $1 AND cluster = $2 AND mode_id = $3 AND submode_id IS NULL
                            """,
                                location,
                                cluster,
                                current_mode_id,
                            )

                        # Save parameters if they exist
                        if params_row:
                            params = dict(params_row)

                            # Check if record exists
                            existing = await conn.fetchval(
                                """
                                SELECT id FROM mode_parameters
                                WHERE location = $1 AND cluster = $2 AND mode_id = $3
                                AND COALESCE(submode_id, -1) = COALESCE($4, -1)
                            """,
                                location,
                                cluster,
                                current_mode_id,
                                current_submode_id,
                            )

                            if existing:
                                # UPDATE existing record
                                await conn.execute(
                                    """
                                    UPDATE mode_parameters SET
                                        day_start_time = $1, night_start_time = $2,
                                        light_ramp_up_minutes = $3, light_ramp_down_minutes = $4,
                                        main_light_intensity = $5, supplemental_light_intensity = $6,
                                        updated_at = NOW()
                                    WHERE id = $7
                                """,
                                    params["day_start_time"],
                                    params["night_start_time"],
                                    params["light_ramp_up_minutes"],
                                    params["light_ramp_down_minutes"],
                                    params["main_light_intensity"],
                                    params["supplemental_light_intensity"],
                                    existing,
                                )

                    # Step 3: Set new active mode
                    new_mode_id = await self._get_mode_id_cached(conn, new_mode)
                    if not new_mode_id:
                        logger.error(f"Mode '{new_mode}' not found")
                        return None

                    new_submode_id = None
                    if new_submode:
                        new_submode_id = await self._get_submode_id_cached(
                            conn, new_mode, new_submode
                        )

                    await conn.execute(
                        """INSERT INTO room_active_mode (location, cluster, mode_id, submode_id, activated_at)
                           VALUES ($1, $2, $3, $4, NOW())
                           ON CONFLICT (location, cluster)
                           DO UPDATE SET mode_id = $3, submode_id = $4, activated_at = NOW()""",
                        location,
                        cluster,
                        new_mode_id,
                        new_submode_id,
                    )

                    # Return new active mode data
                    result = await conn.fetchrow(
                        """SELECT arm.location, arm.cluster, rm.name as mode_name, fs.name as submode_name, arm.mode_id, arm.submode_id
                           FROM room_active_mode arm
                           JOIN room_modes rm ON rm.id = arm.mode_id
                           LEFT JOIN flower_submodes fs ON fs.id = arm.submode_id
                           WHERE arm.location = $1 AND arm.cluster = $2""",
                        location,
                        cluster,
                    )

                    elapsed = (time.perf_counter() - start_time) * 1000
                    logger.info(
                        f"MODE_SWITCH_BATCHED_TIMING: set_mode_with_transaction completed in {elapsed:.2f}ms"
                    )
                    return dict(result) if result else None

        except Exception as e:
            logger.error(f"Failed to set mode with transaction: {e}")
            return None
