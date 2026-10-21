from datetime import UTC, datetime
from typing import Any

import pytest

from app.services.photoperiod_history_logger import DatabasePhotoperiodHistoryStore


class _AsyncContext:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_: object) -> None:
        return None


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def transaction(self, **_: Any) -> _AsyncContext:
        return _AsyncContext(self)

    async def fetch(self, query: str, *_: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        return self.rows


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self.connection)


class _StateConnection(_Connection):
    def __init__(
        self, phase_rows: list[dict[str, Any]], coverage_rows: list[dict[str, Any]]
    ) -> None:
        super().__init__([])
        self.responses = [phase_rows, coverage_rows]

    async def fetch(self, query: str, *_: Any) -> list[dict[str, Any]]:
        self.queries.append(query)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_photoperiod_store_reads_renamed_time_columns() -> None:
    phase_at = datetime(2026, 10, 19, tzinfo=UTC)
    coverage_at = datetime(2026, 10, 19, 0, 1, tzinfo=UTC)
    connection = _StateConnection(
        [
            {
                "time": phase_at,
                "location": "Flower Room",
                "cluster": "main",
                "phase": "SUN",
                "runtime_snapshot_version": 1,
            }
        ],
        [
            {
                "time": coverage_at,
                "location": "Flower Room",
                "cluster": "main",
                "state": "available",
                "runtime_snapshot_version": 2,
            }
        ],
    )
    store = DatabasePhotoperiodHistoryStore(_Pool(connection))

    states = await store.read_state()

    assert states[0].phase_observed_at == phase_at
    assert states[0].coverage_observed_at == coverage_at
    assert all('"time"' in query for query in connection.queries)
    assert all("observed_at" not in query for query in connection.queries)
