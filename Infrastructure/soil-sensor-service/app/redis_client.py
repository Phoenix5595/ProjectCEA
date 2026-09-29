"""Redis client for publishing sensor updates."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

import redis.asyncio as redis

from shared.infra_logging import get_logger
from shared.redis_client import (
    close_async,
    create_async_client,
    redis_url_from_env,
)
from shared.redis_keys import (
    SENSOR_RAW_MAXLEN,
    SENSOR_RAW_STREAM,
    SENSOR_UPDATE_CHANNEL,
    SENSOR_UPDATE_SOIL_CHANNEL,
    SOIL_SENSOR_CURRENT_TTL_SEC,
    sensor_full,
    sensor_full_ts,
)

logger = get_logger(__name__)


class RedisClient:
    """Redis client for publishing sensor data updates.

    Uses two connection pools:
      * state pool (``decode_responses=True``) for ``sensor:*`` keys + pub/sub
      * stream pool (``decode_responses=False``) for ``XADD sensor:raw`` binary writes
    """

    def __init__(self, redis_url: str | None = None):
        """Initialize Redis client.

        Args:
            redis_url: Redis connection URL. If None, uses environment variable or default.
        """
        self.redis_url = redis_url or redis_url_from_env()
        self.redis_client: redis.Redis | None = None
        self.stream_client: redis.Redis | None = None
        self._state_pool: redis.ConnectionPool | None = None
        self._stream_pool: redis.ConnectionPool | None = None
        self.redis_enabled = False

    async def connect(self) -> bool:
        """Connect both Redis pools (state + binary stream)."""
        try:
            self.redis_client, self._state_pool = await create_async_client(
                self.redis_url,
                decode_responses=True,
                max_connections=10,
                name="soil-redis-state",
            )
            self.stream_client, self._stream_pool = await create_async_client(
                self.redis_url,
                decode_responses=False,
                max_connections=5,
                name="soil-redis-stream",
            )
            self.redis_enabled = True
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will continue without Redis.")
            self.redis_enabled = False
            return False

    async def close(self) -> None:
        """Close both pools (best-effort, SIGTERM-safe)."""
        await close_async(self.redis_client, self._state_pool, name="soil-redis-state")
        await close_async(self.stream_client, self._stream_pool, name="soil-redis-stream")
        self.redis_client = None
        self.stream_client = None
        self._state_pool = None
        self._stream_pool = None
        self.redis_enabled = False


    async def write_to_stream(
        self,
        sensor_base_name: str,
        readings: dict[str, float],
        bed_name: str | None,
        location: str = "Flower Room",
    ) -> bool:
        """Write sensor readings to Redis Stream (sensor:raw).

        Args:
            sensor_base_name: Base sensor name (e.g., "soil_sensor_front_bed")
            readings: Dict with temperature, humidity, ec, ph values
            bed_name: Assigned bed name, or None while unassigned
                (the raw stream is always written; bed metadata stays empty)

            location: Location/room name

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled or not self.stream_client:
            return False

        try:
            timestamp_ms = int(datetime.now().timestamp() * 1000)

            # Create stream entry with type="soil" marker
            stream_data: dict[Any, Any] = {
                b"id": f"{sensor_base_name}_{timestamp_ms}".encode(),
                b"ts": str(timestamp_ms).encode(),
                b"type": b"soil",  # Mark as soil sensor data
                b"sensor_name": sensor_base_name.encode(),
                b"bed_name": (bed_name or "").encode(),
                b"location": (location or "").encode(),
                b"readings": json.dumps(readings).encode(),
            }

            await self.stream_client.xadd(
                SENSOR_RAW_STREAM, stream_data, maxlen=SENSOR_RAW_MAXLEN, approximate=True
            )
            return True
        except Exception as e:
            logger.warning(f"Error writing to Redis Stream: {e}")
            return False

    async def publish_all_readings(
        self,
        sensor_base_name: str,
        readings: dict[str, float],
        bed_name: str | None,
        location: str = "Flower Room",
    ) -> bool:
        """
        Publish all sensor readings for a soil sensor in one pipeline.

        Args:
            sensor_base_name: Base sensor name (e.g., "soil_sensor_front_bed")
            readings: Dict with temperature, humidity, ec, ph values
            bed_name: Assigned bed name, or None while unassigned
                (the raw stream is always written; bed metadata stays empty)

            location: Location/room name

        Returns:
            True if all published successfully, False otherwise
        """
        units = {"temperature": "°C", "humidity": "%", "ec": "µS/cm", "ph": "pH"}
        if not any(sensor_type in units for sensor_type in readings):
            return True
        if not self.redis_enabled or self.redis_client is None:
            return False

        try:
            pipeline = self.redis_client.pipeline(transaction=True)
            for sensor_type, value in readings.items():
                unit = units.get(sensor_type)
                if unit is None:
                    continue

                sensor_name = f"{sensor_base_name}_{sensor_type}"
                timestamp = datetime.now()
                timestamp_ms = int(timestamp.timestamp() * 1000)
                message = {
                    "sensor_name": sensor_name,
                    "value": value,
                    "unit": unit,
                    "timestamp": timestamp.isoformat(),
                    "location": location,
                    "bed": bed_name,
                }
                pipeline.publish(SENSOR_UPDATE_CHANNEL, json.dumps(message))
                pipeline.publish(SENSOR_UPDATE_SOIL_CHANNEL, json.dumps(message))

                cluster = "main"
                pipeline.setex(
                    sensor_full(location, cluster, sensor_name),
                    SOIL_SENSOR_CURRENT_TTL_SEC,
                    str(value),
                )
                pipeline.setex(
                    sensor_full_ts(location, cluster, sensor_name),
                    SOIL_SENSOR_CURRENT_TTL_SEC,
                    str(timestamp_ms),
                )

            await pipeline.execute()
            return True
        except Exception as exc:
            logger.warning("Error publishing soil sample to Redis: %s", exc)
            return False
