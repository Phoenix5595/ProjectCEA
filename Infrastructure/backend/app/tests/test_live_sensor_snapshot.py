from __future__ import annotations

import asyncio
from datetime import datetime
from fnmatch import fnmatchcase
import unittest
from unittest.mock import AsyncMock, patch

from app import background_tasks, redis_client
from app.redis_client import _read_sensor_snapshot
from app.routes import live, sensors
from shared.redis_keys import sensor_full, sensor_full_ts


class FakeRedis:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.values: dict[str, str | None] = {}

    async def scan_iter(self, *, match: str, count: int):
        _ = count
        for key in self.keys:
            if fnmatchcase(key, match):
                yield key

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self.values.get(key) for key in keys]

    def add_sensor(
        self,
        location: str,
        cluster: str,
        sensor_name: str,
        value: str,
        timestamp_ms: str | None,
    ) -> str:
        value_key = sensor_full(location, cluster, sensor_name)
        timestamp_key = sensor_full_ts(location, cluster, sensor_name)
        self.keys.append(value_key)
        self.values[value_key] = value
        if timestamp_ms is not None:
            self.keys.append(timestamp_key)
            self.values[timestamp_key] = timestamp_ms
        return value_key


class FakeSensorRepository:
    async def get_live_sensors(self, _location: str, _cluster: str) -> list[str]:
        return ["dry_bulb_b", "rh_b"]


class LiveSensorSnapshotTests(unittest.IsolatedAsyncioTestCase):
    def patch_redis(self, client: FakeRedis | None):
        return patch.object(
            redis_client,
            "get_redis_client",
            new=AsyncMock(return_value=client),
        )

    async def test_snapshot_pairs_last_duplicate_value_with_its_qualified_timestamp(self) -> None:
        client = FakeRedis()
        now_ms = int(datetime.now().timestamp() * 1000)
        first_key = client.add_sensor(
            "Flower Room", "back", "dry_bulb_b", "20", str(now_ms - 40_000)
        )
        second_key = client.add_sensor(
            "Flower Room", "front", "dry_bulb_b", "24.5", str(now_ms - 5_000)
        )
        client.keys.insert(1, first_key)
        client.keys.insert(4, second_key)
        client.add_sensor("Flower Room", "back", "rh_b", "65", "invalid-timestamp")
        client.add_sensor("Flower Room", "back", "co2_b", "not-numeric", str(now_ms))
        client.add_sensor("Flower Room", "back", "vpd_b", "1.2", None)

        with self.patch_redis(client):
            response = await live.build_live_snapshot()

        self.assertEqual(response.values["dry_bulb_b"].value, 24.5)
        self.assertFalse(response.values["dry_bulb_b"].stale)
        self.assertEqual(response.values["dry_bulb_b"].location, "Flower Room")
        self.assertEqual(response.values["dry_bulb_b"].cluster, "back")
        self.assertTrue(response.values["rh_b"].stale)
        self.assertIsNone(response.values["rh_b"].age_seconds)
        self.assertTrue(response.values["vpd_b"].stale)
        self.assertIsNone(response.values["vpd_b"].age_seconds)
        self.assertNotIn("co2_b", response.values)

    async def test_duplicate_scan_keys_do_not_consume_the_unique_key_cap(self) -> None:
        client = FakeRedis()
        names = [f"probe_{index:04d}" for index in range(5001)]
        keys = [sensor_full("Lab", "main", name) for name in names]
        client.keys = [keys[0]] * 6 + keys
        client.values = dict.fromkeys(keys, "1.0")

        with self.patch_redis(client):
            response = await live.build_live_snapshot()

        self.assertEqual(len(response.values), 5000)
        self.assertIn("probe_4999", response.values)
        self.assertNotIn("probe_5000", response.values)

    async def test_live_sensor_routes_keep_their_response_shapes(self) -> None:
        client = FakeRedis()
        now_ms = int(datetime.now().timestamp() * 1000)
        client.add_sensor("Flower Room", "back", "dry_bulb_b", "24.5", str(now_ms - 2_000))
        client.add_sensor("Flower Room", "back", "rh_b", "65", "invalid-timestamp")

        with (
            self.patch_redis(client),
            patch.object(sensors, "get_sensor_repository", return_value=FakeSensorRepository()),
        ):
            per_cluster = await sensors.get_live_sensor_data("Flower Room", "back")
            all_sensors = await sensors.get_all_live_sensor_data()

        self.assertEqual(set(per_cluster), {"dry_bulb_b", "rh_b"})
        self.assertEqual(per_cluster["dry_bulb_b"].sensor_type, "dry_bulb_b")
        self.assertEqual(per_cluster["dry_bulb_b"].data[0].value, 24.5)
        self.assertEqual(per_cluster["dry_bulb_b"].unit, "°C")
        self.assertEqual(
            {row["sensor"] for row in all_sensors},
            {"dry_bulb_b", "rh_b"},
        )
        dry_bulb = next(row for row in all_sensors if row["sensor"] == "dry_bulb_b")
        self.assertEqual(dry_bulb["value"], 24.5)
        self.assertEqual(dry_bulb["unit"], "°C")

    async def test_background_broadcast_preserves_websocket_payload(self) -> None:
        client = FakeRedis()
        timestamp_ms = int(datetime.now().timestamp() * 1000) - 1_000
        client.add_sensor("Flower Room", "back", "dry_bulb_b", "24.5", str(timestamp_ms))
        broadcast = AsyncMock()

        async def stop_after_one_cycle(_delay: float) -> None:
            raise asyncio.CancelledError

        with (
            self.patch_redis(client),
            patch.object(background_tasks.websocket_manager, "broadcast_sensor_update", broadcast),
            patch.object(background_tasks.asyncio, "sleep", stop_after_one_cycle),
            self.assertRaises(asyncio.CancelledError),
        ):
            await background_tasks.broadcast_latest_sensor_data()

        broadcast.assert_awaited_once()
        payload = broadcast.await_args.kwargs
        self.assertEqual(payload["location"], "Flower Room")
        self.assertEqual(payload["cluster"], "back")
        self.assertEqual(payload["sensor_type"], "dry_bulb_b")
        self.assertEqual(payload["value"], 24.5)
        self.assertEqual(payload["unit"], "°C")
        self.assertEqual(payload["timestamp"].timestamp(), timestamp_ms / 1000.0)

    async def test_live_snapshot_preserves_redis_unavailable_response(self) -> None:
        with self.patch_redis(None), self.assertRaises(live.HTTPException) as caught:
            await live.build_live_snapshot()

        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(
            caught.exception.detail,
            "Redis unavailable. Live sensor data cannot be retrieved.",
        )


class PairedSnapshotReadTests(unittest.IsolatedAsyncioTestCase):
    """Qualified pairing, unavailable values and malformed bulk replies."""

    def patch_client(self, client):
        async def fake_get():
            return client

        return patch.object(redis_client, "get_redis_client", new=fake_get)

    async def test_snapshot_filters_missing_values_and_preserves_unknown_timestamps(self) -> None:
        client = FakeRedis()
        client.add_sensor("Veg Room", "main", "dry_bulb_v", "21.5", "1700000000000")
        client.add_sensor("Veg Room", "main", "rh_v", "60", "invalid")
        client.add_sensor("Veg Room", "main", "vpd_v", "0", None)
        client.add_sensor("Veg Room", "main", "bad_value", "invalid", "1700000005000")
        missing_key = client.add_sensor(
            "Veg Room", "main", "missing_value", "unused", "1700000005000"
        )
        client.values[missing_key] = None

        with self.patch_client(client):
            snapshot = await _read_sensor_snapshot()

        self.assertEqual(
            snapshot,
            {
                "dry_bulb_v": ("Veg Room", "main", "dry_bulb_v", 21.5, 1700000000000),
                "rh_v": ("Veg Room", "main", "rh_v", 60.0, None),
                "vpd_v": ("Veg Room", "main", "vpd_v", 0.0, None),
            },
        )

    async def test_malformed_mget_cardinality_returns_empty_snapshot(self) -> None:
        client = FakeRedis()
        client.add_sensor("Veg Room", "main", "dry_bulb_v", "21.5", "1700000000000")

        class TornReplyClient(FakeRedis):
            async def mget(self, keys: list[str]) -> list[str | None]:
                _ = keys
                # torn/truncated reply: fewer results than interleaved keys
                return ["21.5"]

        torn = TornReplyClient()
        torn.values = dict(client.values)
        torn.keys = list(client.keys)

        with self.patch_client(torn):
            snapshot = await _read_sensor_snapshot()

        self.assertEqual(snapshot, {}, "cardinality mismatch -> existing empty path")


if __name__ == "__main__":
    _ = unittest.main()
