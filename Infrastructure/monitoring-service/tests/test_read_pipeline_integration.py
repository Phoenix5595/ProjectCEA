from __future__ import annotations

import asyncio  # noqa: ANYIO_OK
from collections import Counter, defaultdict
from contextlib import AbstractAsyncContextManager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from time import perf_counter
from types import TracebackType
from typing import Protocol
from unittest.mock import MagicMock

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.main import create_app
from monitoring_service.sensor_repository import SensorMonitoringRepository
from monitoring_service.sensor_routes import get_sensor_reads

_REQUEST_ID: ContextVar[int] = ContextVar("read_pipeline_request_id", default=-1)
_FORBIDDEN_LEADING_SQL_VERBS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "TRUNCATE",
    "ALTER",
    "CREATE",
)


class ReadConnection(Protocol):
    def transaction(
        self, *, isolation: str, readonly: bool
    ) -> AbstractAsyncContextManager[None]: ...

    async def fetch(self, query: str, *args: object) -> list[asyncpg.Record]: ...


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class LatentConnection:
    def __init__(self, queries_by_request: dict[int, list[str]]) -> None:
        self._queries_by_request = queries_by_request

    def transaction(self, *, isolation: str, readonly: bool) -> AbstractAsyncContextManager[None]:
        assert (isolation, readonly) == ("repeatable_read", True)
        return FakeTransaction()

    async def fetch(self, query: str, *_: object) -> list[asyncpg.Record]:
        self._queries_by_request[_REQUEST_ID.get()].append(query)
        await asyncio.sleep(0.05)
        if "materialization_watermark" in query:
            row = MagicMock(spec=asyncpg.Record)
            row.__getitem__.side_effect = {
                "materialization_watermark": datetime(2026, 8, 24, 14, tzinfo=UTC)
            }.__getitem__
            return [row]
        return []


class LatentAcquireContext:
    def __init__(self, pool: LatentPool) -> None:
        self._pool = pool

    async def __aenter__(self) -> ReadConnection:
        await asyncio.sleep(0.02)
        await self._pool._capacity.acquire()
        self._pool.active_count += 1
        self._pool.maximum_active_count = max(
            self._pool.maximum_active_count, self._pool.active_count
        )
        return LatentConnection(self._pool.queries_by_request)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        self._pool.active_count -= 1
        self._pool._capacity.release()


class LatentPool:
    def __init__(self, max_size: int) -> None:
        self._capacity = asyncio.BoundedSemaphore(max_size)
        self.active_count = 0
        self.maximum_active_count = 0
        self.queries_by_request: dict[int, list[str]] = defaultdict(list)

    def acquire(self, *, timeout: float | None = None) -> LatentAcquireContext:
        del timeout
        return LatentAcquireContext(self)

    async def close(self) -> None:
        return None


class CancelledConnection:
    def transaction(self, *, isolation: str, readonly: bool) -> AbstractAsyncContextManager[None]:
        assert (isolation, readonly) == ("repeatable_read", True)
        return FakeTransaction()

    async def fetch(self, query: str, *_: object) -> list[asyncpg.Record]:
        del query
        raise asyncpg.QueryCanceledError("statement timeout")


class CancelledAcquireContext:
    async def __aenter__(self) -> ReadConnection:
        return CancelledConnection()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        return None


class CancelledPool:
    def acquire(self, *, timeout: float | None = None) -> CancelledAcquireContext:
        del timeout
        return CancelledAcquireContext()

    async def close(self) -> None:
        return None


class UnusedRedis:
    async def sensor_values(self, pattern: str) -> tuple[tuple[str, str | None, str | None], ...]:
        del pattern
        return ()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _sensor_app(database: ReadOnlyDatabase) -> FastAPI:
    app = create_app()
    repository = SensorMonitoringRepository(database, UnusedRedis())
    app.dependency_overrides[get_sensor_reads] = lambda: repository
    return app


@pytest.mark.anyio
async def test_eight_concurrent_bounded_flower_range_reads_release_the_shared_pool() -> None:
    # Given: eight concurrent Flower requests sharing a pool capped at eight connections.
    pool = LatentPool(max_size=8)
    app = _sensor_app(ReadOnlyDatabase(pool))
    params = {
        "start": "2026-08-17T14:00:00Z",
        "end": "2026-08-24T14:00:00Z",
        "max_points": "1000",
    }

    async def request(index: int) -> httpx.Response:
        token = _REQUEST_ID.set(index)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                return await client.get(
                    "/api/sensors/monitoring/range/Flower%20Room", params=params
                )
        finally:
            _REQUEST_ID.reset(token)

    # When: the eight viewers request the budgeted seven-day range together.
    started_at = perf_counter()
    responses = await asyncio.gather(*(request(index) for index in range(8)))
    elapsed_seconds = perf_counter() - started_at

    # Then: bounded sequential reads complete without leaked acquisition or starvation.
    assert [response.status_code for response in responses] == [200] * 8
    assert pool.active_count == 0
    assert pool.maximum_active_count <= 8
    assert elapsed_seconds < 1.5
    assert {
        request_id: len(queries) for request_id, queries in pool.queries_by_request.items()
    } == {request_id: 3 for request_id in range(8)}
    assert all(
        sum("materialization_watermark" in query for query in queries) == 1
        for queries in pool.queries_by_request.values()
    )
    assert all(max(Counter(queries).values()) == 1 for queries in pool.queries_by_request.values())


@pytest.mark.anyio
async def test_statement_cancellation_maps_to_monitoring_unavailable_instead_of_500() -> None:
    # Given: a real read-only database wrapper whose driver cancels a statement.
    app = _sensor_app(ReadOnlyDatabase(CancelledPool()))

    # When: the sensor range route executes through the fake driver stack.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/sensors/monitoring/range/Flower%20Room")

    # Then: the service returns its typed unavailable response, never an unhandled 500.
    assert response.status_code == 503


def test_monitoring_sql_constants_never_begin_with_a_write_verb() -> None:
    # Given: every module that owns or re-exports monitoring SQL constants.
    from monitoring_service import (
        control_history_queries,
        control_repository,
        sensor_repository,
        sensor_sql,
    )

    modules = (sensor_sql, control_history_queries, sensor_repository, control_repository)

    # When: each SQL-named constant and statement mapping is collected.
    statements = (
        statement
        for module in modules
        for name, value in vars(module).items()
        if "SQL" in name or "STATEMENTS" in name
        for statement in (value.values() if isinstance(value, dict) else (value,))
        if isinstance(statement, str)
    )

    # Then: no monitoring SQL constant can begin a mutating schema or data command.
    assert all(
        not statement.lstrip().upper().startswith(_FORBIDDEN_LEADING_SQL_VERBS)
        for statement in statements
    )
