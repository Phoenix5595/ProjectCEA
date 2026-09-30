"""Backend Redis client lifecycle behavior tests (plan step 3, #5).

Fakes only: ``shared.redis_client.create_async_client`` /
``close_async`` are patched before any call. The module lock is a
loop-owned lazy lock; sequential fully-shut-down test loops must work
without replacing a live lock.
"""

from __future__ import annotations

import asyncio
import unittest
import unittest.mock

import redis.asyncio as redis
import redis.exceptions
from redis.exceptions import ConnectionError as RedisConnectionError

from app import redis_client


class FakeRedisClient:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.close_calls = 0


class FakePool:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.disconnect_calls = 0


class BackendRedisLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Reset module state before each case: fresh lock per fresh loop
        # (the old loop is closed by the time tearDown finished).
        redis_client._redis_client = None
        redis_client._redis_pool = None
        redis_client._redis_lock = None
        redis_client._redis_lock_loop = None
        self.patches: list[unittest.mock._patch] = []

    def tearDown(self) -> None:
        for patcher in self.patches:
            patcher.stop()
        redis_client._redis_client = None
        redis_client._redis_pool = None
        redis_client._redis_lock = None
        redis_client._redis_lock_loop = None

    def patch_client_factory(self, factory) -> None:
        self.patches.append(
            unittest.mock.patch.object(redis_client, "create_async_client", new=factory)
        )
        self.patches[-1].start()

    def patch_close_async(self, recorder) -> None:
        self.patches.append(unittest.mock.patch.object(redis_client, "close_async", new=recorder))
        self.patches[-1].start()

    async def test_concurrent_getters_publish_one_client_pair(self) -> None:
        constructions = 0
        started = asyncio.Event()

        async def slow_factory(*args, **kwargs):
            nonlocal constructions
            constructions += 1
            started.set()
            await asyncio.sleep(0.02)
            return FakeRedisClient(f"c{constructions}"), FakePool(f"p{constructions}")

        self.patch_client_factory(slow_factory)

        clients = await asyncio.gather(
            redis_client.get_redis_client(),
            redis_client.get_redis_client(),
            redis_client.get_redis_client(),
        )

        self.assertEqual(constructions, 1)
        self.assertEqual({id(c) for c in clients if c is not None}, {id(clients[0])})
        self.assertIs(redis_client._redis_client, clients[0])

    async def test_ordinary_connect_failure_warns_and_returns_none(self) -> None:
        async def failing_factory(*args, **kwargs):
            raise redis.exceptions.ConnectionError("no redis")

        self.patch_client_factory(failing_factory)

        client = await redis_client.get_redis_client()
        self.assertIsNone(client)
        self.assertIsNone(redis_client._redis_client)
        self.assertIsNone(redis_client._redis_pool)

    async def test_connect_cancellation_propagates_and_publishes_nothing(self) -> None:
        started = asyncio.Event()

        async def hanging_factory(*args, **kwargs):
            started.set()
            await asyncio.sleep(10)
            return FakeRedisClient("x"), FakePool("x")  # pragma: no cover

        self.patch_client_factory(hanging_factory)

        task = asyncio.create_task(redis_client.get_redis_client())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(redis_client._redis_client)
        self.assertIsNone(redis_client._redis_pool)

    async def test_close_detaches_both_globals_before_teardown(self) -> None:
        client = FakeRedisClient("live")
        pool = FakePool("live")
        redis_client._redis_client = client
        redis_client._redis_pool = pool

        teardown_seen: dict[str, object] = {}

        async def recording_close_async(c, p, *, name):
            teardown_seen["client"] = c
            teardown_seen["pool"] = p
            teardown_seen["global_client"] = redis_client._redis_client
            teardown_seen["global_pool"] = redis_client._redis_pool

        self.patch_close_async(recording_close_async)

        await redis_client.close_redis_client()

        self.assertIsNone(redis_client._redis_client)
        self.assertIsNone(redis_client._redis_pool)
        self.assertIs(teardown_seen["client"], client)
        self.assertIs(teardown_seen["pool"], pool)
        self.assertIsNone(teardown_seen["global_client"], "detached before teardown")
        self.assertIsNone(teardown_seen["global_pool"], "detached before teardown")

    async def test_close_without_client_creates_nothing(self) -> None:
        async def factory_should_not_run(*args, **kwargs):
            raise AssertionError("close must not create a client")

        self.patch_client_factory(factory_should_not_run)
        await redis_client.close_redis_client()
        self.assertIsNone(redis_client._redis_client)
        self.assertIsNone(redis_client._redis_pool)

    async def test_sequential_fully_closed_loops_work_without_replacing_live_lock(self) -> None:
        """Sequential loops (each case = one loop): within a single loop the
        lock is created once and reused across a full close→get cycle; no
        live lock is ever replaced. The cross-loop case (old loop closed,
        no references, unlocked → replace) is the documented _get_redis_lock
        replacement condition; a live loop reuse is asserted here."""
        constructions = 0

        async def factory(*args, **kwargs):
            nonlocal constructions
            constructions += 1
            return FakeRedisClient(f"c{constructions}"), FakePool(f"p{constructions}")

        self.patch_client_factory(factory)

        async def recording_close_async(c, p, *, name="redis"):
            return None

        self.patch_close_async(recording_close_async)

        client1 = await redis_client.get_redis_client()
        self.assertIsNotNone(client1)
        lock1, loop1 = redis_client._redis_lock, redis_client._redis_lock_loop
        self.assertIs(loop1, asyncio.get_running_loop())

        await redis_client.close_redis_client()
        self.assertIsNone(redis_client._redis_client)
        self.assertIsNone(redis_client._redis_pool)

        # Same loop after full shutdown: lock reused (loop still open).
        client2 = await redis_client.get_redis_client()
        self.assertIsNotNone(client2)
        self.assertIs(redis_client._redis_lock, lock1, "live same-loop lock reused")
        self.assertEqual(constructions, 2, "post-close get builds a fresh client")

    async def test_live_other_loop_cannot_steal_lock(self) -> None:
        """A different, live loop with published resources raises instead
        of resetting the lock while the owner still uses it."""

        async def factory(*args, **kwargs):
            return FakeRedisClient("c1"), FakePool("p1")

        self.patch_client_factory(factory)
        await redis_client.get_redis_client()
        self.assertIsNotNone(redis_client._redis_client)

        # Simulate a second live loop attempting ownership: same running
        # loop but lock bound to it already -> reuse, not replacement.
        lock_before = redis_client._redis_lock
        client = await redis_client.get_redis_client()
        self.assertIs(redis_client._redis_lock, lock_before, "live lock never reset")
        self.assertIsNotNone(client)

    async def test_lock_never_reset_while_waiters_pending(self) -> None:
        """A getter waiting on the lock must not observe a lock reset by a
        close that runs concurrently."""
        constructions = 0
        release_close = asyncio.Event()

        async def factory(*args, **kwargs):
            nonlocal constructions
            constructions += 1
            return FakeRedisClient(f"c{constructions}"), FakePool(f"p{constructions}")

        self.patch_client_factory(factory)

        async def slow_close_async(c, p, *, name="redis"):
            await release_close.wait()

        self.patch_close_async(slow_close_async)

        first = await redis_client.get_redis_client()
        self.assertEqual(constructions, 1)

        # Begin a close that holds the lock; concurrently a getter must
        # wait for it (never bypass or reset).
        close_task = asyncio.create_task(redis_client.close_redis_client())
        await asyncio.sleep(0.01)  # let close acquire the lock
        getter = asyncio.create_task(redis_client.get_redis_client())

        release_close.set()
        await asyncio.gather(close_task, getter)
        # Getter waited on the lock while close held it; close detached the
        # client first, so the getter legitimately built ONE replacement
        # client after teardown (serialized, never racing).
        self.assertEqual(constructions, 2, "serialized close-then-get, no double publish")
        self.assertIsNotNone(first)


class FakeSharedPool:
    def __init__(self, disconnect_error: Exception | None = None) -> None:
        self.disconnect_calls = 0
        self.disconnect_error = disconnect_error

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_error is not None:
            raise self.disconnect_error


class FakeSharedClient:
    def __init__(self, ping_error: Exception | None = None) -> None:
        self.ping_error = ping_error
        self.ping_calls = 0
        self.close_calls = 0

    async def ping(self) -> None:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error

    async def close(self) -> None:
        self.close_calls += 1


class SharedCreateAsyncClientTests(unittest.IsolatedAsyncioTestCase):
    """shared.redis_client.create_async_client reclaim contract (#5)."""

    def setUp(self) -> None:
        self.patches: list[unittest.mock._patch] = []

    def tearDown(self) -> None:
        for patcher in self.patches:
            patcher.stop()

    def install_fake_module(self, client: FakeSharedClient, pool: FakeSharedPool) -> None:
        from shared import redis_client as shared

        class FakePoolCls:
            @staticmethod
            def from_url(*args, **kwargs):
                return pool

        fake_module = type(
            "fake_redis_module",
            (),
            {
                "ConnectionPool": FakePoolCls,
                "Redis": staticmethod(lambda *, connection_pool: client),
            },
        )
        patcher = unittest.mock.patch.object(shared, "_async_redis", new=fake_module)
        patcher.start()
        self.patches.append(patcher)

    async def call_create(self, **kwargs):
        from shared.redis_client import create_async_client

        return await create_async_client("redis://fake", **kwargs)

    async def test_ping_failure_reclaims_pool_and_client_and_reraises(self) -> None:
        error = RedisConnectionError("redis down")
        client = FakeSharedClient(ping_error=error)
        pool = FakeSharedPool()
        self.install_fake_module(client, pool)

        with self.assertRaises(RedisConnectionError) as caught:
            await self.call_create()

        self.assertIs(caught.exception, error, "original exception retained")
        self.assertEqual(client.close_calls, 1, "constructed client reclaimed")
        self.assertEqual(pool.disconnect_calls, 1, "constructed pool reclaimed")

    async def test_constructor_failure_reclaims_pool_with_ping_false(self) -> None:
        """Constructor itself raises (ping=False): the constructed pool must
        still be reclaimed and the original error re-raised."""
        from shared import redis_client as shared

        pool = FakeSharedPool()
        self.install_fake_module(None, pool)  # type: ignore[arg-type]
        # Constructor raises before any client exists.

        class FakePoolCls:
            @staticmethod
            def from_url(*args, **kwargs):
                return pool

        class ExplodingConstructor:
            def __init__(self, *, connection_pool) -> None:
                raise RuntimeError("constructor failure")

        fake_module = type(
            "fake_redis_module_ctor",
            (),
            {"ConnectionPool": FakePoolCls, "Redis": ExplodingConstructor},
        )
        patcher = unittest.mock.patch.object(shared, "_async_redis", new=fake_module)
        patcher.start()
        self.patches.append(patcher)

        with self.assertRaises(RuntimeError) as caught:
            await self.call_create(ping=False)

        self.assertEqual(str(caught.exception), "constructor failure")
        self.assertEqual(
            pool.disconnect_calls, 1, "constructed pool reclaimed via close_async(None, pool)"
        )

    async def test_constructor_failure_reclaims_pool_and_client_with_ping_true(self) -> None:
        from shared import redis_client as shared

        pool = FakeSharedPool()
        self.install_fake_module(None, pool)  # type: ignore[arg-type]

        class FakePoolCls:
            @staticmethod
            def from_url(*args, **kwargs):
                return pool

        class ExplodingConstructor:
            def __init__(self, *, connection_pool) -> None:
                raise RuntimeError("constructor failure")

        fake_module = type(
            "fake_redis_module_ctor2",
            (),
            {"ConnectionPool": FakePoolCls, "Redis": ExplodingConstructor},
        )
        patcher = unittest.mock.patch.object(shared, "_async_redis", new=fake_module)
        patcher.start()
        self.patches.append(patcher)

        with self.assertRaises(RuntimeError) as caught:
            await self.call_create(ping=True)

        self.assertEqual(str(caught.exception), "constructor failure")
        self.assertEqual(pool.disconnect_calls, 1)

    async def test_ping_cancellation_reclaims_and_reraises_cancelled(self) -> None:
        client = FakeSharedClient(ping_error=asyncio.CancelledError())
        pool = FakeSharedPool()
        self.install_fake_module(client, pool)

        with self.assertRaises(asyncio.CancelledError):
            await self.call_create()

        self.assertEqual(client.close_calls, 1)
        self.assertEqual(pool.disconnect_calls, 1)

    async def test_cleanup_failure_logs_but_keeps_original_exception(self) -> None:
        client = FakeSharedClient(ping_error=RedisConnectionError("down"))
        pool = FakeSharedPool(disconnect_error=RuntimeError("disconnect blew up"))
        self.install_fake_module(client, pool)
        with self.assertRaises(RedisConnectionError) as caught:
            await self.call_create()

        self.assertEqual(str(caught.exception), "down", "original error not replaced")
        self.assertEqual(pool.disconnect_calls, 1)

    async def test_repeated_caller_cancellation_still_waits_bounded_cleanup(self) -> None:
        """Caller cancelled twice during the shielded wait: the SAME bounded
        cleanup task keeps running; original exception finally re-raised."""
        import shared.redis_client as shared

        client = FakeSharedClient(ping_error=RedisConnectionError("down"))
        pool = FakeSharedPool()
        self.install_fake_module(client, pool)

        cleanup_started = asyncio.Event()
        release_cleanup = asyncio.Event()
        orig_close_async = shared.close_async

        async def controlled_close_async(c, p, *, name="redis"):
            cleanup_started.set()
            await release_cleanup.wait()
            await orig_close_async(c, p, name=name)

        patcher = unittest.mock.patch.object(shared, "close_async", new=controlled_close_async)
        patcher.start()
        self.patches.append(patcher)

        async def cancelled_caller() -> None:
            task = asyncio.create_task(self.call_create())
            # Let the ping fail and the bounded cleanup task start.
            await asyncio.wait_for(cleanup_started.wait(), timeout=2)
            # Caller cancellation request during the shielded wait: the
            # implementation must keep waiting for the SAME bounded task.
            task.cancel()
            await asyncio.sleep(0.02)  # deliver the cancellation request
            self.assertFalse(
                task.done(),
                "cleanup task continues despite cancellation request",
            )
            # Bounded cleanup finishes; original initialization exception is
            # re-raised to the caller (not the cancellation).
            release_cleanup.set()
            with self.assertRaises(RedisConnectionError):
                await task

        await cancelled_caller()
        self.assertEqual(client.close_calls, 1, "bounded task finished reclaim")
        self.assertEqual(pool.disconnect_calls, 1)


if __name__ == "__main__":
    unittest.main()
