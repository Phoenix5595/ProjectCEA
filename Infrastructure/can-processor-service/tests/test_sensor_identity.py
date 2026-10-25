from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
import unittest
from unittest.mock import patch

from app.sensor_registry import ABSENCE, SensorAssignment
from app.writer import DataWriter, DBWriteItem
from shared.sensor_identity import SENSOR_IDENTITY_LOCK_NAMESPACE


class FakeCursor:
    def __init__(
        self,
        registry_ids: list[tuple[int]],
        rows: list[tuple[int, int, int]],
    ) -> None:
        self.registry_ids = registry_ids
        self.rows = rows
        self.query = ""
        self.params: tuple[Any, ...] | None = None
        self.current_rows: list[tuple[int, ...]] = []
        self.lock_calls: list[tuple[int, int]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params
        if "pg_advisory_xact_lock_shared" in query:
            self.lock_calls.append(params)
            self.current_rows = []
        elif "SELECT registry_id, hardware_address, device_id" in query:
            self.current_rows = self.rows
        else:
            self.current_rows = self.registry_ids

    def fetchall(self) -> list[tuple[int, ...]]:
        return self.current_rows


class FakeDbCursor:
    def __init__(self, connection: FakeDbConnection) -> None:
        self.connection = connection
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        self.connection.statements.append((query, params, self.connection.autocommit))
        if query.strip() == "SELECT 1":
            self.rows = [(1,)]
        elif "SELECT registry_id, hardware_address, device_id" in query:
            self.rows = [(10, 1, 88)]
        elif "FROM sensor_registry" in query:
            self.rows = [(10,)]
        elif "FROM sensor WHERE device_id" in query:
            self.rows = [(50, "dry_bulb_b")]
        else:
            self.rows = []

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        return None


class FakeDbConnection:
    def __init__(self) -> None:
        self.autocommit = True
        self.statements: list[tuple[str, tuple[Any, ...] | None, bool]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> FakeDbCursor:
        return FakeDbCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class SensorIdentityWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = DataWriter(db_config={}, redis_url="redis://127.0.0.1:6379")

    def test_stale_address_cache_is_discarded_before_registry_lookup(self) -> None:
        self.writer.device_cache[1] = 77
        cursor = FakeCursor([], [])

        self.writer._prefetch_registry_device_ids(cursor, {1})

        self.assertNotIn(1, self.writer.device_cache)
        self.assertIn("FROM sensor_registry", cursor.query)
        self.assertEqual(cursor.params, ([1], ABSENCE))

    def test_current_registry_device_is_locked_and_used_for_the_can_address(self) -> None:
        cursor = FakeCursor([(10,)], [(10, 1, 88)])

        self.writer._prefetch_registry_device_ids(cursor, {1})

        self.assertEqual(self.writer.device_cache, {1: 88})
        self.assertEqual(
            cursor.lock_calls,
            [(SENSOR_IDENTITY_LOCK_NAMESPACE, 10)],
        )

    def test_batch_resolution_and_insert_share_a_registry_lock_transaction(self) -> None:
        connection = FakeDbConnection()
        self.writer.db_conn = connection
        self.writer.db_enabled = True
        timestamp = datetime.now(UTC)
        item = DBWriteItem(
            decoded={"node_id": 1},
            raw_data="",
            sensors=[("dry_bulb_b", 22.0, "°C")],
            timestamp=timestamp,
            assignment=SensorAssignment(10, 1, "Flower Room", "back"),
        )

        with patch("app.writer.psycopg2.extras.execute_batch") as execute_batch:
            execute_batch.side_effect = lambda _cursor, _sql, _rows, **_kwargs: self.assertFalse(
                connection.autocommit
            )
            self.writer._flush_batch([item])

        lock_calls = [
            (params, autocommit)
            for query, params, autocommit in connection.statements
            if "pg_advisory_xact_lock_shared" in query
        ]
        self.assertEqual(
            lock_calls,
            [((SENSOR_IDENTITY_LOCK_NAMESPACE, 10), False)],
        )
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(connection.autocommit)
        self.assertTrue(
            all(
                autocommit is False
                for query, _params, autocommit in connection.statements
                if query.strip() != "SELECT 1"
            )
        )
        self.assertEqual(
            execute_batch.call_args.args[2],
            [(timestamp, 50, 22.0, None)],
        )

    def test_identity_absence_threshold_is_ten_minutes(self) -> None:
        self.assertEqual(ABSENCE, timedelta(minutes=10))
