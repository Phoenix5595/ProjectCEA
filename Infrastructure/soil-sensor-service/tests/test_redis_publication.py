from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import cast
import unittest
from unittest.mock import patch

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVICE_ROOT))

from app.redis_client import RedisClient  # noqa: E402
from shared.redis_keys import (  # noqa: E402
    SENSOR_UPDATE_CHANNEL,
    SENSOR_UPDATE_SOIL_CHANNEL,
    SOIL_SENSOR_CURRENT_TTL_SEC,
    sensor_full,
    sensor_full_ts,
)


class FakePipeline:
    client: FakeRedis
    commands: list[tuple[str, tuple[str, str] | tuple[str, int, str]]]

    def __init__(self, client: FakeRedis) -> None:
        self.client = client
        self.commands = []

    def publish(self, channel: str, message: str) -> None:
        self.commands.append(("publish", (channel, message)))

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.commands.append(("setex", (key, ttl, value)))

    async def execute(self) -> list[object]:
        if self.client.fail_execute:
            raise RuntimeError("Redis transaction failed")
        for command, args in self.commands:
            if command == "publish":
                channel, message = cast(tuple[str, str], args)
                self.client.notifications.append((channel, message))
            else:
                key, ttl, value = cast(tuple[str, int, str], args)
                self.client.state[key] = (value, ttl)
        return []


class FakeRedis:
    state: dict[str, tuple[str, int]]
    notifications: list[tuple[str, str]]
    fail_execute: bool

    def __init__(self) -> None:
        self.state = {}
        self.notifications = []
        self.fail_execute = False

    def pipeline(self, *, transaction: bool = True) -> FakePipeline:
        _ = transaction
        return FakePipeline(self)


class SteppingDateTime:
    calls: int = 0

    @classmethod
    def now(cls) -> datetime:
        timestamp = datetime(2026, 9, 27, 12, 0, 0) + timedelta(milliseconds=cls.calls)
        cls.calls += 1
        return timestamp


class SoilPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_four_readings_keep_channels_fields_keys_ttls_and_per_reading_times(self) -> None:
        redis = FakeRedis()
        client = RedisClient()
        client.redis_enabled = True
        client.redis_client = redis
        readings = {"temperature": 24.5, "humidity": 65.0, "ec": 1200.0, "ph": 6.2}
        units = {"temperature": "°C", "humidity": "%", "ec": "µS/cm", "ph": "pH"}
        base_name = "soil_sensor_front_bed"
        location = "Flower Room"
        bed_name = "Front Bed"
        SteppingDateTime.calls = 0

        with patch("app.redis_client.datetime", SteppingDateTime):
            succeeded = await client.publish_all_readings(base_name, readings, bed_name, location)

        self.assertTrue(succeeded)
        expected_sensor_names = [f"{base_name}_{sensor_type}" for sensor_type in readings]
        expected_channels = [SENSOR_UPDATE_CHANNEL, SENSOR_UPDATE_SOIL_CHANNEL] * len(
            expected_sensor_names
        )
        self.assertEqual([channel for channel, _ in redis.notifications], expected_channels)
        for index, sensor_type in enumerate(readings):
            sensor_name = expected_sensor_names[index]
            message = cast(dict[str, object], json.loads(redis.notifications[index * 2][1]))
            timestamp = datetime(2026, 9, 27, 12, 0, 0) + timedelta(milliseconds=index)
            self.assertEqual(
                message,
                {
                    "sensor_name": sensor_name,
                    "value": readings[sensor_type],
                    "unit": units[sensor_type],
                    "timestamp": timestamp.isoformat(),
                    "location": location,
                    "bed": bed_name,
                },
            )
            timestamp_ms = int(timestamp.timestamp() * 1000)
            self.assertEqual(
                redis.notifications[index * 2 + 1][1], redis.notifications[index * 2][1]
            )
            self.assertEqual(
                redis.state[sensor_full(location, "main", sensor_name)],
                (str(readings[sensor_type]), SOIL_SENSOR_CURRENT_TTL_SEC),
            )
            self.assertEqual(
                redis.state[sensor_full_ts(location, "main", sensor_name)],
                (str(timestamp_ms), SOIL_SENSOR_CURRENT_TTL_SEC),
            )

    async def test_empty_and_unsupported_readings_are_successful_without_state_changes(
        self,
    ) -> None:
        redis = FakeRedis()
        client = RedisClient()
        client.redis_enabled = True
        client.redis_client = redis

        self.assertTrue(await client.publish_all_readings("soil_sensor", {}, "Bed"))
        self.assertTrue(
            await client.publish_all_readings("soil_sensor", {"pressure": 1013.0}, "Bed")
        )
        self.assertEqual(redis.state, {})
        self.assertEqual(redis.notifications, [])

    async def test_disconnected_and_failed_redis_publication_return_false(self) -> None:
        readings = {"temperature": 24.5}
        client = RedisClient()
        self.assertFalse(await client.publish_all_readings("soil_sensor", readings, "Bed"))

        redis = FakeRedis()
        redis.fail_execute = True
        client.redis_enabled = True
        client.redis_client = redis
        self.assertFalse(await client.publish_all_readings("soil_sensor", readings, "Bed"))
        self.assertEqual(redis.state, {})
        self.assertEqual(redis.notifications, [])


if __name__ == "__main__":
    _ = unittest.main()
