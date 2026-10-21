from __future__ import annotations

from typing import Any

from app.redis.setpoints import SetpointsMixin


class _FakePipeline:
    def __init__(self) -> None:
        self.writes: list[tuple[str, int, str]] = []

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.writes.append((key, ttl, value))

    def execute(self) -> None:
        return None


class _FakeRedis:
    def __init__(self) -> None:
        self.pipe = _FakePipeline()
        self.xadd_calls = 0

    def pipeline(self) -> _FakePipeline:
        return self.pipe

    def xadd(self, *_args: Any, **_kwargs: Any) -> None:
        self.xadd_calls += 1


def test_effective_setpoints_write_state_keys_without_stream_publication() -> None:
    # Given: a Redis setpoints mixin connected to a pipeline and stream spy.
    client = _FakeRedis()
    setpoints = SetpointsMixin()
    setpoints.redis_enabled = True
    setpoints.redis_client = client  # type: ignore[assignment]

    # When: climate and per-light effective setpoints are published.
    written = setpoints.write_effective_setpoints(
        "Flower Room",
        "main",
        effective_heating_setpoint=24.0,
        effective_cooling_setpoint=20.0,
        device_name="grow_light_1",
        effective_light_intensity=50.0,
    )

    # Then: the canonical Redis state keys retain their values and no XADD occurs.
    assert written is True
    assert client.pipe.writes == [
        ("cea:effective_setpoint:Flower Room:main:heating_setpoint", 300, "24.0"),
        ("cea:effective_setpoint:Flower Room:main:cooling_setpoint", 300, "20.0"),
        (
            "cea:effective_setpoint:Flower Room:main:light:grow_light_1:effective_intensity",
            300,
            "50.0",
        ),
    ]
    assert client.xadd_calls == 0
