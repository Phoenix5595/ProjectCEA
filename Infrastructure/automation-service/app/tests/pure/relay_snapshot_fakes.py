from __future__ import annotations

from collections import deque


class Snapshot:
    def __init__(self) -> None:
        self.device_info = {("Veg Room", "main", "heater"): {"channel": 2}}
        self.by_channel = {2: ("Veg Room", "main", "heater")}
        self.by_device = {("Veg Room", "main", "heater"): 1}


class Registry:
    def __init__(self) -> None:
        self.snapshot = Snapshot()

    def subscribe(self, _callback) -> None:
        return None


class Interlocks:
    def check_interlock(self, *_args, **_kwargs) -> tuple[bool, str | None]:
        return True, None


class Mcp:
    def __init__(self, write_results: list[bool] | None = None) -> None:
        self.write_results = deque(write_results or [])
        self.writes: list[tuple[int, bool]] = []

    def set_channel(self, channel: int, state: bool) -> bool:
        self.writes.append((channel, state))
        return self.write_results.popleft()


class SamplingMcp:
    def __init__(self, samples: list[tuple[bool, ...] | None]) -> None:
        self.samples = deque(samples)
        self.sample_calls = 0

    def sample_all_channels(self) -> tuple[bool, ...] | None:
        self.sample_calls += 1
        return self.samples.popleft()


class Redis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str]] = []

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value
        self.set_calls.append((key, value))


class BoardState:
    def __init__(self) -> None:
        self.write_samples = 0

    async def on_write_done(self) -> bool:
        self.write_samples += 1
        return True


class ControlActions:
    def __init__(self) -> None:
        self.failures: list[tuple[str, str, str, int, int, str, int]] = []

    async def record_failed_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        prior_state: int,
        mode: str,
        requested_state: int,
    ) -> bool:
        self.failures.append(
            (location, cluster, device_name, channel, prior_state, mode, requested_state)
        )
        return True


class SnapshotBinding:
    def __init__(self) -> None:
        self.snapshot = object()
        self.released: list[object] = []

    def bind_snapshot(self, _snapshot: object) -> object:
        return object()

    def release_snapshot(self, token: object) -> None:
        self.released.append(token)


class TickBoardState:
    def __init__(self) -> None:
        self.samples = 0

    async def sample(self) -> bool:
        self.samples += 1
        return True


class ApiRedis:
    redis_client = None


class ApiDeviceRepository:
    async def get_all_device_states(self) -> list[dict[str, str]]:
        return []


class ApiDatabase:
    device_repo = ApiDeviceRepository()
