from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

import pytest

from app.repositories.setpoints import SetpointRepository


class _FakeConnection:
    def __init__(self) -> None:
        self.failures = 0
        self.failure: Exception = OSError("disposable fake database outage")
        self.batches: list[list[tuple[Any, ...]]] = []

    async def executemany(self, _query: str, records: list[tuple[Any, ...]]) -> None:
        if self.failures:
            self.failures -= 1
            raise self.failure
        self.batches.append(records)


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_failed_flush_keeps_setpoints_and_success_replays_oldest_first_with_original_times() -> (
    None
):
    connection = _FakeConnection()
    repository = SetpointRepository(pool=_FakePool(connection))
    repository._batch_interval = 60
    first_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    second_time = first_time + timedelta(seconds=5)

    assert await repository.log_effective_setpoints(
        location="Flower Room",
        cluster="main",
        mode="DAY",
        timestamp=first_time,
    )
    assert await repository.log_effective_setpoints(
        location="Flower Room",
        cluster="main",
        mode="NIGHT",
        timestamp=second_time,
    )
    assert len(repository._batch_buffer) == 2

    connection.failures = 1
    assert await repository.flush_batch_buffer() == 0
    assert len(repository._batch_buffer) == 2
    assert connection.batches == []

    assert await repository.flush_batch_buffer() == 2
    assert len(repository._batch_buffer) == 0
    assert [record[0] for record in connection.batches[0]] == [first_time, second_time]


@pytest.mark.asyncio
async def test_permanent_error_drops_failed_setpoint_batch_and_flushes_later_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = _FakeConnection()
    repository = SetpointRepository(pool=_FakePool(connection))
    repository._batch_interval = 60
    first_time = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)

    for index in range(2_001):
        await repository.log_effective_setpoints(
            location="Flower Room",
            cluster="main",
            mode="DAY",
            timestamp=first_time + timedelta(seconds=index),
        )

    connection.failures = 1
    connection.failure = ValueError("disposable invalid row")
    with caplog.at_level(logging.ERROR):
        assert await repository.flush_batch_buffer() == 1

    assert len(repository._batch_buffer) == 0
    assert len(connection.batches) == 1
    assert len(connection.batches[0]) == 1
    assert connection.batches[0][0][0] == first_time + timedelta(seconds=2_000)
    assert any(
        "Dropped 2000 effective setpoint rows" in record.message for record in caplog.records
    )
