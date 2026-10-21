from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import redis

from app import writer as writer_module
from app.sensor_registry import AssignmentCache, MetadataWorker, SensorAssignment
from app.writer import DataWriter


class FakeStreamClient:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[bytes, bytes]]] = []

    def xadd(self, stream: str, fields: dict[bytes, bytes], **_kwargs: object) -> None:
        self.entries.append((stream, fields))


class FakePipeline:
    def __init__(self, *, fail_on_execute: bool = False) -> None:
        self.commands: list[tuple[str, str, int | None]] = []
        self.fail_on_execute = fail_on_execute

    def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.commands.append((key, value, ex))

    def execute(self) -> list[object]:
        if self.fail_on_execute:
            raise redis.exceptions.ConnectionError("redis unavailable")
        return []


class FakeStateClient:
    def __init__(self, pipeline: FakePipeline | None = None) -> None:
        self._pipeline = pipeline or FakePipeline()

    def pipeline(self) -> FakePipeline:
        return self._pipeline


class RedisCooldownTests(unittest.TestCase):
    def test_failed_connect_retries_after_five_seconds_and_keeps_db_enqueue(self) -> None:
        writer = DataWriter(db_config={}, redis_url="redis://127.0.0.1:6379")
        stream_client = FakeStreamClient()
        state_client = FakeStateClient()
        now = [0.0]
        connect_results: list[object] = [
            redis.exceptions.ConnectionError("redis unavailable"),
            (stream_client, None),
            (state_client, None),
        ]

        def create_client(*_args: object, **_kwargs: object) -> object:
            result = connect_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        msg = SimpleNamespace(data=(0xAA, 0xBB), arbitration_id=0x123, dlc=2)
        assignment = SensorAssignment(17, 17, "Flower Room", "front")
        timestamp = datetime(2026, 10, 21, tzinfo=UTC)
        sensor_values = [("dry_bulb_f", 24.5, "°C")]

        with (
            patch.object(writer_module, "time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(writer_module, "create_sync_client", side_effect=create_client) as connect,
            patch.object(writer_module.logger, "warning") as warning,
            patch.object(writer_module.logger, "info") as info,
        ):
            result = writer.write(
                msg,
                {"node_id": 17},
                "AA BB",
                sensor_values,
                timestamp,
                1_792_569_600_000,
                assignment,
            )
            self.assertEqual(result, {"stream": False, "db": True, "redis": False})
            self.assertEqual(writer._batch_queue.stats()["queued"], 1)
            self.assertEqual(connect.call_count, 1)
            self.assertEqual(writer._redis_retry_at, 5.0)

            for retry_time in (1.0, 4.99):
                now[0] = retry_time
                self.assertFalse(writer.write_to_stream(msg, {}, "AA BB"))
                self.assertFalse(
                    writer.write_to_redis_state("Flower Room", "front", sensor_values, 0)
                )
                self.assertEqual(connect.call_count, 1)

            now[0] = 5.0
            self.assertTrue(writer.write_to_stream(msg, {}, "AA BB"))
            self.assertTrue(
                writer.write_to_redis_state("Flower Room", "front", sensor_values, 0)
            )
            self.assertEqual(connect.call_count, 3)
            self.assertEqual(stream_client.entries[0][1][b"data"], b"AA BB")
            self.assertEqual(
                warning.call_args_list,
                [call("Redis unavailable; retrying every 5s")],
            )
            info.assert_called_once_with("Redis reconnected")

    def test_stream_connection_error_starts_retry_cooldown(self) -> None:
        class BrokenStreamClient:
            def xadd(self, *_args: object, **_kwargs: object) -> None:
                raise redis.exceptions.ConnectionError("redis unavailable")

        writer = DataWriter(db_config={}, redis_url="redis://127.0.0.1:6379")
        writer.redis_enabled = True
        writer.redis_client = BrokenStreamClient()  # type: ignore[assignment]
        now = [10.0]

        with (
            patch.object(writer_module, "time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(writer_module, "create_sync_client") as connect,
            patch.object(writer_module.logger, "warning") as warning,
        ):
            msg = SimpleNamespace(data=(), arbitration_id=0x123, dlc=0)
            self.assertFalse(writer.write_to_stream(msg, {}, ""))
            self.assertFalse(writer.redis_enabled)
            self.assertEqual(writer._redis_retry_at, 15.0)

            now[0] = 14.99
            self.assertFalse(writer.write_to_stream(msg, {}, ""))
            connect.assert_not_called()
            warning.assert_called_once_with("Redis unavailable; retrying every 5s")

    def test_state_connection_error_starts_retry_cooldown(self) -> None:
        writer = DataWriter(db_config={}, redis_url="redis://127.0.0.1:6379")
        writer.redis_enabled = True
        writer.redis_state_client = FakeStateClient(FakePipeline(fail_on_execute=True))  # type: ignore[assignment]
        now = [20.0]

        with (
            patch.object(writer_module, "time", SimpleNamespace(monotonic=lambda: now[0])),
            patch.object(writer_module, "create_sync_client") as connect,
            patch.object(writer_module.logger, "warning") as warning,
        ):
            self.assertFalse(
                writer.write_to_redis_state(
                    "Flower Room", "front", [("dry_bulb_f", 24.5, "°C")], 0
                )
            )
            self.assertFalse(writer.redis_enabled)
            self.assertEqual(writer._redis_retry_at, 25.0)

            now[0] = 24.99
            self.assertFalse(writer.write_to_redis_state("Flower Room", "front", [], 0))
            connect.assert_not_called()
            warning.assert_called_once_with("Redis unavailable; retrying every 5s")


class MetadataDrainTests(unittest.TestCase):
    def test_drain_upserts_each_node_once_in_first_seen_order(self) -> None:
        on_change = Mock()
        worker = MetadataWorker(cache=AssignmentCache(), db_config={}, on_change=on_change)
        for node_id in (17, 17, 18):
            worker.offer(node_id)

        connection = object()
        with patch.object(worker, "_upsert_node", side_effect=(False, True)) as upsert:
            worker._drain_observations(connection)  # type: ignore[arg-type]

        self.assertEqual([call.args[1] for call in upsert.call_args_list], [17, 18])
        self.assertEqual(worker._observations.qsize(), 0)
        on_change.assert_called_once_with()

    def test_drain_dequeues_at_most_two_hundred_observations(self) -> None:
        worker = MetadataWorker(cache=AssignmentCache(), db_config={})
        for _ in range(201):
            worker.offer(17)

        with patch.object(worker, "_upsert_node", return_value=False) as upsert:
            worker._drain_observations(object())  # type: ignore[arg-type]

        upsert.assert_called_once()
        self.assertEqual(worker._observations.qsize(), 1)
