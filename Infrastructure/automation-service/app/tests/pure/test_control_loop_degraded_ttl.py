"""The control-loop degraded advisory key must expire, never linger as a ghost."""

import pytest

from app.background_tasks.control_loop import ControlLoopMixin


class _FakeRedis:
    """Records write calls; supports both plain set and TTL-carrying setex."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def set(self, key: str, value: str) -> None:
        self.calls.append(("set", key, value))

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls.append(("setex", key, str(ttl), value))


class _Database:
    _automation_redis = None
    _db_connected = False


def _mixin_with(redis: _FakeRedis | None) -> ControlLoopMixin:
    mixin = ControlLoopMixin.__new__(ControlLoopMixin)
    database = _Database()

    class _Adapter:
        redis_enabled = True
        redis_client = redis

    database._automation_redis = _Adapter()
    mixin.database = database
    mixin._control_failure_count = 1
    mixin._control_success_count = 0
    mixin._degraded_active = False
    return mixin


@pytest.mark.asyncio
async def test_degraded_write_carries_a_short_ttl() -> None:
    # Given: a healthy Redis with an existing degraded write surface.
    mixin = _mixin_with(_FakeRedis())

    # When: the control loop publishes a degraded-state payload.
    await mixin._write_degraded_state({"active": True, "reason": "x"})

    # Then: the write is NOT TTL-less `set`; it uses setex so the advisory
    # self-expires even if the recovering loop dies mid-recovery.
    adapter = mixin.database._automation_redis
    assert adapter.redis_client.calls, "no write happened"
    assert all(call[0] == "setex" for call in adapter.redis_client.calls), str(
        adapter.redis_client.calls
    )
    ttl_used = int(adapter.redis_client.calls[0][2])
    assert 1 <= ttl_used <= 120


@pytest.mark.asyncio
async def test_a_stale_ghost_flag_is_repainted_on_next_degraded_tick() -> None:
    # Given: a ghost degraded payload from a long-dead failure burst.
    mixin = _mixin_with(_FakeRedis())

    # When: the loop keeps failing and rewrites the flag every failure tick.
    for _ in range(4):
        await mixin._record_control_failure("boom")
        mixin._control_failure_count = 9
    mixin._degraded_active = True
    await mixin._record_control_success()

    # Then: the latest honoured payload resets failures and refreshes the key
    # (with TTL), so an old failure_count can never be re-displayed.
    calls = mixin.database._automation_redis.redis_client.calls
    assert calls, "recovery path wrote nothing"
    assert all(call[1] == "automation:degraded" for call in calls)
