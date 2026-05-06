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
    sensor_full,
    sensor_full_ts,
    sensor_short,
    sensor_short_ts,
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
        self.redis_ttl = 10  # consistent with CAN + onewire TTL

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

    async def publish_sensor_update(
        self,
        sensor_name: str,
        value: float,
        unit: str,
        bed_name: str,
        location: str = "Flower Room",
    ) -> bool:
        """
        Publish sensor update to Redis channels and store in state.

        Args:
            sensor_name: Full sensor name (e.g., "soil_sensor_front_bed_temperature")
            value: Sensor value
            unit: Unit of measurement
            bed_name: Bed name (e.g., "Front Bed")
            location: Location/room name

        Returns:
            True if successful, False otherwise
        """
        if not self.redis_enabled:
            return False

        try:
            timestamp = datetime.now()
            timestamp_ms = int(timestamp.timestamp() * 1000)

            # Create message
            message = {
                "sensor_name": sensor_name,
                "value": value,
                "unit": unit,
                "timestamp": timestamp.isoformat(),
                "location": location,
                "bed": bed_name,
            }

            # Publish to channels
            assert self.redis_client is not None, (
                "redis_client must be connected when redis_enabled is True"
            )
            await self.redis_client.publish("sensor:update", json.dumps(message))
            await self.redis_client.publish("sensor:update:soil", json.dumps(message))

            # Store in state with TTL
            # Dual-write during key migration:
            # - short form (sensor:{name}) is used by some dashboard readers
            # - full form (cea:sensor:{location}:{cluster}:{sensor_type}) is used by automation
            #
            # Soil sensors are currently scoped to main.
            cluster = "main"
            state_key = sensor_short(sensor_name)
            ts_key = sensor_short_ts(sensor_name)
            full_key = sensor_full(location, cluster, sensor_name)
            full_ts_key = sensor_full_ts(location, cluster, sensor_name)

            pipe = self.redis_client.pipeline()
            pipe.setex(state_key, self.redis_ttl, str(value))
            pipe.setex(ts_key, self.redis_ttl, str(timestamp_ms))
            pipe.setex(full_key, self.redis_ttl, str(value))
            pipe.setex(full_ts_key, self.redis_ttl, str(timestamp_ms))
            await pipe.execute()

            return True
        except Exception as e:
            logger.warning(f"Error publishing to Redis: {e}")
            return False

    async def write_to_stream(
        self,
        sensor_base_name: str,
        readings: dict[str, float],
        bed_name: str,
        location: str = "Flower Room",
    ) -> bool:
        """Write sensor readings to Redis Stream (sensor:raw).

        Args:
            sensor_base_name: Base sensor name (e.g., "soil_sensor_front_bed")
            readings: Dict with temperature, humidity, ec, ph values
            bed_name: Bed name
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
                b"bed_name": bed_name.encode(),
                b"location": location.encode(),
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
        bed_name: str,
        location: str = "Flower Room",
    ) -> bool:
        """
        Publish all sensor readings for a soil sensor.

        Args:
            sensor_base_name: Base sensor name (e.g., "soil_sensor_front_bed")
            readings: Dict with temperature, humidity, ec, ph values
            bed_name: Bed name
            location: Location/room name

        Returns:
            True if all published successfully, False otherwise
        """
        units = {"temperature": "°C", "humidity": "%", "ec": "µS/cm", "ph": "pH"}

        success = True
        for sensor_type, value in readings.items():
            if sensor_type in units:
                sensor_name = f"{sensor_base_name}_{sensor_type}"
                result = await self.publish_sensor_update(
                    sensor_name, value, units[sensor_type], bed_name, location
                )
                if not result:
                    success = False

        return success
