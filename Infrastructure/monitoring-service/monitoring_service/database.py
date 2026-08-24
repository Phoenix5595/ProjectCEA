"""Dedicated bounded PostgreSQL resource for monitoring reads."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, final

import asyncpg
from typing_extensions import override

from monitoring_service.resources import DatabaseResourceSettings


@dataclass(frozen=True, slots=True)
class ReadOnlyQueryError(Exception):
    """Reject a query that cannot be served by the monitoring read surface."""

    query: str

    @override
    def __str__(self) -> str:
        return "monitoring database resource accepts SELECT, WITH, or EXPLAIN queries only"


class ConnectionLike(Protocol):
    def transaction(
        self, *, isolation: str, readonly: bool
    ) -> AbstractAsyncContextManager[None]: ...

    async def fetch(
        self, query: str, *args: str | int | float | datetime
    ) -> list[asyncpg.Record]: ...


class AcquiredConnection(Protocol):
    async def __aenter__(self) -> ConnectionLike: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class PoolLike(Protocol):
    def acquire(self, *, timeout: float | None = None) -> AcquiredConnection: ...

    async def close(self) -> None: ...


@final
class ReadOnlyDatabase:
    """Execute chart reads in an isolated, bounded, read-only pool."""

    def __init__(self, pool: asyncpg.Pool | PoolLike, acquire_timeout_seconds: float = 10) -> None:
        self._pool = pool
        self._acquire_timeout_seconds = acquire_timeout_seconds

    @property
    def pool(self) -> asyncpg.Pool:
        """Expose the pool only to the non-mutating shared readiness probe."""
        if not isinstance(self._pool, asyncpg.Pool):
            raise RuntimeError("monitoring database test double cannot run readiness")
        return self._pool

    @classmethod
    async def connect(cls, settings: DatabaseResourceSettings) -> ReadOnlyDatabase:
        """Create the monitoring-only pool with server and client query deadlines."""
        pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=1,
            max_size=settings.pool_size,
            timeout=settings.acquire_timeout_seconds,
            command_timeout=settings.statement_timeout_ms / 1000,
            server_settings={
                "application_name": "monitoring-service",
                "default_transaction_read_only": "on",
                "statement_timeout": str(settings.statement_timeout_ms),
            },
        )
        return cls(pool, settings.acquire_timeout_seconds)

    async def fetch(self, query: str, *args: str | int | float | datetime) -> list[asyncpg.Record]:
        """Run a chart query inside a repeatable read-only transaction."""
        normalized_query = query.lstrip().upper()
        if not normalized_query.startswith(("SELECT", "EXPLAIN", "WITH")):
            raise ReadOnlyQueryError(query)
        async with self._pool.acquire(timeout=self._acquire_timeout_seconds) as connection:
            async with connection.transaction(isolation="repeatable_read", readonly=True):
                return await connection.fetch(query, *args)

    async def close(self) -> None:
        """Release the independently owned monitoring pool."""
        await self._pool.close()
