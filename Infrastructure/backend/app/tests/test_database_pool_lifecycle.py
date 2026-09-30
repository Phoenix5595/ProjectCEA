"""DatabaseManager lazy-pool lifecycle behavior tests (plan step 3, #5).

Fakes only: the real ``create_pool`` is patched with deterministic fakes
before any call, so no database, network or scheduler callback runs.
"""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock

from app.database import DatabaseManager


class FakeAsyncpgPool:
    """Fake asyncpg pool recording construction and teardown."""

    constructed = 0

    def __init__(self, name: str = "pool") -> None:
        FakeAsyncpgPool.constructed += 1
        self.name = name
        self.close_calls = 0
        self.closed = False

    async def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class FakeSlowPool(FakeAsyncpgPool):
    async def close(self) -> None:
        self.close_calls += 1
        await asyncio.sleep(0.01)  # teardown latency for race coverage
        self.closed = True


class PoolLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        FakeAsyncpgPool.constructed = 0
        self.patches: list[object] = []

    def tearDown(self) -> None:
        for patcher in self.patches:
            patcher.stop()

    def patch_factory(self, factory) -> None:
        from app import database as dbmod

        patcher = unittest.mock.patch.object(dbmod, "create_pool", new=factory)
        patcher.start()
        self.patches.append(patcher)

    async def test_concurrent_initializers_share_identity(self) -> None:
        """Deterministic concurrent first-getters share one pool identity."""

        async def fake_factory(db_config, *, application_name):
            self.assertEqual(application_name, "cea_backend")
            await asyncio.sleep(0.01)  # widen the race window deterministically
            return FakeAsyncpgPool()

        self.patch_factory(fake_factory)
        manager = DatabaseManager(db_config={"host": "x"})

        results = await asyncio.gather(
            manager._get_pool(), manager._get_pool(), manager._get_pool()
        )

        self.assertEqual({id(pool) for pool in results}, {id(results[0])})
        self.assertEqual(FakeAsyncpgPool.constructed, 1)
        self.assertIs(manager._pool, results[0])

    async def test_failed_initializer_publishes_no_owner_and_preserves_factory(self) -> None:
        failures = 0

        async def failing_then_succeeding(db_config, *, application_name):
            nonlocal failures
            failures += 1
            if failures == 1:
                raise ConnectionError("no db after retries")
            return FakeAsyncpgPool()

        self.patch_factory(failing_then_succeeding)
        manager = DatabaseManager(db_config={"host": "x"})

        with self.assertRaises(ConnectionError):
            await manager._get_pool()
        self.assertIsNone(manager._pool, "failed init publishes nothing")

        # Factory retry settings preserved: next attempt calls factory again.
        pool = await manager._get_pool()
        self.assertEqual(failures, 2)
        self.assertIsInstance(pool, FakeAsyncpgPool)

    async def test_cancelled_initializer_publishes_no_owner(self) -> None:
        started = asyncio.Event()

        async def hanging_factory(db_config, *, application_name):
            started.set()
            await asyncio.sleep(10)
            return FakeAsyncpgPool()  # pragma: no cover - never reached

        self.patch_factory(hanging_factory)
        manager = DatabaseManager(db_config={"host": "x"})

        task = asyncio.create_task(manager._get_pool())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(manager._pool, "cancelled init publishes nothing")

        # Manager still usable afterwards: factory retried and publishes.
        async def quick_factory(db_config, *, application_name):
            return FakeAsyncpgPool()

        self.patch_factory(quick_factory)
        pool = await manager._get_pool()
        self.assertIsInstance(pool, FakeAsyncpgPool)

    async def test_unused_close_creates_nothing(self) -> None:
        async def factory_should_not_run(db_config, *, application_name):
            raise AssertionError("close must not initialize the factory")

        self.patch_factory(factory_should_not_run)
        manager = DatabaseManager(db_config={"host": "x"})

        await manager.close()  # no pool, no factory call, no error
        self.assertIsNone(manager._pool)
        self.assertEqual(FakeAsyncpgPool.constructed, 0)

    async def test_idempotent_close_tears_down_once(self) -> None:
        pool = FakeSlowPool()
        got = asyncio.Event()

        async def one_shot_factory(db_config, *, application_name):
            if got.is_set():
                raise AssertionError("factory called twice")
            got.set()
            return pool

        self.patch_factory(one_shot_factory)
        manager = DatabaseManager(db_config={"host": "x"})
        await manager._get_pool()

        await asyncio.gather(manager.close(), manager.close())
        self.assertEqual(pool.close_calls, 1)
        self.assertIsNone(manager._pool)

        # Post-close get() builds a new pool (same manager, fresh loop use).
        replacement = FakeAsyncpgPool()

        async def replacement_factory(db_config, *, application_name):
            return replacement

        self.patch_factory(replacement_factory)
        self.assertIs(await manager._get_pool(), replacement)

    async def test_close_detaches_before_teardown_and_never_restores_on_error(self) -> None:
        class ExplodingPool(FakeAsyncpgPool):
            async def close(self) -> None:
                self.close_calls += 1
                raise RuntimeError("teardown blew up")

        pool = ExplodingPool()

        async def factory(db_config, *, application_name):
            return pool

        self.patch_factory(factory)
        manager = DatabaseManager(db_config={"host": "x"})
        await manager._get_pool()

        with self.assertRaises(RuntimeError):
            await manager.close()
        self.assertIsNone(manager._pool, "teardown error must not restore _pool")
        self.assertEqual(pool.close_calls, 1)

        # The detached (closed) pool is not published again afterwards.
        async def after_close_factory(db_config, *, application_name):
            return FakeAsyncpgPool()

        self.patch_factory(after_close_factory)
        fresh = await manager._get_pool()
        self.assertIsNot(fresh, pool)


if __name__ == "__main__":
    unittest.main()
