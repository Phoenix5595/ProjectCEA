from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from redis.exceptions import RedisError

from app import background_tasks, redis_client
from app.redis_client import _read_sensor_snapshot
from app.repositories.sensor_registry_repository import SensorRegistryRepository
from app.routes import sensor_data, sensors
from shared.redis_keys import sensor_full, sensor_full_ts


class FakeRedis:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.values: dict[str, str | None] = {}
        self.scan_calls = 0
        self.mget_calls: list[list[str]] = []

    async def scan_iter(self, *, match: str, count: int):
        _ = count
        self.scan_calls += 1
        for key in self.keys:
            if fnmatchcase(key, match):
                yield key

    async def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls.append(list(keys))
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
    def setUp(self) -> None:
        redis_client._KEY_CACHE = None

    def patch_redis(self, client: FakeRedis | None):
        return patch.object(
            redis_client,
            "get_redis_client",
            new=AsyncMock(return_value=client),
        )

    async def test_snapshot_pairs_duplicate_value_with_qualified_timestamp(self) -> None:
        client = FakeRedis()
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
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
            snapshot = await _read_sensor_snapshot()

        self.assertEqual(
            snapshot["dry_bulb_b"],
            ("Flower Room", "front", "dry_bulb_b", 24.5, now_ms - 5_000),
        )
        self.assertIsNone(snapshot["rh_b"][4])
        self.assertIsNone(snapshot["vpd_b"][4])
        self.assertNotIn("co2_b", snapshot)

    async def test_duplicate_scan_keys_do_not_consume_the_unique_key_cap(self) -> None:
        client = FakeRedis()
        names = [f"probe_{index:04d}" for index in range(5001)]
        keys = [sensor_full("Lab", "main", name) for name in names]
        client.keys = [keys[0]] * 6 + keys
        client.values = dict.fromkeys(keys, "1.0")

        with self.patch_redis(client):
            snapshot = await _read_sensor_snapshot()

        self.assertEqual(len(snapshot), 5000)
        self.assertIn("probe_4999", snapshot)
        self.assertNotIn("probe_5000", snapshot)

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

    async def test_sensor_data_uses_one_ordered_mget_with_existing_precedence(self) -> None:
        client = SimpleNamespace(
            mget=AsyncMock(
                return_value=[
                    "21.5",
                    "18",
                    None,
                    None,
                    "22",
                    "24",
                    '{"intensity":31.5}',
                ]
            )
        )
        requested = [
            "Veg Room_main_dry_bulb_v",
            "Veg Room_main_heating_setpoint",
            "Veg Room_main_cooling_setpoint",
            "Veg Room_main_light_1_intensity",
        ]
        expected_keys = [
            sensor_full("Veg Room", "main", "dry_bulb_v"),
            sensor_full("Veg Room", "main", "heating_setpoint"),
            sensor_full("Veg Room", "main", "cooling_setpoint"),
            sensor_full("Veg Room", "main", "light_1_intensity"),
            "cea:effective_setpoint:Veg Room:main:heating_setpoint",
            "cea:effective_setpoint:Veg Room:main:cooling_setpoint",
            "cea:light:Veg Room:main:light_1",
        ]

        with patch.object(
            sensor_data,
            "get_redis_client",
            new=AsyncMock(return_value=client),
        ):
            result = await sensor_data.post_sensor_data({"keys": requested})

        client.mget.assert_awaited_once_with(expected_keys)
        self.assertEqual(result["Veg Room_main_dry_bulb_v"], 21.5)
        self.assertEqual(result["Veg Room_main_heating_setpoint"], 18.0)
        self.assertEqual(result["Veg Room_main_cooling_setpoint"], 24.0)
        self.assertEqual(result["Veg Room_main_light_1_intensity"], 31.5)

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



class PairedSnapshotReadTests(unittest.IsolatedAsyncioTestCase):
    """Qualified pairing, unavailable values and malformed bulk replies."""

    def setUp(self) -> None:
        redis_client._KEY_CACHE = None

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

    async def test_key_names_cache_expires_but_mget_reads_current_values(self) -> None:
        client = FakeRedis()
        first_key = client.add_sensor("Lab", "main", "probe_a", "20", "1700000000000")
        clock = [100.0]

        with (
            self.patch_client(client),
            patch.object(
                redis_client, "time", SimpleNamespace(monotonic=lambda: clock[0])
            ),
        ):
            first = await _read_sensor_snapshot()
            client.add_sensor("Lab", "main", "probe_b", "22", "1700000001000")
            client.values[first_key] = "21"
            clock[0] = 129.999
            within_ttl = await _read_sensor_snapshot()

            clock[0] = 130.0
            after_expiry = await _read_sensor_snapshot()
            client.values[first_key] = None
            clock[0] = 130.001
            after_delete = await _read_sensor_snapshot()

        self.assertEqual(first["probe_a"][3], 20.0)
        self.assertEqual(within_ttl["probe_a"][3], 21.0)
        self.assertNotIn("probe_b", within_ttl)
        self.assertIn("probe_b", after_expiry)
        self.assertNotIn("probe_a", after_delete)
        self.assertEqual(client.scan_calls, 2)
        self.assertEqual(len(client.mget_calls), 4)
        self.assertEqual(
            client.mget_calls[0],
            [first_key, sensor_full_ts("Lab", "main", "probe_a")],
        )

    async def test_scan_failure_does_not_replace_the_expired_cache(self) -> None:
        client = FakeRedis()
        client.add_sensor("Lab", "main", "probe_a", "20", "1700000000000")

        class FailingScanner:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise RedisError("scan unavailable")

        class ScanFailureRedis:
            def __init__(self) -> None:
                self.scan_calls = 0

            def scan_iter(self, *, match: str, count: int):
                _ = match, count
                self.scan_calls += 1
                return FailingScanner()

        failed_client = ScanFailureRedis()
        client_sequence = AsyncMock(side_effect=[client, failed_client, client])
        clock = [200.0]
        with (
            patch.object(redis_client, "get_redis_client", new=client_sequence),
            patch.object(
                redis_client, "time", SimpleNamespace(monotonic=lambda: clock[0])
            ),
        ):
            await _read_sensor_snapshot()
            cache_before_failure = redis_client._KEY_CACHE
            client.add_sensor("Lab", "main", "probe_b", "22", "1700000001000")
            clock[0] = 230.0
            failed_snapshot = await _read_sensor_snapshot()
            self.assertEqual(failed_snapshot, {})
            self.assertIs(redis_client._KEY_CACHE, cache_before_failure)

            recovered = await _read_sensor_snapshot()

        self.assertEqual(failed_client.scan_calls, 1)
        self.assertIn("probe_b", recovered)


class SoilHistoryBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_soil_history_buckets_all_sensors_in_one_query(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = start + timedelta(hours=1)
        device_rows = [
            {
                "registry_id": 1,
                "hardware_address": 10,
                "display_name": "Probe A",
                "bed": "Bed A",
                "device_id": 101,
            },
            {
                "registry_id": 2,
                "hardware_address": 20,
                "display_name": "Probe B",
                "bed": "Bed B",
                "device_id": 202,
            },
        ]
        sensor_rows = [
            {"sensor_id": 11, "device_id": 101, "name": "soil_sensor_226_temperature"},
            {"sensor_id": 12, "device_id": 101, "name": "soil_sensor_226_humidity"},
            {"sensor_id": 21, "device_id": 202, "name": "soil_sensor_227_temperature"},
        ]
        bucket_rows = [
            {
                "sensor_id": sensor_id,
                "bucket_index": 0,
                "avg_value": average,
                "min_value": average - 1,
                "max_value": average + 1,
                "sample_count": 2,
            }
            for sensor_id, average in ((11, 20.0), (12, 35.0), (21, 22.0))
        ]

        class FakeConnection:
            def __init__(self) -> None:
                self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

            async def fetch(self, query: str, *args: object):
                self.fetch_calls.append((query, args))
                if query == SensorRegistryRepository._SELECT_BED_PROBE:
                    return device_rows
                if query.startswith("SELECT sensor_id, device_id, name FROM sensor"):
                    return sensor_rows
                if "FROM measurement m" in query:
                    return bucket_rows
                raise AssertionError(f"unexpected query: {query}")

        connection = FakeConnection()

        class FakePool:
            @asynccontextmanager
            async def acquire(self):
                yield connection

        repository = SensorRegistryRepository(pool=FakePool())
        response = await repository.soil_history(start, end, max_points=100)

        bucket_queries = [
            (query, args)
            for query, args in connection.fetch_calls
            if "FROM measurement m" in query
        ]
        self.assertEqual(len(bucket_queries), 1)
        self.assertEqual(bucket_queries[0][1][2], [11, 12, 21])
        self.assertEqual(response.bucket_seconds, 36)
        self.assertEqual(
            [(item.hardware_address, item.metric) for item in response.series],
            [(10, "temperature"), (10, "water_content"), (20, "temperature")],
        )
        self.assertEqual(response.series[0].points[0].bucket_start, start)
        self.assertEqual(response.series[0].points[0].average, 20.0)
        self.assertEqual(response.series[1].points[0].average, 35.0)
        self.assertEqual(response.series[2].points[0].average, 22.0)


class SoilLiveMgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_reads_interleaved_full_keys_and_converts_milliseconds(self) -> None:
        names = [
            "soil_sensor_226_temperature",
            "soil_sensor_226_humidity",
            "soil_sensor_226_ec",
            "soil_sensor_226_ph",
        ]
        timestamps_ms = [
            1_700_000_000_123,
            1_700_000_001_123,
            1_700_000_002_123,
            None,
        ]
        redis_values = [
            "22.5",
            timestamps_ms[0],
            None,
            timestamps_ms[1],
            "412.0",
            timestamps_ms[2],
            "6.2",
            timestamps_ms[3],
        ]
        client = SimpleNamespace(mget=AsyncMock(return_value=redis_values))
        row = {
            "registry_id": 7,
            "hardware_address": 226,
            "display_name": "soil_sensor_226",
            "bed": "Bed 1",
            "last_seen": datetime(2026, 1, 1, tzinfo=UTC),
        }
        repository = SensorRegistryRepository(pool=object())
        expected_keys = [
            key
            for name in names
            for key in (
                sensor_full("Flower Room", "main", name),
                sensor_full_ts("Flower Room", "main", name),
            )
        ]

        before = datetime.now(UTC)
        with patch.object(
            redis_client,
            "get_redis_client",
            new=AsyncMock(return_value=client),
        ) as get_client:
            probe = await repository._probe_live(row, names)
        after = datetime.now(UTC)

        get_client.assert_awaited_once_with()
        client.mget.assert_awaited_once_with(expected_keys)
        self.assertEqual(probe.metrics["temperature"].value, 22.5)
        self.assertAlmostEqual(
            probe.metrics["temperature"].observed_at.timestamp(),
            timestamps_ms[0] / 1000.0,
        )
        self.assertIsNone(probe.metrics["water_content"])
        self.assertEqual(probe.metrics["ec"].value, 412.0)
        self.assertAlmostEqual(
            probe.metrics["ec"].observed_at.timestamp(),
            timestamps_ms[2] / 1000.0,
        )
        self.assertEqual(probe.metrics["ph"].value, 6.2)
        self.assertIsNotNone(probe.metrics["ph"].observed_at)
        self.assertGreaterEqual(probe.metrics["ph"].observed_at, before)
        self.assertLessEqual(probe.metrics["ph"].observed_at, after)

    async def test_redis_failure_leaves_all_soil_metrics_missing(self) -> None:
        client = SimpleNamespace(mget=AsyncMock(side_effect=RedisError("offline")))
        row = {
            "registry_id": 7,
            "hardware_address": 226,
            "display_name": "soil_sensor_226",
            "bed": "Bed 1",
            "last_seen": datetime(2026, 1, 1, tzinfo=UTC),
        }
        repository = SensorRegistryRepository(pool=object())

        with patch.object(
            redis_client,
            "get_redis_client",
            new=AsyncMock(return_value=client),
        ):
            probe = await repository._probe_live(
                row, ["soil_sensor_226_temperature", "soil_sensor_226_ph"]
            )

        self.assertEqual(
            probe.metrics,
            dict.fromkeys(("temperature", "water_content", "ec", "ph")),
        )


if __name__ == "__main__":
    _ = unittest.main()
