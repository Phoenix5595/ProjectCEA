from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from types import TracebackType

import asyncpg
import pytest

from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.query_observation import request_observation
from monitoring_service.sensor_models import MonitoringUnavailableError


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: object) -> None:
        return None


class FakeConnection:
    def transaction(self, *, isolation: str, readonly: bool) -> AbstractAsyncContextManager[None]:
        del isolation, readonly
        return FakeTransaction()

    async def fetch(self, query: str, *_: str | int | float | datetime) -> list[asyncpg.Record]:
        del query
        return []


class FakeAcquireContext:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> FakeConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FakePool:
    def __init__(
        self, connection: FakeConnection, dsn: str = "postgresql://safe@localhost/cea"
    ) -> None:
        self.connection = connection
        self.dsn = dsn

    def acquire(self, *, timeout: float | None = None) -> FakeAcquireContext:
        del timeout
        return FakeAcquireContext(self.connection)

    async def close(self) -> None:
        return None


class TimedOutAcquireContext:
    async def __aenter__(self) -> FakeConnection:
        raise TimeoutError("pool acquisition timed out")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class TimedOutPool:
    def acquire(self, *, timeout: float | None = None) -> TimedOutAcquireContext:
        del timeout
        return TimedOutAcquireContext()

    async def close(self) -> None:
        return None


@pytest.mark.anyio
async def test_query_observation_emits_safe_acquire_query_and_summary_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: an observed read request using a fake database pool
    caplog.set_level(logging.DEBUG, logger="monitoring_service.query_observation")
    database = ReadOnlyDatabase(FakePool(FakeConnection()))

    # When: the request executes one SELECT query
    async with request_observation(tier="raw"):
        rows = await database.fetch("SELECT 1")

    # Then: acquisition, query, and request events have only the safe event schema
    assert rows == []
    events = [record.extra for record in caplog.records]
    assert [event["event"] for event in events] == ["db_acquire", "db_query", "request_summary"]
    assert all(
        set(event)
        == {
            "event",
            "duration_ms",
            "outcome",
            "statement_kind",
            "row_count",
            "tier",
            "query_count",
            "total_duration_ms",
        }
        for event in events
    )
    assert events[0] | {"outcome": "ok"} == events[0]
    assert events[1]["statement_kind"] == "SELECT"
    assert events[1]["row_count"] == 0
    assert events[1]["tier"] == "raw"
    assert events[2]["query_count"] == 1


@pytest.mark.anyio
async def test_query_observation_maps_acquire_timeout_to_monitoring_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a pool whose acquisition times out with the established exception
    caplog.set_level(logging.DEBUG, logger="monitoring_service.query_observation")
    database = ReadOnlyDatabase(TimedOutPool())

    # When: a read is attempted
    with pytest.raises(MonitoringUnavailableError, match="database read timed out"):
        _ = await database.fetch("SELECT 1")

    # Then: the original exception propagates and the timeout is safely observed
    events = [record.extra for record in caplog.records]
    assert events == [
        {
            "event": "db_acquire",
            "duration_ms": events[0]["duration_ms"],
            "outcome": "timeout",
            "statement_kind": None,
            "row_count": None,
            "tier": None,
            "query_count": None,
            "total_duration_ms": None,
        }
    ]


@pytest.mark.anyio
async def test_request_observation_sums_two_query_durations(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a request-scoped collector and a fake read database
    caplog.set_level(logging.DEBUG, logger="monitoring_service.query_observation")
    database = ReadOnlyDatabase(FakePool(FakeConnection()))

    # When: the request makes two queries
    async with request_observation(tier="1min"):
        _ = await database.fetch("SELECT 1")
        _ = await database.fetch("WITH values AS (SELECT 1) SELECT * FROM values")

    # Then: the request summary reports both query events and their summed duration
    events = [record.extra for record in caplog.records]
    query_events = [event for event in events if event["event"] == "db_query"]
    summary = next(event for event in events if event["event"] == "request_summary")
    assert summary["query_count"] == 2
    assert summary["total_duration_ms"] == round(
        sum(event["duration_ms"] for event in query_events), 1
    )


@pytest.mark.anyio
async def test_query_observation_never_logs_secret_or_sql_literal_sentinels(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Given: a pool and read input containing values that must never become log labels
    caplog.set_level(logging.DEBUG, logger="monitoring_service.query_observation")
    sentinels = (
        "postgresql://monitor:DSN_PASSWORD_SENTINEL@db/cea",
        "API_KEY_SENTINEL",
        "SQL_LITERAL_SENTINEL",
    )
    database = ReadOnlyDatabase(FakePool(FakeConnection(), dsn=sentinels[0]))

    # When: an observed query uses those values in the fake configuration and SQL input
    async with request_observation(tier="raw"):
        _ = await database.fetch(f"SELECT '{sentinels[2]}'", sentinels[1])

    # Then: captured structured records contain none of the sensitive values
    captured = "\n".join(f"{record.getMessage()} {record.extra!r}" for record in caplog.records)
    assert all(sentinel not in captured for sentinel in sentinels)
