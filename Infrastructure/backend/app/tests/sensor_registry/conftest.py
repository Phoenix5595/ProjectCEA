"""Test conftest for the sensor registry facade (pure fakes, no database)."""

from __future__ import annotations

import pytest

from app.repositories.sensor_registry_repository import SensorRegistryRepository
from app.tests.sensor_registry.fakes import FakeConnection, FakePool


@pytest.fixture(autouse=True)
def postgres_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route tests resolve repositories lazily; construction must not need
    production credentials."""
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-password")
    monkeypatch.setenv("POSTGRES_DB", "cea_sensors")
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)


@pytest.fixture
def fake_connection() -> FakeConnection:
    return FakeConnection()


@pytest.fixture
def fake_pool(fake_connection: FakeConnection) -> FakePool:
    return FakePool(fake_connection)


@pytest.fixture
def repository(fake_pool: FakePool) -> SensorRegistryRepository:
    return SensorRegistryRepository(pool=fake_pool)  # type: ignore[arg-type]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
