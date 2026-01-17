from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class SetpointRepository(BaseRepository):
    """Repository for setpoint operations."""

    def __init__(self, pool: Pool | None = None, redis_client: Any = None) -> None:
        super().__init__(pool)
        self._redis_client = redis_client

    def set_redis_client(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def get_setpoint(self, location: str, cluster: str, mode: str = "main") -> dict[str, Any] | None:
        """Get setpoint for location/cluster/mode."""
        db_mode = mode if mode != "main" else "day"
        cache_key = self._get_cache_key("get_setpoint", location, cluster, db_mode)
        cached = self._get_cached_result(cache_key)
        if cached is not None:
            return cached

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, mode, heating_setpoint, cooling_setpoint,
                              humidity, co2, vpd, ramp_in_duration, updated_at
                       FROM setpoints 
                       WHERE location = $1 AND cluster = $2 AND mode = $3""",
                    location, cluster, db_mode
                )
                if row:
                    result = dict(row)
                    self._set_cached_result(cache_key, result)
                    return result
        except Exception as e:
            logger.error(f"Failed to get setpoint: {e}")
        return None

    async def set_setpoint(
        self,
        location: str,
        cluster: str,
        heating_setpoint: float | None = None,
        cooling_setpoint: float | None = None,
        humidity: float | None = None,
        co2: float | None = None,
        vpd: float | None = None,
        mode: str = "main",
        ramp_in_duration: int | None = None,
        source: str = "api",
        expected_version: datetime | None = None
    ) -> tuple[bool, datetime | None]:
        """Set setpoint with optimistic locking."""
        db_mode = mode if mode != "main" else "day"

        try:
            async with self.pool.acquire() as conn:
                if expected_version:
                    row = await conn.fetchrow(
                        "SELECT updated_at FROM setpoints WHERE location = $1 AND cluster = $2 AND mode = $3",
                        location, cluster, db_mode
                    )
                    if row:
                        current = row["updated_at"]
                        if current and current != expected_version:
                            return (False, None)

                heat = heating_setpoint if heating_setpoint is not None else 20.0
                cool = cooling_setpoint if cooling_setpoint is not None else 25.0
                hum = humidity if humidity is not None else 60.0
                co2_val = co2 if co2 is not None else 800.0
                vpd_val = vpd if vpd is not None else 1.0
                ramp_in = ramp_in_duration if ramp_in_duration is not None else 30

                new_row = await conn.fetchrow(
                    """INSERT INTO setpoints (location, cluster, mode, heating_setpoint, cooling_setpoint,
                                             humidity, co2, vpd, ramp_in_duration, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                       ON CONFLICT (location, cluster, mode)
                       DO UPDATE SET heating_setpoint = $4, cooling_setpoint = $5, humidity = $6,
                                    co2 = $7, vpd = $8, ramp_in_duration = $9, updated_at = NOW()
                       RETURNING updated_at""",
                    location, cluster, db_mode, heat, cool, hum, co2_val, vpd_val, ramp_in
                )
                self.clear_cache()
                return (True, new_row["updated_at"] if new_row else None)
        except Exception as e:
            logger.error(f"Failed to set setpoint: {e}")
            return (False, None)

    async def get_all_setpoints_for_location_cluster(self, location: str, cluster: str) -> list[dict[str, Any]]:
        """Get all setpoints for a location/cluster."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT location, cluster, mode, heating_setpoint, cooling_setpoint,
                              humidity, co2, vpd, ramp_in_duration, updated_at
                       FROM setpoints WHERE location = $1 AND cluster = $2""",
                    location, cluster
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get setpoints: {e}")
            return []

    async def get_latest_effective_setpoints(self, location: str, cluster: str) -> dict[str, Any] | None:
        """Get the latest effective setpoints."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM effective_setpoints
                       WHERE location = $1 AND cluster = $2
                       ORDER BY timestamp DESC LIMIT 1""",
                    location, cluster
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get effective setpoints: {e}")
        return None
