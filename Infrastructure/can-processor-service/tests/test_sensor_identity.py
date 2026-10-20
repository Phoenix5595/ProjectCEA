from __future__ import annotations

from datetime import timedelta
from typing import Any
import unittest

from app.sensor_registry import ABSENCE
from app.writer import DataWriter


class FakeCursor:
    def __init__(self, rows: list[tuple[int, int]]) -> None:
        self.rows = rows
        self.query = ""
        self.params: tuple[Any, ...] | None = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[tuple[int, int]]:
        return self.rows


class SensorIdentityWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.writer = DataWriter(db_config={}, redis_url="redis://127.0.0.1:6379")

    def test_stale_address_cache_is_discarded_before_registry_lookup(self) -> None:
        self.writer.device_cache[1] = 77
        cursor = FakeCursor([])

        self.writer._prefetch_registry_device_ids(cursor, {1})

        self.assertNotIn(1, self.writer.device_cache)
        self.assertIn("FROM sensor_registry", cursor.query)
        self.assertIn("identity_status = 'pending'", cursor.query)
        self.assertEqual(cursor.params, ([1], ABSENCE))

    def test_current_registry_device_is_used_for_the_can_address(self) -> None:
        cursor = FakeCursor([(1, 88)])

        self.writer._prefetch_registry_device_ids(cursor, {1})

        self.assertEqual(self.writer.device_cache, {1: 88})

    def test_identity_absence_threshold_is_ten_minutes(self) -> None:
        self.assertEqual(ABSENCE, timedelta(minutes=10))
