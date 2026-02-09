"""Redis client for publishing 1-Wire sensor state."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
import os
from typing import Any, cast

import redis.asyncio as redis

from shared.logging import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Publish sensor values to Redis state keys (sensor:*, sensor:*:ts)."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.client: redis.Redis | None = None
        self._pool: redis.ConnectionPool | None = None
        self.redis_enabled = False
        self.redis_ttl = 10

    async def connect(self) -> bool:
        try:
            self._pool = redis.ConnectionPool.from_url(
                self.redis_url, decode_responses=True, max_connections=5
            )
            self.client = redis.Redis(connection_pool=self._pool)
            await cast(Awaitable[Any], self.client.ping())
            self.redis_enabled = True
            logger.info(f"Connected to Redis: {self.redis_url}")
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            self.redis_enabled = False
            return False

    async def close(self) -> None:
        if self.client:
            await self.client.close()
        if self._pool:
            await self._pool.disconnect()
        self.redis_enabled = False
        logger.info("Redis connection closed")

    async def set_sensor_value(self, sensor_name: str, value: float) -> bool:
        if not self.redis_enabled or not self.client:
            return False
        try:
            ts_ms = int(datetime.now().timestamp() * 1000)
            pipe = self.client.pipeline()
            pipe.setex(f"sensor:{sensor_name}", self.redis_ttl, str(value))
            pipe.setex(f"sensor:{sensor_name}:ts", self.redis_ttl, str(ts_ms))
            await pipe.execute()
            return True
        except Exception as e:
            logger.warning(f"Error writing Redis {sensor_name}: {e}")
            return False
