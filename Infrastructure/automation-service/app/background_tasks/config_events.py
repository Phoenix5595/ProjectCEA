"""Config event consumer loop for schedule/mode/PID cache invalidation."""

from __future__ import annotations

import asyncio

from app.control.schedule_merge import merge_schedules_with_config
from app.redis.schema import pid_key
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

                        # Secondary schedule changes still install all projections as one version.
                        await self._reload_runtime_snapshot()

                        # Invalidate schedule cache in StateManager
                        state = getattr(self.control_engine, "_state", None)
                        if state:
                            await state.delete(f"schedule:{event.location}:{event.cluster}")

                    if event.event_type == ConfigEventType.MODE_CHANGED:
                        await self._reload_runtime_snapshot()

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
                                await state.delete(pid_key(str(device_type)))

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

    async def _reload_runtime_snapshot(self) -> None:
        """Replace every control projection after a committed configuration event."""
        registry = self.control_engine.runtime_device_registry
        if registry is None:
            raise RuntimeError("Runtime device registry is not configured")
        await registry.reload_after_commit()
