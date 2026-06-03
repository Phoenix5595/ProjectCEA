"""Base repository with asyncpg pool injection.

Mirrors the canonical pattern from
``Infrastructure/automation-service/app/repositories/base.py`` but
trimmed for the backend's read-only workload (no query cache needed —
the backend serves historical data and the pool already handles
connection reuse).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class BaseRepository:
    """Base class for all backend repositories."""

    def __init__(self, pool: Pool | None = None) -> None:
        self._pool = pool

    def set_pool(self, pool: Pool) -> None:
        """Inject or replace the connection pool (e.g. after reconnect)."""
        self._pool = pool

    @property
    def pool(self) -> Pool:
        if self._pool is None:
            raise RuntimeError("Database pool not initialized")
        return self._pool

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[Any]:
        """Context-managed connection from the pool.

        Guarantees the connection is returned to the pool on exit,
        even if the caller raises.
        """
        async with self.pool.acquire() as conn:
            yield conn

    async def _execute(self, sql: str, *args: Any) -> list[Any]:
        async with self._acquire() as conn:
            return await conn.fetch(sql, *args)
