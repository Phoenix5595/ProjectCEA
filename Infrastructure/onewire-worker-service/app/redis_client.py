"""Redis client for publishing 1-Wire sensor state."""

from __future__ import annotations

from datetime import datetime

import redis.asyncio as redis

from shared.infra_logging import get_logger
from shared.redis_client import (
    close_async,
    create_async_client,
    redis_url_from_env,
)
from shared.redis_keys import sensor_full, sensor_full_ts, sensor_short, sensor_short_ts

logger = get_logger(__name__)


class RedisClient:
    """Publish sensor values to Redis state keys (sensor:*, sensor:*:ts)."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or redis_url_from_env()
        self.client: redis.Redis | None = None
        self._pool: redis.ConnectionPool | None = None
        self.redis_enabled = False
        self.redis_ttl = 10

    async def connect(self) -> bool:
        try:
            self.client, self._pool = await create_async_client(
                self.redis_url,
                decode_responses=True,
                max_connections=5,
                name="onewire-redis",
            )
            self.redis_enabled = True
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            self.redis_enabled = False
            return False

    async def close(self) -> None:
        await close_async(self.client, self._pool, name="onewire-redis")
        self.client = None
        self._pool = None
        self.redis_enabled = False

    async def set_sensor_value(self, sensor_name: str, value: float) -> bool:
        if not self.redis_enabled or not self.client:
            return False
        try:
            ts_ms = int(datetime.now().timestamp() * 1000)
            pipe = self.client.pipeline()
            # Dual-write during key migration:
            # - short form (sensor:{name}) is used by dashboard bulk reads
            # - full form (cea:sensor:{location}:{cluster}:{sensor_type}) is used by automation
            #
            # 1-wire sensors are logically scoped to Lab/main.
            location = "Lab"
            cluster = "main"
            pipe.setex(sensor_short(sensor_name), self.redis_ttl, str(value))
            pipe.setex(sensor_short_ts(sensor_name), self.redis_ttl, str(ts_ms))
            pipe.setex(sensor_full(location, cluster, sensor_name), self.redis_ttl, str(value))
            pipe.setex(sensor_full_ts(location, cluster, sensor_name), self.redis_ttl, str(ts_ms))
            await pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Error writing Redis {sensor_name}: {e}")
            return False
