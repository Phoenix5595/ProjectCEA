"""Shared dependencies for FastAPI routes."""

from __future__ import annotations

from app.database import DatabaseManager
from app.repositories.sensor_registry_repository import SensorRegistryRepository
from app.repositories.sensor_repository import SensorRepository

_db_manager: DatabaseManager | None = None
_sensor_repository: SensorRepository | None = None
_sensor_registry_repository: SensorRegistryRepository | None = None


def get_db_manager() -> DatabaseManager:
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_sensor_repository() -> SensorRepository:
    global _sensor_repository
    if _sensor_repository is None:
        _sensor_repository = SensorRepository(db_manager=get_db_manager())
    return _sensor_repository


def get_sensor_registry_repository() -> SensorRegistryRepository:
    """DI seam for the sensor-registry router (override-friendly for tests)."""
    global _sensor_registry_repository
    if _sensor_registry_repository is None:
        _sensor_registry_repository = SensorRegistryRepository(db_manager=get_db_manager())
    return _sensor_registry_repository


async def close_database_resources() -> None:
    """Lifespan shutdown adapter: close the existing DB manager if any.

    Closes only a manager that was already created (no getter-based shutdown
    initialization) and clears the cached manager and repository globals in
    ``finally`` so a later ``get_db_manager()`` on a fresh loop constructs a
    new manager instead of reusing the closed one.
    """
    global _db_manager, _sensor_repository, _sensor_registry_repository
    try:
        manager = _db_manager
        if manager is not None:
            await manager.close()
    finally:
        _db_manager = None
        _sensor_repository = None
        _sensor_registry_repository = None
