"""Database manager for TimescaleDB connection pool management.

Query logic has been extracted to ``app.repositories.sensor_repository``.
This module retains only pool lifecycle (create, close) so that the
ready-check in ``main.py`` and the ``SensorRepository`` can share a
single connection pool.
"""

from __future__ import annotations

import asyncio

import asyncpg

from shared.db import create_pool, db_config_from_env
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages the TimescaleDB connection pool.

    Instances belong to one service event loop: lazy creation and close
    are serialized by a per-instance lock created on first use, and only
    successful factory results are published, so a failed or cancelled
    initialization leaves ``_pool`` as ``None`` and preserves the factory
    retry/settings for the next attempt.
    """

    def __init__(self, db_config: dict[str, str] | None = None):
        self.db_config = db_config if db_config is not None else db_config_from_env()
        self._pool: asyncpg.Pool | None = None
        self._pool_lock: asyncio.Lock | None = None

    def _get_pool_lock(self) -> asyncio.Lock:
        """Lazily create the instance lock (created inside the running loop)."""
        if self._pool_lock is None:
            self._pool_lock = asyncio.Lock()
        return self._pool_lock

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool.

        Concurrent first callers serialize on the instance lock and share
        one pool identity (double-check under lock).

        Raises:
            ConnectionError: If connection pool creation fails after the
                shared retry loop exhausts its attempts. A failed or
                cancelled creation publishes nothing.
        """
        async with self._get_pool_lock():
            if self._pool is None:
                self._pool = await create_pool(self.db_config, application_name="cea_backend")
            return self._pool

    async def close(self) -> None:
        """Close connection pool.

        Serialized with creation on the same lock. ``_pool`` is detached
        before awaiting teardown and never restored on error, so a failed
        close propagates to the caller (lifespan) without leaving a
        closed pool published. Closing an unused or already-closed
        manager creates nothing.
        """
        async with self._get_pool_lock():
            pool = self._pool
            if pool is None:
                return
            self._pool = None
            await pool.close()
