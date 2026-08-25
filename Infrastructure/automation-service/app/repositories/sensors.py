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
                matching_keys = list(
                    self._redis_client._redis.scan_iter(
                        match=f"cea:sensor:*:*:{sensor_name}", count=100
                    )
                )
                value = (
                    None if not matching_keys else self._redis_client._redis.get(matching_keys[0])
                )
                if value is not None:
                    return float(value)
            except Exception as e:
                logger.warning(f"Redis read failed for {sensor_name}: {e}")

        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT m.value FROM measurement m
                       WHERE m.sensor_id = (SELECT sensor_id FROM sensor WHERE name = $1)
                       ORDER BY m.time DESC LIMIT 1""",
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
                values_by_name: dict[str, float] = {}
                for key in self._redis_client._redis.scan_iter(match="cea:sensor:*:*:*", count=500):
                    if key.endswith(("_ts", "_last_good")):
                        continue
                    value = self._redis_client._redis.get(key)
                    if value is not None:
                        sensor_name = key.rsplit(":", 1)[-1]
                        if sensor_name in result:
                            values_by_name[sensor_name] = float(value)
                result.update(values_by_name)
            except Exception as e:
                logger.warning(f"Redis batch read failed: {e}")

        missing_sensors = [name for name, val in result.items() if val is None]
        if missing_sensors:
            try:
                async with self.pool.acquire() as conn:
                    rows = await conn.fetch(
                        """SELECT DISTINCT ON (m.sensor_id) m.sensor_id, m.value
                           FROM measurement m
                           WHERE m.sensor_id IN (SELECT sensor_id FROM sensor WHERE name = ANY($1))
                           ORDER BY m.sensor_id, m.time DESC""",
                        missing_sensors,
                    )
                    # Map back to sensor names
                    sensor_names_query = await conn.fetch(
                        "SELECT sensor_id, name FROM sensor WHERE name = ANY($1)",
                        missing_sensors,
                    )
                    name_by_id = {row["sensor_id"]: row["name"] for row in sensor_names_query}
                    for row in rows:
                        if row["sensor_id"] in name_by_id:
                            result[name_by_id[row["sensor_id"]]] = float(row["value"])
            except Exception as e:
                logger.error(f"Database batch read failed: {e}")

        return result
