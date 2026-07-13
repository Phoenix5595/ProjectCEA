"""State synchronization task — loads scheduler data from database."""

from __future__ import annotations

from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class StateSyncMixin:
    """Mixin for loading scheduler data from database into in-memory caches."""

    async def _load_scheduler_data(self) -> None:
        """Load all 4 data sources into the Scheduler (atomic reference swaps).

        Called at startup and by event handlers.  Builds:
        - mode_parameters   : filtered to active mode per room/cluster
        - light_intensities : all rows from light_target_intensity
        - light_programs    : all rows from light_programs
        - device_lookup     : flattened from config.get_devices()
        """
        scheduler = self.control_engine.scheduler
        if scheduler is None:
            logger.warning("No scheduler attached to control engine, skipping data load")
            return

        # 1. mode_parameters — filtered to active mode per room/cluster
        mode_params: dict[tuple[str, str], dict[str, Any]] = {}
        try:
            devices = await self.control_engine.config.get_devices()
            for location, clusters in devices.items():
                for cluster in clusters:
                    active_mode = await self.database.room_mode_repo.get_active_mode(
                        location, cluster
                    )
                    if active_mode is None:
                        continue
                    mode_name = active_mode.get("mode_name")
                    submode_name = active_mode.get("submode_name")
                    if not mode_name:
                        continue
                    params = await self.database.room_mode_repo.get_mode_parameters(
                        location, cluster, mode_name, submode_name
                    )
                    if params:
                        mode_params[(location, cluster)] = {
                            "mode_id": params.get("mode_id"),
                            "day_start": params.get("day_start_time", "06:00"),
                            "night_start": params.get("night_start_time", "18:00"),
                            "ramp_up": params.get("light_ramp_up_minutes", 0),
                            "ramp_down": params.get("light_ramp_down_minutes", 0),
                        }
            scheduler.update_mode_parameters(mode_params)
        except Exception as e:
            logger.error(f"Failed to load mode_parameters into scheduler: {e}", exc_info=True)

        # 2. light_intensities — all rows
        try:
            intensities = await self.database.light_target_intensity_repo.get_all_intensities()
            scheduler.update_light_intensities(intensities)
        except Exception as e:
            logger.error(f"Failed to load light_intensities into scheduler: {e}", exc_info=True)

        # 3. light_programs — all rows
        try:
            programs = await self.database.light_programs_repo.get_all_programs()
            scheduler.update_light_programs(programs)
        except Exception as e:
            logger.error(f"Failed to load light_programs into scheduler: {e}", exc_info=True)

        # 4. device_lookup — flatten config.get_devices() hierarchy
        try:
            devices = await self.control_engine.config.get_devices()
            device_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
            for location, clusters in devices.items():
                for cluster, devs in clusters.items():
                    for device_name, info in devs.items():
                        device_lookup[(location, cluster, device_name)] = dict(info)
            scheduler.update_device_lookup(device_lookup)
        except Exception as e:
            logger.error(f"Failed to load device_lookup into scheduler: {e}", exc_info=True)

        self._scheduler_loaded_once = True
        logger.info(
            "Scheduler data load complete: "
            f"mode_params={len(mode_params)}, intensities={len(intensities)}, "
            f"programs={len(programs)}, devices={len(device_lookup)}"
        )
