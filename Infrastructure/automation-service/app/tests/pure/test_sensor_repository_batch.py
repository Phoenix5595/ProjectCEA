from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast

from asyncpg import Pool
import pytest

from app.control.sensor_reader import SensorReader
from app.repositories.sensors import SensorRepository


class FakeConnection:
    values: Mapping[str, float | None]
    batch_error: Exception | None
    row_errors: set[str]

    def __init__(self, values: Mapping[str, float | None]) -> None:
        self.values = values
        self.batch_error = None
        self.row_errors = set()

    async def fetch(self, _query: str, sensor_names: list[str]) -> list[dict[str, object]]:
        if self.batch_error is not None:
            raise self.batch_error
        return [
            {"sensor_name": name, "value": self.values.get(name)}
            for name in dict.fromkeys(sensor_names)
        ]

    async def fetchrow(self, _query: str, sensor_name: str) -> dict[str, object] | None:
        if sensor_name in self.row_errors:
            raise RuntimeError(f"failed to read {sensor_name}")
        value = self.values.get(sensor_name)
        return None if value is None else {"value": value}


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


@pytest.mark.asyncio
async def test_batch_preserves_latest_values_missing_and_duplicate_name_results() -> None:
    repository, _connection = _repository(
        {"dry_bulb_b": 24.5, "rh_b": 65.0, "co2_b": None, "duplicate_name": None}
    )

    result = await repository.get_sensor_values_batch(
        ["dry_bulb_b", "rh_b", "co2_b", "missing", "duplicate_name"]
    )

    assert result == {
        "dry_bulb_b": 24.5,
        "rh_b": 65.0,
        "co2_b": None,
        "missing": None,
        "duplicate_name": None,
    }


@pytest.mark.asyncio
async def test_batch_failure_falls_back_per_name_and_keeps_partial_results() -> None:
    repository, connection = _repository({"dry_bulb_b": 24.5, "rh_b": 62.0})
    connection.batch_error = RuntimeError("batch query failed")
    connection.row_errors.add("broken_sensor")

    result = await repository.get_sensor_values_batch(
        ["dry_bulb_b", "missing_sensor", "broken_sensor"]
    )

    assert result == {
        "dry_bulb_b": 24.5,
        "missing_sensor": None,
        "broken_sensor": None,
    }


@pytest.mark.asyncio
async def test_empty_batch_returns_without_database_values() -> None:
    repository, _connection = _repository({})

    assert await repository.get_sensor_values_batch([]) == {}


@pytest.mark.asyncio
async def test_sensor_reader_keeps_flower_and_veg_sensor_name_inputs() -> None:
    values = {
        "dry_bulb_b": 24.5,
        "rh_b": 65.0,
        "co2_b": 700.0,
        "vpd_b": 1.1,
        "dry_bulb_v": 22.5,
        "rh_v": 68.0,
        "co2_v": 900.0,
        "vpd_v": 1.0,
    }
    repository, _connection = _repository(values)
    reader = SensorReader(SimpleNamespace(sensor_repo=repository), None)
    sensor_mapping = {
        "Flower Room": {
            "main": {
                "temperature_sensor": "dry_bulb_b",
                "humidity_sensor": "rh_b",
                "co2_sensor": "co2_b",
                "vpd_sensor": "vpd_b",
            }
        },
        "Veg Room": {
            "main": {
                "temperature_sensor": "dry_bulb_v",
                "humidity_sensor": "rh_v",
                "co2_sensor": "co2_v",
                "vpd_sensor": "vpd_v",
            }
        },
    }

    flower = await reader.read_sensors("Flower Room", "main", sensor_mapping)
    veg = await reader.read_sensors("Veg Room", "main", sensor_mapping)

    assert flower == {name: values[name] for name in sensor_mapping["Flower Room"]["main"].values()}
    assert veg == {name: values[name] for name in sensor_mapping["Veg Room"]["main"].values()}


@pytest.mark.asyncio
async def test_status_keeps_sensor_type_response_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routes import status

    repository, _connection = _repository({"dry_bulb_b": 24.5, "rh_b": 65.0, "dry_bulb_v": 22.5})
    sensor_mapping = {
        "Flower Room": {"main": {"temperature_sensor": "dry_bulb_b", "humidity_sensor": "rh_b"}},
        "Veg Room": {"main": {"temperature_sensor": "dry_bulb_v"}},
    }

    class Config:
        async def get_devices(self) -> dict[str, dict[str, list[str]]]:
            return {}

        def get_sensor_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
            return sensor_mapping

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
