"""Base Redis connection mixin for automation service."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import redis

from shared.infra_logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class RedisConnectionMixin:
    """Mixin providing Redis connection management.

    Provides connection pooling for both state keys and stream writes.
    """

    redis_url: str
    redis_ttl: int
    redis_client: redis.Redis | None
    stream_client: redis.Redis | None
    _state_pool: redis.ConnectionPool | None
    _stream_pool: redis.ConnectionPool | None
    redis_enabled: bool

    def _init_connection(self, redis_url: str | None = None, redis_ttl: int = 10) -> None:
        """Initialize connection attributes.

        Args:
            redis_url: Redis connection URL. If None, uses environment variable or default.
            redis_ttl: TTL for Redis state keys in seconds (default: 10)
        """
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis_ttl = redis_ttl
        self.redis_client = None
        self.stream_client = None
        self._state_pool = None
        self._stream_pool = None
        self.redis_enabled = False

    def connect(self) -> bool:
        """Connect to Redis with connection pooling for better performance.

        Creates two connection pools:
        - State pool (decode_responses=True) for state key operations
        - Stream pool (decode_responses=False) for binary stream writes

        Connection pooling improves performance by reusing connections
        instead of creating new ones for each operation.

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create connection pool for state keys (decode_responses=True)
            self._state_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=True,
                max_connections=20,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            self.redis_client = redis.Redis(connection_pool=self._state_pool)
            self.redis_client.ping()

            # Create connection pool for stream writes (decode_responses=False for binary)
            self._stream_pool = redis.ConnectionPool.from_url(
                self.redis_url,
                decode_responses=False,
                max_connections=10,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            self.stream_client = redis.Redis(connection_pool=self._stream_pool)
            self.stream_client.ping()

            self.redis_enabled = True
            logger.info(
                f"Connected to Redis: {self.redis_url} (with connection pooling: state=20, stream=10)"
            )
            return True
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Will continue without Redis.")
            self.redis_enabled = False
            return False

    def close(self) -> None:
        """Close Redis connections and disconnect connection pools.

        All four cleanup steps are best-effort: if a client/pool is already
        closed (typical during a SIGTERM race) the underlying redis-py call
        raises ``ConnectionError`` / ``RuntimeError``; we log at debug
        rather than propagate because the caller is already on the
        shutdown path and there's nothing to recover.
        """
        if self.redis_client:
            try:
                self.redis_client.close()
            except Exception as e:
                logger.debug("redis_client.close() failed during shutdown: %s", e)
        if self.stream_client:
            try:
                self.stream_client.close()
            except Exception as e:
                logger.debug("stream_client.close() failed during shutdown: %s", e)
        if self._state_pool:
            try:
                self._state_pool.disconnect()
            except Exception as e:
                logger.debug("state_pool.disconnect() failed during shutdown: %s", e)
        if self._stream_pool:
            try:
                self._stream_pool.disconnect()
            except Exception as e:
                logger.debug("stream_pool.disconnect() failed during shutdown: %s", e)
        self.redis_enabled = False
        logger.info("Redis connection closed")
