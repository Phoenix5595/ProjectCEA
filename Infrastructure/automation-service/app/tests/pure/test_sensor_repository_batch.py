from __future__ import annotations

import asyncio
from collections.abc import Mapping
import time
from types import SimpleNamespace
from typing import cast

from asyncpg import Pool
import pytest

import app.control.sensor_reader as sensor_reader_module
from app.control.sensor_reader import SensorReader
from app.repositories.sensors import SensorRepository
from shared.redis_keys import sensor_full, sensor_full_ts


class FakeConnection:
    values: Mapping[str, float | None]
    batch_error: Exception | None
    fetch_args: tuple[str, list[str], float] | None

    def __init__(self, values: Mapping[str, float | None]) -> None:
        self.values = values
        self.batch_error = None
        self.fetch_args = None

    async def fetch(
        self, query: str, sensor_names: list[str], max_age_sec: float
    ) -> list[dict[str, object]]:
        self.fetch_args = (query, sensor_names, max_age_sec)
        if self.batch_error is not None:
            raise self.batch_error
        return [
            {"sensor_name": name, "value": self.values.get(name)}
            for name in dict.fromkeys(sensor_names)
        ]


class FakeAcquire:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection: FakeConnection = connection

    async def __aenter__(self) -> FakeConnection:
        return self.connection

    async def __aexit__(self, *_exc: object) -> None:
        return None


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection: FakeConnection = connection

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.connection)


def _repository(
    values: Mapping[str, float | None],
) -> tuple[SensorRepository, FakeConnection]:
    connection = FakeConnection(values)
    return SensorRepository(cast(Pool, FakePool(connection))), connection


def _config(hold_period: int = 30) -> SimpleNamespace:
    return SimpleNamespace(get_last_good_hold_period=lambda: hold_period)


@pytest.mark.asyncio
async def test_batch_preserves_latest_values_missing_and_duplicate_name_results() -> None:
    repository, connection = _repository(
        {"dry_bulb_b": 24.5, "rh_b": 65.0, "co2_b": None, "duplicate_name": None}
    )

    result = await repository.get_sensor_values_batch(
        ["dry_bulb_b", "rh_b", "co2_b", "missing", "duplicate_name"],
        max_age_sec=30,
    )

    assert result == {
        "dry_bulb_b": 24.5,
        "rh_b": 65.0,
        "co2_b": None,
        "missing": None,
        "duplicate_name": None,
    }
    assert connection.fetch_args is not None
    query, names, max_age_sec = connection.fetch_args
    assert "m.time > now() - make_interval(secs => $2)" in query
    assert names == ["dry_bulb_b", "rh_b", "co2_b", "missing", "duplicate_name"]
    assert max_age_sec == 30.0


@pytest.mark.asyncio
async def test_batch_failure_propagates() -> None:
    repository, connection = _repository({"dry_bulb_b": 24.5})
    connection.batch_error = RuntimeError("batch query failed")

    with pytest.raises(RuntimeError, match="batch query failed"):
        await repository.get_sensor_values_batch(["dry_bulb_b"], max_age_sec=30)


@pytest.mark.asyncio
async def test_empty_batch_returns_without_database_values() -> None:
    repository, connection = _repository({})

    assert await repository.get_sensor_values_batch([], max_age_sec=30) == {}
    assert connection.fetch_args is None


@pytest.mark.asyncio
async def test_sensor_reader_batches_flower_and_veg_sensor_names_once() -> None:
    values = {"dry_bulb_b": 24.5, "rh_b": 65.0, "dry_bulb_v": 22.0}
    repository, connection = _repository(values)
    reader = SensorReader(SimpleNamespace(sensor_repo=repository), None, _config())
    sensor_mapping = {
        "Flower Room": {
            "main": {
                "temperature_sensor": "dry_bulb_b",
                "humidity_sensor": "rh_b",
            }
        },
        "Veg Room": {"main": {"temperature_sensor": "dry_bulb_v"}},
    }
    rooms = [("Flower Room", "main"), ("Veg Room", "main")]

    room_values = await reader.read_sensors_for_rooms(rooms, sensor_mapping)

    assert room_values == {
        ("Flower Room", "main"): {"dry_bulb_b": 24.5, "rh_b": 65.0},
        ("Veg Room", "main"): {"dry_bulb_v": 22.0},
    }
    assert connection.fetch_args is not None
    _, names, max_age_sec = connection.fetch_args
    assert names == ["dry_bulb_b", "rh_b", "dry_bulb_v"]
    assert max_age_sec == 30.0


@pytest.mark.asyncio
async def test_status_keeps_sensor_type_response_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routes import status

    repository, connection = _repository({"dry_bulb_b": 24.5, "rh_b": 65.0, "dry_bulb_v": 22.5})
    sensor_mapping = {
        "Flower Room": {"main": {"temperature_sensor": "dry_bulb_b", "humidity_sensor": "rh_b"}},
        "Veg Room": {"main": {"temperature_sensor": "dry_bulb_v"}},
    }

    class Config:
        async def get_devices(self) -> dict[str, dict[str, list[str]]]:
            return {}

        def get_sensor_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
            return sensor_mapping

        def get_last_good_hold_period(self) -> int:
            return 30

    monkeypatch.setattr(status, "get_performance_metrics", lambda: {})
    monkeypatch.setattr(
        status,
        "get_performance_monitor",
        lambda: SimpleNamespace(get_statistics=lambda: {}),
    )
    monkeypatch.setattr(status, "_get_system_stats", lambda: {})

    result = await status.get_status(
        database=SimpleNamespace(sensor_repo=repository),
        relay_manager=SimpleNamespace(get_all_states=lambda: {}),
        config=Config(),
        pid_controller_manager=None,
        publication_workers=None,
        operational_event_dispatcher=None,
        health=False,
    )

    assert result["sensors"] == {
        "Flower Room": {"main": {"temperature_sensor": 24.5, "humidity_sensor": 65.0}},
        "Veg Room": {"main": {"temperature_sensor": 22.5}},
    }

    connection.batch_error = RuntimeError("database unavailable")
    unavailable = await status.get_status(
        database=SimpleNamespace(sensor_repo=repository),
        relay_manager=SimpleNamespace(get_all_states=lambda: {}),
        config=Config(),
        pid_controller_manager=None,
        publication_workers=None,
        operational_event_dispatcher=None,
        health=False,
    )
    assert unavailable["sensors"] == {
        "Flower Room": {"main": {"temperature_sensor": None, "humidity_sensor": None}},
        "Veg Room": {"main": {"temperature_sensor": None}},
    }


@pytest.mark.asyncio
async def test_status_collects_host_stats_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import status

    repository, _ = _repository({})

    class Config:
        async def get_devices(self) -> dict[str, dict[str, list[str]]]:
            return {}

        def get_sensor_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
            return {}

        def get_last_good_hold_period(self) -> int:
            return 30

    def slow_system_stats() -> dict[str, float]:
        time.sleep(0.2)
        return {"cpu_percent": 1.0}

    monkeypatch.setattr(status, "get_performance_metrics", lambda: {})
    monkeypatch.setattr(
        status,
        "get_performance_monitor",
        lambda: SimpleNamespace(get_statistics=lambda: {}),
    )
    monkeypatch.setattr(status, "_get_system_stats", slow_system_stats)

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        result = await status.get_status(
            database=SimpleNamespace(sensor_repo=repository),
            relay_manager=SimpleNamespace(get_all_states=lambda: {}),
            config=Config(),
            pid_controller_manager=None,
            publication_workers=None,
            operational_event_dispatcher=None,
            health=False,
        )
    finally:
        ticker_task.cancel()

    assert result["system"] == {"cpu_percent": 1.0}
    assert ticks >= 5


@pytest.mark.asyncio
async def test_sensor_reader_uses_fresh_flower_back_redis_value_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(sensor_reader_module.time, "time", lambda: now)

    class FailingRepository:
        async def get_sensor_values_batch(
            self, _sensor_names: list[str], *, max_age_sec: float
        ) -> dict[str, float | None]:
            raise RuntimeError(f"database unavailable ({max_age_sec})")

    class FakeRedis:
        def __init__(self, values: dict[str, bytes]) -> None:
            self.values = values
            self.calls: list[str] = []

        def get(self, key: str) -> bytes | None:
            self.calls.append(key)
            return self.values.get(key)

    sensor_name = "dry_bulb_b"
    redis = FakeRedis(
        {
            sensor_full("Flower Room", "back", sensor_name): b"24.5",
            sensor_full_ts("Flower Room", "back", sensor_name): str(now * 1000 - 5_000).encode(),
        }
    )
    database = SimpleNamespace(
        sensor_repo=FailingRepository(),
        _automation_redis=SimpleNamespace(redis_client=redis),
    )
    reader = SensorReader(database, None, _config())
    mapping = {"Flower Room": {"main": {"temperature_sensor": sensor_name}}}

    result = await reader.read_sensors("Flower Room", "main", mapping)

    assert result == {sensor_name: 24.5}
    assert sensor_full("Flower Room", "back", sensor_name) in redis.calls


@pytest.mark.asyncio
async def test_sensor_reader_rejects_redis_value_older_than_hold_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_700_000_000.0
    monkeypatch.setattr(sensor_reader_module.time, "time", lambda: now)

    class FailingRepository:
        async def get_sensor_values_batch(
            self, _sensor_names: list[str], *, max_age_sec: float
        ) -> dict[str, float | None]:
            raise RuntimeError("database unavailable")

    sensor_name = "dry_bulb_b"
    redis_values = {
        sensor_full("Flower Room", "back", sensor_name): b"24.5",
        sensor_full_ts("Flower Room", "back", sensor_name): str(now * 1000 - 31_000).encode(),
    }
    redis = SimpleNamespace(get=redis_values.get)
    database = SimpleNamespace(
        sensor_repo=FailingRepository(),
        _automation_redis=SimpleNamespace(redis_client=redis),
    )
    reader = SensorReader(database, None, _config())
    mapping = {"Flower Room": {"main": {"temperature_sensor": sensor_name}}}

    assert await reader.read_sensors("Flower Room", "main", mapping) == {sensor_name: None}


@pytest.mark.asyncio
async def test_sensor_reader_does_not_use_redis_when_database_read_succeeds() -> None:
    repository, connection = _repository({"dry_bulb_b": 23.5})

    class FakeRedis:
        def __init__(self) -> None:
            self.calls = 0

        def get(self, _key: str) -> None:
            self.calls += 1
            return None

    redis = FakeRedis()
    database = SimpleNamespace(
        sensor_repo=repository,
        _automation_redis=SimpleNamespace(redis_client=redis),
    )
    reader = SensorReader(database, None, _config())
    mapping = {"Flower Room": {"main": {"temperature_sensor": "dry_bulb_b"}}}

    assert await reader.read_sensors("Flower Room", "main", mapping) == {"dry_bulb_b": 23.5}
    assert connection.fetch_args is not None
    assert connection.fetch_args[2] == 30.0
    assert redis.calls == 0
