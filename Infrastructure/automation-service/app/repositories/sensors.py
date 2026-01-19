from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class SensorRepository(BaseRepository):
    """Repository for sensor data operations."""

    def __init__(self, pool: Pool | None = None, redis_client: Any = None) -> None:
        super().__init__(pool)
        self._redis_client = redis_client

    def set_redis_client(self, redis_client: Any) -> None:
        self._redis_client = redis_client

    async def get_sensor_value(self, sensor_name: str) -> float | None:
        """Get current sensor value, trying Redis first then database."""
        if self._redis_client and self._redis_client._connected:
            try:
                value = self._redis_client._redis.get(f"sensor:{sensor_name}")
                if value is not None:
                    return float(value)
            except Exception as e:
                logger.warning(f"Redis read failed for {sensor_name}: {e}")

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT value FROM measurement 
                       WHERE sensor_name = $1 
                       ORDER BY time DESC LIMIT 1""",
                    sensor_name,
                )
                if row:
                    return float(row["value"])
        except Exception as e:
            logger.error(f"Database read failed for {sensor_name}: {e}")
        return None

    async def get_sensor_values_batch(self, sensor_names: list[str]) -> dict[str, float | None]:
        """Get multiple sensor values in batch."""
        result: dict[str, float | None] = dict.fromkeys(sensor_names)

        if self._redis_client and self._redis_client._connected:
            try:
                keys = [f"sensor:{name}" for name in sensor_names]
                values = self._redis_client._redis.mget(keys)
                for sensor_name, value in zip(sensor_names, values, strict=False):
                    if value is not None:
                        result[sensor_name] = float(value)
            except Exception as e:
                logger.warning(f"Redis batch read failed: {e}")

        missing_sensors = [name for name, val in result.items() if val is None]
        if missing_sensors:
            try:
                async with self.pool.acquire() as conn:
                    rows = await conn.fetch(
                        """SELECT DISTINCT ON (sensor_name) sensor_name, value 
                           FROM measurement 
                           WHERE sensor_name = ANY($1)
                           ORDER BY sensor_name, time DESC""",
                        missing_sensors,
                    )
                    for row in rows:
                        result[row["sensor_name"]] = float(row["value"])
            except Exception as e:
                logger.error(f"Database batch read failed: {e}")

        return result
