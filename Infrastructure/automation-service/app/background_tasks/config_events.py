"""Config event consumer loop for schedule/mode/PID cache invalidation."""

from __future__ import annotations

import asyncio
from typing import Any

from app.control.schedule_merge import merge_schedules_with_config
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ConfigEventsMixin:
    """Mixin for consuming config change events and updating scheduler caches."""

    async def _config_event_consumer_loop(self) -> None:
        """Consume config change events and update scheduler immediately.

        This runs as a background task that subscribes to the ConfigEventBus
        and reacts to configuration changes by updating the scheduler immediately
        (bypassing the 60-second refresh interval).

        Events handled:
        - RAMP_TIMES_CHANGED: Refreshes schedules from database and updates scheduler
        """
        from app.events import ConfigEventType, get_event_bus

        event_bus = get_event_bus()
        logger.info("Config event consumer started")

        try:
            async for event in event_bus.subscribe():
                try:
                    logger.info(
                        "Processing config event: "
                        + f"{event.event_type.value} for {event.location}/{event.cluster}"
                    )

                    if event.event_type == ConfigEventType.RAMP_TIMES_CHANGED:
                        # Fetch fresh schedules and update scheduler
                        if not self.database._db_connected:
                            logger.warning(
                                "Database not connected, cannot refresh schedules for event"
                            )
                            continue

                        db_schedules = await self.database.schedule_repo.get_schedules()
                        if self.control_engine.scheduler:
                            merged = await merge_schedules_with_config(
                                db_schedules, self.control_engine.config
                            )
                            self.control_engine.scheduler.update_schedules(merged)
                            logger.info(
                                "Scheduler updated with "
                                + f"{len(merged)} schedules after config change event"
                            )

                    if event.event_type == ConfigEventType.SCHEDULE_CHANGED:
                        # Refresh schedules AND light-specific caches (NOT mode_params)
                        if not self.database._db_connected:
                            logger.warning(
                                "Database not connected, cannot refresh schedules for event"
                            )
                            continue

                        # 1. Refresh non-light schedules via merge_schedules_with_config
                        db_schedules = await self.database.schedule_repo.get_schedules()
                        if self.control_engine.scheduler:
                            merged = await merge_schedules_with_config(
                                db_schedules, self.control_engine.config
                            )
                            self.control_engine.scheduler.update_schedules(merged)
                            logger.info(
                                "Scheduler updated with "
                                + f"{len(merged)} schedules after SCHEDULE_CHANGED event"
                            )

                        # 2. Reload light intensities + programs + device lookup
                        scheduler = self.control_engine.scheduler
                        if scheduler:
                            try:
                                intensities = await self.database.light_target_intensity_repo.get_all_intensities()
                                scheduler.update_light_intensities(intensities)
                            except Exception as e:
                                logger.error(
                                    f"Failed to reload light intensities on SCHEDULE_CHANGED: {e}"
                                )
                            try:
                                programs = (
                                    await self.database.light_programs_repo.get_all_programs()
                                )
                                scheduler.update_light_programs(programs)
                            except Exception as e:
                                logger.error(
                                    f"Failed to reload light programs on SCHEDULE_CHANGED: {e}"
                                )
                            try:
                                devices = await self.control_engine.config.get_devices()
                                device_lookup: dict[tuple[str, str, str], dict[str, Any]] = {}
                                for location, clusters in devices.items():
                                    for cluster, devs in clusters.items():
                                        for device_name, info in devs.items():
                                            device_lookup[(location, cluster, device_name)] = dict(
                                                info
                                            )
                                scheduler.update_device_lookup(device_lookup)
                            except Exception as e:
                                logger.error(
                                    f"Failed to reload device lookup on SCHEDULE_CHANGED: {e}"
                                )

                        # Invalidate schedule cache in StateManager
                        state = getattr(self.control_engine, "_state", None)
                        if state:
                            await state.delete(f"schedule:{event.location}:{event.cluster}")

                    if event.event_type == ConfigEventType.MODE_CHANGED:
                        # Reload mode_parameters + intensities + programs (device_lookup unchanged)
                        scheduler = self.control_engine.scheduler
                        if scheduler and self.database._db_connected:
                            # 1. mode_parameters for the affected room
                            try:
                                active_mode = await self.database.room_mode_repo.get_active_mode(
                                    event.location, event.cluster
                                )
                                if active_mode:
                                    mode_name = active_mode.get("mode_name")
                                    submode_name = active_mode.get("submode_name")
                                    if mode_name:
                                        params = (
                                            await self.database.room_mode_repo.get_mode_parameters(
                                                event.location,
                                                event.cluster,
                                                mode_name,
                                                submode_name,
                                            )
                                        )
                                        if params:
                                            # Merge into existing mode_params (atomic swap of full dict)
                                            current_params = dict(scheduler._mode_params)
                                            current_params[(event.location, event.cluster)] = {
                                                "mode_id": params.get("mode_id"),
                                                "day_start": params.get("day_start_time", "06:00"),
                                                "night_start": params.get(
                                                    "night_start_time", "18:00"
                                                ),
                                                "ramp_up": params.get("light_ramp_up_minutes", 0),
                                                "ramp_down": params.get(
                                                    "light_ramp_down_minutes", 0
                                                ),
                                            }
                                            scheduler.update_mode_parameters(current_params)
                            except Exception as e:
                                logger.error(
                                    f"Failed to reload mode_parameters on MODE_CHANGED: {e}"
                                )

                            # 2. light intensities
                            try:
                                intensities = await self.database.light_target_intensity_repo.get_all_intensities()
                                scheduler.update_light_intensities(intensities)
                            except Exception as e:
                                logger.error(
                                    f"Failed to reload light intensities on MODE_CHANGED: {e}"
                                )

                            # 3. light programs
                            try:
                                programs = (
                                    await self.database.light_programs_repo.get_all_programs()
                                )
                                scheduler.update_light_programs(programs)
                            except Exception as e:
                                logger.error(
                                    f"Failed to reload light programs on MODE_CHANGED: {e}"
                                )

                        # Invalidate mode cache in StateManager
                        state = getattr(self.control_engine, "_state", None)
                        if state:
                            await state.delete_mode(event.location, event.cluster)

                    if event.event_type == ConfigEventType.PID_PARAMS_CHANGED:
                        # Invalidate PID params cache in StateManager
                        state = getattr(self.control_engine, "_state", None)
                        if state:
                            device_type = event.data.get("device_type") if event.data else None
                            if device_type:
                                await state.delete(f"pid:parameters:{device_type}")

                    if event.event_type == ConfigEventType.SETPOINT_CHANGED:
                        # Invalidate climate period cache for this location/cluster
                        state = getattr(self.control_engine, "_state", None)
                        if state and event.location and event.cluster:
                            # Partial invalidation: clear all climate period entries for this room
                            # StateManager doesn't have pattern delete, so we accept stale reads for 30s TTL
                            logger.debug(
                                f"Setpoint changed for {event.location}/{event.cluster}, "
                                "climate period cache will expire naturally in 30s"
                            )

                except Exception as e:
                    logger.error(f"Error processing config event: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Config event consumer stopped")
            raise
