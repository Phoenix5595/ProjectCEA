"""State synchronization task — loads scheduler data from database."""

from __future__ import annotations

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class StateSyncMixin:
    """Mixin for loading scheduler data from database into in-memory caches."""

    async def _load_scheduler_data(self) -> None:
        """Install one complete runtime snapshot; never rebuild scheduler maps piecemeal."""
        registry = self.control_engine.runtime_device_registry
        if registry is None:
            raise RuntimeError("Runtime device registry is not configured")
        await registry.load_startup()
        self._scheduler_loaded_once = True
        logger.info("Scheduler data installed from the runtime device snapshot")
