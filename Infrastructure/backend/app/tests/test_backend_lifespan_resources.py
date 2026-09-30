"""Backend lifespan resource cleanup behavior tests (plan step 3, #5).

Fake-only: signal/notifier functions, the broadcast factory, the config
consumer and DB/Redis teardown adapters are stubbed BEFORE lifespan runs;
real consumer/broadcast factories never execute and no signal handler is
installed in the test process.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import unittest
from unittest.mock import MagicMock, patch

from app import main as app_main


class FakeConsumer:
    """Stub standing in for ConfigEventConsumer."""

    def __init__(self, start_error: Exception | None = None) -> None:
        self.start_error = start_error
        self.started = False
        self.stop_calls = 0
        self.stop_error: Exception | None = None

    async def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    async def stop(self) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        self.stop_calls += 1


class LifespanResourcesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        app_main.shutdown_event = asyncio.Event()
        app_main.background_task = None
        self._stack = ExitStack()
        # Signal handlers/notifiers stubbed before entry.
        self._stack.enter_context(patch.object(app_main, "setup_signal_handlers", lambda: None))
        self._stack.enter_context(patch.object(app_main, "notify_started", MagicMock()))
        self._stack.enter_context(patch.object(app_main, "notify_stopping", MagicMock()))

    def tearDown(self) -> None:
        self._stack.close()
        app_main.background_task = None
        app_main.shutdown_event = asyncio.Event()

    def stub_resources(
        self,
        *,
        consumer: FakeConsumer,
        broadcast_blocking: bool = True,
        redis_error: Exception | None = None,
        db_error: Exception | None = None,
    ) -> dict[str, list]:
        """Stub broadcast coro, consumer class and both teardown adapters.

        Returns a recorder dict: ``events`` collects teardown order and
        exceptions, ``broadcast_done`` is set when the broadcast task exits.
        """
        record: dict[str, list] = {"events": [], "broadcast_done": asyncio.Event()}
        broadcast_started = asyncio.Event()

        if broadcast_blocking:

            async def fake_broadcast() -> None:
                broadcast_started.set()
                try:
                    await asyncio.Event().wait()  # until cancelled
                except asyncio.CancelledError:
                    record["events"].append("broadcast-cancelled")
                    record["broadcast_done"].set()
                    raise
                record["events"].append("broadcast-completed")  # pragma: no cover
        else:

            async def fake_broadcast() -> None:
                record["events"].append("broadcast-completed")
                record["broadcast_done"].set()

        self._stack.enter_context(
            patch("app.background_tasks.broadcast_latest_sensor_data", new=fake_broadcast)
        )
        consumer_factory = lambda: consumer  # noqa: E731
        self._stack.enter_context(
            patch("app.events.consumer.ConfigEventConsumer", new=consumer_factory)
        )

        async def fake_close_redis() -> None:
            record["events"].append("redis-close")
            if redis_error is not None:
                raise redis_error

        async def fake_close_db() -> None:
            record["events"].append("db-close")
            if db_error is not None:
                raise db_error

        self._stack.enter_context(
            patch("app.redis_client.close_redis_client", new=fake_close_redis)
        )
        self._stack.enter_context(
            patch("app.dependencies.close_database_resources", new=fake_close_db)
        )
        return record

    async def run_lifespan(self, *, raise_in_body: Exception | None = None):
        exc: BaseException | None = None
        cm = app_main.lifespan(app=None)  # type: ignore[arg-type]
        try:
            async with cm:
                if raise_in_body is not None:
                    raise raise_in_body
                # Let the monitor task run once; shutdown_event stays unset
                # so the monitor stays pending until cleanup cancels it.
                await asyncio.sleep(0)
        except BaseException as caught:
            exc = caught
        return exc

    async def test_normal_cleanup_order_awaits_monitor_and_detaches(self) -> None:
        consumer = FakeConsumer()
        record = self.stub_resources(consumer=consumer)

        exc = await self.run_lifespan()

        self.assertIsNone(exc, f"no lifespan error: {exc!r}")
        # cleanup order: broadcast cancellation, monitor cancelled, consumer
        # stopped, then redis, then db.
        self.assertIn("broadcast-cancelled", record["events"])
        self.assertEqual(record["events"][-2:], ["redis-close", "db-close"])
        self.assertEqual(consumer.stop_calls, 1)
        self.assertIsNone(app_main.background_task, "global task reference detached")
        # Monitor task was awaited: it must not be pending at exit.
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        self.assertEqual(pending, [], f"no leaked tasks: {pending}")

    async def test_partial_startup_failure_still_stops_constructed_consumer(self) -> None:
        consumer = FakeConsumer(start_error=RuntimeError("consumer start failed"))
        record = self.stub_resources(consumer=consumer)

        exc = await self.run_lifespan()

        self.assertIsInstance(exc, RuntimeError, "startup error propagates")
        self.assertEqual(str(exc), "consumer start failed")
        self.assertEqual(
            consumer.stop_calls, 1, "constructed consumer stopped even though start failed"
        )
        self.assertIn("redis-close", record["events"])
        self.assertIn("db-close", record["events"])
        self.assertIsNone(app_main.background_task)

    async def test_redis_close_failure_does_not_skip_db_close(self) -> None:
        consumer = FakeConsumer()
        record = self.stub_resources(
            consumer=consumer, redis_error=RuntimeError("redis close failed")
        )

        exc = await self.run_lifespan()

        self.assertIsNone(exc, "ordinary teardown failure is logged, not raised")
        self.assertEqual(record["events"][-2:], ["redis-close", "db-close"])
        self.assertEqual(consumer.stop_calls, 1)

    async def test_body_exception_propagates_after_full_cleanup(self) -> None:
        consumer = FakeConsumer()
        record = self.stub_resources(consumer=consumer)
        original = RuntimeError("app failed during run")

        exc = await self.run_lifespan(raise_in_body=original)

        self.assertIs(exc, original, "original body exception not masked")
        self.assertEqual(record["events"][-2:], ["redis-close", "db-close"])
        self.assertEqual(consumer.stop_calls, 1)
        self.assertIsNone(app_main.background_task)


if __name__ == "__main__":
    unittest.main()
