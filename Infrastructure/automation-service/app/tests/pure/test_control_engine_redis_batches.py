from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.control.control_engine import ControlEngine
from app.redis.schema import relay_raw_override_key


class _FakeRedis:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.mget_calls: list[list[str]] = []
        self.deleted: list[str] = []

    def mget(self, keys: list[str]) -> list[str | None]:
        self.mget_calls.append(keys)
        return [self.values.get(key) for key in keys]

    def delete(self, key: str) -> int:
        self.deleted.append(key)
        return 1


class _FakeRelayManager:
    def __init__(self) -> None:
        self.states: list[tuple[int, int]] = []

    async def set_channel_state(self, channel: int, state: int) -> None:
        self.states.append((channel, state))


@pytest.mark.asyncio
async def test_raw_override_sweep_uses_one_mget_and_expires_only_elapsed_channels() -> None:
    # Given: channel 3 has expired and channel 7 remains active.
    now = datetime.now(UTC)
    expired = (now - timedelta(seconds=1)).isoformat()
    future = (now + timedelta(seconds=60)).isoformat()
    values = {
        relay_raw_override_key(3): f'{{"expires_at":"{expired}"}}',
        relay_raw_override_key(7): f'{{"expires_at":"{future}"}}',
    }
    redis = _FakeRedis(values)
    relay = _FakeRelayManager()
    engine = ControlEngine.__new__(ControlEngine)
    engine.database = SimpleNamespace(
        _automation_redis=SimpleNamespace(redis_client=redis)
    )
    engine.relay_manager = relay

    # When: the control engine sweeps raw channel overrides.
    await engine._expire_raw_channel_overrides()

    # Then: all 16 keys are fetched once, and only channel 3 is switched off/deleted.
    keys = [relay_raw_override_key(channel) for channel in range(16)]
    assert redis.mget_calls == [keys]
    assert relay.states == [(3, 0)]
    assert redis.deleted == [relay_raw_override_key(3)]
