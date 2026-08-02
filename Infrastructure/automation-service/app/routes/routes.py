"""Route registration and dependency injection setup."""

from __future__ import annotations

from fastapi import FastAPI

from app.repositories.monitoring_history import MonitoringHistoryRepository
from app.repositories.monitoring_snapshot import MonitoringSnapshotRepository
from app.routes import (
    alarms,
    calendar,
    climate_periods,
    debug,
    devices,
    devices_crud,
    failsafe,
    hardware,
    lights,
    mode,
    monitoring,
    notes,
    pid,
    redis_state,
    room_modes,
    schedules,
    status,
    system_config,
    websocket,
)
from shared.infra_logging import get_logger

logger = get_logger(__name__)


def register_routes(app: FastAPI) -> None:
    """Register all API routes with the FastAPI application.

    Args:
        app: FastAPI application instance
    """
    # API Routes
    app.include_router(schedules.router, tags=["schedules"])
    app.include_router(lights.router, tags=["lights"])
    app.include_router(climate_periods.router, tags=["climate-periods"])
    app.include_router(devices.router, tags=["devices"])
    app.include_router(devices_crud.router, tags=["devices"])
    app.include_router(hardware.router, tags=["hardware"])
    app.include_router(status.router, tags=["status"])
    app.include_router(notes.router, tags=["notes"])
    app.include_router(alarms.router, tags=["alarms"])
    app.include_router(pid.router, tags=["pid"])
    app.include_router(mode.router, tags=["mode"])
    app.include_router(failsafe.router, tags=["failsafe"])
    app.include_router(websocket.router, tags=["websocket"])
    app.include_router(room_modes.router, tags=["room-modes"])
    app.include_router(calendar.router, tags=["calendar"])
    app.include_router(redis_state.router, tags=["redis-state"])
    app.include_router(debug.router, tags=["debug"])
    app.include_router(system_config.router, tags=["system-config"])
    app.include_router(monitoring.router, tags=["monitoring"])
    # Health (with hardware.mcp) is served by status.router GET /health

    logger.info("All routes registered")


def setup_dependency_overrides(app: FastAPI, container) -> None:
    """Setup dependency injection overrides for route handlers.

    Args:
        app: FastAPI application instance
        container: ServiceContainer instance with initialized dependencies
    """
    # Override dependencies in climate_periods module
    app.dependency_overrides[climate_periods.get_database] = container.get_database

    # Override dependencies in schedules module
    app.dependency_overrides[schedules.get_database] = container.get_database
    app.dependency_overrides[schedules.get_scheduler] = container.get_scheduler
    app.dependency_overrides[schedules.get_config] = container.get_config

    # Override dependencies in lights module
    app.dependency_overrides[lights.get_database] = container.get_database
    app.dependency_overrides[lights.get_config] = container.get_config
    app.dependency_overrides[lights.get_dfr0971_manager] = container.get_dfr0971_manager
    app.dependency_overrides[lights.get_relay_manager] = container.get_relay_manager
    app.dependency_overrides[lights.get_interlock_manager] = container.get_interlock_manager
    app.dependency_overrides[lights.get_scheduler] = container.get_scheduler
    app.dependency_overrides[lights.get_device_repo] = lambda: container.get_database().device_repo

    # Override dependencies in devices module
    app.dependency_overrides[devices.get_database] = container.get_database
    app.dependency_overrides[devices.get_relay_manager] = container.get_relay_manager
    app.dependency_overrides[devices.get_device_command_service] = (
        container.get_device_command_service
    )
    app.dependency_overrides[devices.get_control_snapshot_service] = (
        container.get_control_snapshot_service
    )

    # Override dependencies in devices_crud module
    # Override dependencies in hardware module
    app.dependency_overrides[hardware.get_relay_manager] = container.get_relay_manager
    app.dependency_overrides[hardware.get_relay_board_state_manager] = (
        container.get_relay_board_state_manager
    )
    app.dependency_overrides[hardware.get_automation_redis] = container.get_automation_redis
    app.dependency_overrides[hardware.get_device_command_service] = (
        container.get_device_command_service
    )

    # Override dependencies in status module
    app.dependency_overrides[status.get_database] = container.get_database
    app.dependency_overrides[status.get_config] = container.get_config
    app.dependency_overrides[status.get_relay_manager] = container.get_relay_manager
    app.dependency_overrides[status.get_pid_controller_manager] = (
        container.get_pid_controller_manager
    )

    # Override dependencies in pid module
    app.dependency_overrides[pid.get_database] = container.get_database
    app.dependency_overrides[pid.get_config] = container.get_config

    # Override dependencies in mode module
    # mode.py uses direct imports from app.main, may need refactoring

    # Override dependencies in failsafe module
    app.dependency_overrides[failsafe.get_database] = container.get_database

    # Override dependencies in redis_state module
    app.dependency_overrides[redis_state.get_database] = container.get_database

    # Override dependencies in debug module
    app.dependency_overrides[debug.get_database] = container.get_database
    app.dependency_overrides[debug.get_scheduler] = container.get_scheduler
    app.dependency_overrides[debug.get_control_engine] = container.get_control_engine

    app.dependency_overrides[calendar.get_database] = container.get_database

    # Override dependencies in system_config module
    app.dependency_overrides[system_config.get_config] = container.get_config

    # Override monitoring's repository and pure-projection seams.
    app.dependency_overrides[monitoring.get_database] = container.get_database
    app.dependency_overrides[monitoring.get_automation_redis] = container.get_automation_redis
    app.dependency_overrides[monitoring.get_logger_health_provider] = (
        container.get_photoperiod_history_logger
    )
    app.dependency_overrides[monitoring.get_snapshot_repository] = (
        lambda: MonitoringSnapshotRepository(
            db_manager=container.get_database(), redis=container.get_automation_redis()
        )
    )
    app.dependency_overrides[monitoring.get_history_repository] = (
        lambda: MonitoringHistoryRepository(
            db_manager=container.get_database(),
            health_provider=container.get_photoperiod_history_logger(),
        )
    )
    app.dependency_overrides[monitoring.get_climate_projection] = monitoring.get_climate_projection
    app.dependency_overrides[monitoring.get_light_projection] = monitoring.get_light_projection

    logger.info("Dependency overrides configured")
