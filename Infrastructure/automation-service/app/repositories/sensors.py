from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BaseRepository

if TYPE_CHECKING:
    from asyncpg import Pool


_SENSOR_VALUES_BATCH_SQL = """
WITH requested_names AS (
    SELECT DISTINCT name
    FROM unnest($1::text[]) AS requested(name)
)
SELECT requested_names.name AS sensor_name,
       CASE
           WHEN COUNT(s.sensor_id) = 1 THEN MAX(latest.value)
           ELSE NULL
       END AS value
FROM requested_names
LEFT JOIN sensor s ON s.name = requested_names.name
LEFT JOIN LATERAL (
    SELECT m.value
    FROM measurement m
    WHERE m.sensor_id = s.sensor_id
      AND m.time > now() - make_interval(secs => $2)
    ORDER BY m.time DESC
    LIMIT 1
) latest ON s.sensor_id IS NOT NULL
GROUP BY requested_names.name
"""


class SensorRepository(BaseRepository):
    """Repository for sensor data operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_sensor_values_batch(
        self, sensor_names: list[str], *, max_age_sec: float
    ) -> dict[str, float | None]:
        """Read each unique sensor name's newest database measurement within its age limit."""
        names = list(dict.fromkeys(sensor_names))
        if not names:
            return {}

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_SENSOR_VALUES_BATCH_SQL, names, float(max_age_sec))

        values: dict[str, float | None] = dict.fromkeys(names)
        for row in rows:
            sensor_name = row["sensor_name"]
            value = row["value"]
            if sensor_name in values:
                values[sensor_name] = None if value is None else float(value)
        return values
