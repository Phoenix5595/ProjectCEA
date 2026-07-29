"""Atomic runtime device-registry loader."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from shared.infra_logging import get_logger

from .runtime_device_snapshot import RuntimeDeviceSnapshot

if TYPE_CHECKING:
    from ..database import DatabaseManager

logger = get_logger(__name__)

SnapshotConsumer = Callable[[RuntimeDeviceSnapshot], None]


class RuntimeDeviceRegistry:
    """Loads complete device projections and atomically publishes one reference.

    Registry writes call :meth:`reload_after_commit` after their transaction
    commits. Control ticks read the installed reference without querying the DB.
    """

    def __init__(self, database: DatabaseManager) -> None:
        self._database = database
        self._snapshot: RuntimeDeviceSnapshot | None = None
        self._consumers: list[SnapshotConsumer] = []
        self._next_version = 1
        self._reload_lock = asyncio.Lock()

    @property
    def snapshot(self) -> RuntimeDeviceSnapshot:
        """Return the fully installed snapshot or fail closed before startup load."""
        if self._snapshot is None:
            raise RuntimeError("Runtime device snapshot is not installed")
        return self._snapshot

    def subscribe(self, consumer: SnapshotConsumer) -> None:
        """Register a synchronous projection consumer and install the current snapshot."""
        self._consumers.append(consumer)
        if self._snapshot is not None:
            consumer(self._snapshot)

    async def load_startup(self) -> RuntimeDeviceSnapshot:
        """Load the initial complete projection."""
        return await self._reload("startup")

    async def reload_after_commit(self) -> RuntimeDeviceSnapshot:
        """Install the complete projection that follows a committed registry mutation."""
        return await self._reload("committed mutation")

    async def _reload(self, reason: str) -> RuntimeDeviceSnapshot:
        """Build every projection first, then replace exactly one snapshot reference."""
        async with self._reload_lock:
            try:
                snapshot = await self._build_snapshot()
            except Exception:
                logger.exception("Runtime device snapshot reload failed during %s", reason)
                raise

            for consumer in self._consumers:
                consumer(snapshot)
            self._snapshot = snapshot
            self._next_version += 1
            logger.info(
                "Installed runtime device snapshot version=%s (%s)", snapshot.version, reason
            )
            return snapshot

    async def _build_snapshot(self) -> RuntimeDeviceSnapshot:
        """Fetch all control projections without publishing a partial result."""
        hierarchy = await self._database.device_repo.get_all_as_hierarchy()
        mode_parameters = await self._load_active_mode_parameters(hierarchy)
        light_intensities = await self._database.light_target_intensity_repo.get_all_intensities()
        light_programs = await self._database.light_programs_repo.get_all_programs()
        return RuntimeDeviceSnapshot.create(
            version=self._next_version,
            hierarchy=hierarchy,
            mode_parameters=mode_parameters,
            light_intensities=light_intensities,
            light_programs=light_programs,
        )

    async def _load_active_mode_parameters(
        self, hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Load active photoperiod data for every room represented in the registry."""
        projection: dict[tuple[str, str], dict[str, Any]] = {}
        for location, clusters in hierarchy.items():
            for cluster in clusters:
                active_mode = await self._database.room_mode_repo.get_active_mode(location, cluster)
                if active_mode is None:
                    continue
                mode_name = active_mode.get("mode_name")
                submode_name = active_mode.get("submode_name")
                if not isinstance(mode_name, str):
                    continue
                parameters = await self._database.room_mode_repo.get_mode_parameters(
                    location, cluster, mode_name, submode_name
                )
                if parameters is None:
                    continue
                projection[(location, cluster)] = {
                    "mode_id": parameters.get("mode_id"),
                    "day_start": parameters.get("day_start_time", "06:00"),
                    "night_start": parameters.get("night_start_time", "18:00"),
                    "ramp_up": parameters.get("light_ramp_up_minutes", 0),
                    "ramp_down": parameters.get("light_ramp_down_minutes", 0),
                }
        return projection
