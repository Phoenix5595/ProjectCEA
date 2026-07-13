"""Light dimming control endpoints for DFR0971 DAC modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from app.automation.interlock_manager import InterlockManager
    from app.config import ConfigLoader
    from app.control.relay_manager import RelayManager
    from app.control.scheduler import Scheduler
    from app.database import DatabaseManager
    from app.hardware.dfr0971 import DFR0971Manager
    from app.repositories.devices import DeviceRepository

router = APIRouter()

logger = get_logger(__name__)


# These will be overridden by main app
def get_dfr0971_manager() -> DFR0971Manager:
    """Dependency to get DFR0971 manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_interlock_manager() -> InterlockManager:
    """Dependency to get interlock manager."""
    raise RuntimeError("Dependency not injected")


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_scheduler() -> Scheduler:
    """Dependency to get scheduler."""
    raise RuntimeError("Dependency not injected")


def get_device_repo() -> DeviceRepository:
    """Dependency to get device repository."""
    from app.main import container

    return container.get_database().device_repo


from app.routes.lights import (  # noqa: E402
    dfr_assignments,  # noqa: F401
    light_control,  # noqa: F401
    light_crud,  # noqa: F401
    light_status,  # noqa: F401
    light_target,  # noqa: F401
    light_test,  # noqa: F401
)

__all__ = [
    "router",
    "get_dfr0971_manager",
    "get_config",
    "get_relay_manager",
    "get_interlock_manager",
    "get_database",
    "get_scheduler",
    "get_device_repo",
]
