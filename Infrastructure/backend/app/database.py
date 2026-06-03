"""Database manager for TimescaleDB connection pool management.

Query logic has been extracted to ``app.repositories.sensor_repository``.
This module retains only pool lifecycle (create, close) so that the
ready-check in ``main.py`` and the ``SensorRepository`` can share a
single connection pool.
"""

from __future__ import annotations

import asyncpg

from shared.db import create_pool, db_config_from_env
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Manages the TimescaleDB connection pool."""

    def __init__(self, db_config: dict[str, str] | None = None):
        self.db_config = db_config if db_config is not None else db_config_from_env()
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        """Get or create connection pool.

        Raises:
            ConnectionError: If connection pool creation fails after the
                shared retry loop exhausts its attempts.
        """
        if self._pool is None:
            self._pool = await create_pool(self.db_config, application_name="cea_backend")
        return self._pool

    async def close(self):
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None
