"""TTL cache for config-derived device hierarchy and sensor mapping (control loop hot path)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import inspect
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class EngineConfigCache:
    """Caches `get_devices()` and `get_sensor_mapping()` results with a shared TTL timestamp."""

    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._device_hierarchy_cache: dict[str, dict[str, dict[str, dict[str, Any]]]] | None = None
        self._sensor_mapping_cache: dict[str, Any] | None = None
        self._cache_timestamp: datetime | None = None
        self._cache_ttl = timedelta(seconds=ttl_seconds)

    async def get_device_hierarchy(
        self,
        get_devices: Callable[[], Any],
    ) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """Return cached device tree or refresh if TTL expired.

        Supports both sync and async callables for ``get_devices``.
        """
        now = datetime.now()
        if (
            self._device_hierarchy_cache is None
            or self._cache_timestamp is None
            or now - self._cache_timestamp > self._cache_ttl
        ):
            result = get_devices()
            if inspect.isawaitable(result):
                result = await result
            self._device_hierarchy_cache = result
            self._cache_timestamp = now
            logger.debug("Refreshed device hierarchy cache")
        assert self._device_hierarchy_cache is not None
        return self._device_hierarchy_cache

    def get_sensor_mapping(
        self, get_sensor_mapping: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        """Return cached sensor mapping or refresh if TTL expired (same timestamp as hierarchy)."""
        now = datetime.now()
        if (
            self._sensor_mapping_cache is None
            or self._cache_timestamp is None
            or now - self._cache_timestamp > self._cache_ttl
        ):
            self._sensor_mapping_cache = get_sensor_mapping()
            if self._cache_timestamp is None:
                self._cache_timestamp = now
            logger.debug("Refreshed sensor mapping cache")
        return self._sensor_mapping_cache
