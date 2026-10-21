"""Control history and ramp-restoration methods."""

from __future__ import annotations

from datetime import UTC, datetime
import math
from typing import Any

from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from shared.db_errors import is_transient_db_error
from shared.infra_logging import get_logger

logger = get_logger("app.control.control_engine")


class HistoryMixin:
    async def _restore_ramps_on_startup(self) -> None:
        """Restore active ramps from StateManager (Redis-backed) on startup."""
        try:
            # Set Redis client for StateManager if available
            redis_client = self.database._automation_redis
            if redis_client and redis_client.redis_enabled:
                self._state.set_redis_client(redis_client.redis_client)
                # Also sync RampManager's redis for backward compatibility
                self.setpoint_manager.ramp_manager.set_redis(redis_client)

            # Restore ramps via StateManager (which handles Redis fallback)
            persisted_ramps = await self._state.get_persisted_ramps()
            if persisted_ramps:
                from datetime import datetime

                now = datetime.now()
                restored = 0
                for ramp_data in persisted_ramps:
                    try:
                        location = ramp_data.get("location")
                        cluster = ramp_data.get("cluster")
                        setpoint_type = ramp_data.get("setpoint_type")
                        start_time = ramp_data.get("start_time")
                        duration = ramp_data.get("duration_minutes", 0)
                        start_value = ramp_data.get("start_value")
                        target_value = ramp_data.get("target_value")

                        if location and cluster and setpoint_type and start_time:
                            # Check if ramp is still active
                            from datetime import timedelta

                            end_time = start_time + timedelta(minutes=int(duration))
                            if now < end_time:
                                self.setpoint_manager.ramp_manager.start_ramp(
                                    location,
                                    cluster,
                                    setpoint_type,
                                    float(start_value or 0),
                                    float(target_value or 0),
                                    float(duration),
                                    start_time,
                                )
                                restored += 1
                    except Exception as ramp_err:
                        logger.warning(f"Failed to restore ramp {ramp_data}: {ramp_err}")

                if restored > 0:
                    logger.info(
                        f"Control engine startup: restored {restored} active ramp(s) via StateManager"
                    )
                else:
                    logger.info("Control engine startup: no active ramps to restore")
            else:
                logger.info("Control engine startup: no persisted ramps found")
        except Exception as e:
            logger.error(f"Failed to restore ramps on startup: {e}")
            self.setpoint_manager.ramp_manager.clear_all_ramps()

    async def _log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        current_period_name: str,
        effective_data: dict[str, Any],
        current_time: datetime,
    ) -> None:
        """Log effective setpoints to database."""
        await self.database.setpoint_repo.log_effective_setpoints(
            location=location,
            cluster=cluster,
            device_name="Main",
            mode=current_period_name,
            effective_heating_setpoint=effective_data["effective_heating_setpoint"],
            effective_cooling_setpoint=effective_data["effective_cooling_setpoint"],
            effective_humidity_setpoint=effective_data["effective_humidity_setpoint"],
            effective_co2_setpoint=effective_data["effective_co2_setpoint"],
            effective_vpd_setpoint=effective_data["effective_vpd_setpoint"],
            nominal_heating_setpoint=effective_data["nominal_heating_setpoint"],
            nominal_cooling_setpoint=effective_data["nominal_cooling_setpoint"],
            nominal_humidity_setpoint=effective_data["nominal_humidity_setpoint"],
            nominal_co2_setpoint=effective_data["nominal_co2_setpoint"],
            nominal_vpd_setpoint=effective_data["nominal_vpd_setpoint"],
            ramp_progress_heating=effective_data["ramp_progress_heating"],
            ramp_progress_cooling=effective_data["ramp_progress_cooling"],
            ramp_progress_humidity=effective_data["ramp_progress_humidity"],
            ramp_progress_co2=effective_data["ramp_progress_co2"],
            ramp_progress_vpd=effective_data["ramp_progress_vpd"],
            timestamp=current_time,
        )

    # _log_light_intensities logic moved inline to run_control_loop

    async def _log_automation_state(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Log automation state only when its decision changes or a heartbeat is due."""
        current_time = datetime.now(UTC)
        heartbeat_slot = math.floor(current_time.timestamp() / 30)
        records: list[dict[str, Any]] = []
        record_keys: list[tuple[str, str, str]] = []
        last_logged_updates: dict[tuple[str, str, str], tuple[tuple[Any, ...], int]] = {}

        for location, clusters in snapshot.hierarchy.items():
            for cluster, cluster_devices in clusters.items():
                for device_name in cluster_devices:
                    key = (location, cluster, device_name)
                    context = self._automation_log_context.get(key, {})
                    current_state = (
                        self.relay_manager.get_device_state(location, cluster, device_name) or 0
                    )
                    current_mode = (
                        self.relay_manager.get_device_mode(location, cluster, device_name) or "auto"
                    )
                    active_rule_ids = context.get("active_rule_ids") or []
                    active_schedule_ids = context.get("active_schedule_ids") or []
                    control_reason = context.get("control_reason", "none")

                    decision_fields = (
                        current_state,
                        current_mode,
                        context.get("pid_output"),
                        context.get("duty_cycle_percent"),
                        tuple(active_rule_ids),
                        tuple(active_schedule_ids),
                        control_reason,
                        context.get("schedule_ramp_up_duration"),
                        context.get("schedule_ramp_down_duration"),
                        context.get("schedule_photoperiod_hours"),
                        context.get("pid_kp"),
                        context.get("pid_ki"),
                        context.get("pid_kd"),
                    )
                    logged_value = (decision_fields, heartbeat_slot)
                    if self._last_logged.get(key) == logged_value:
                        continue

                    records.append(
                        {
                            "time": current_time,
                            "location": location,
                            "cluster": cluster,
                            "device_name": device_name,
                            "device_state": current_state,
                            "device_mode": current_mode,
                            "pid_output": context.get("pid_output"),
                            "duty_cycle_percent": context.get("duty_cycle_percent"),
                            "active_rule_ids": active_rule_ids,
                            "active_schedule_ids": active_schedule_ids,
                            "control_reason": control_reason,
                            "schedule_ramp_up_duration": context.get("schedule_ramp_up_duration"),
                            "schedule_ramp_down_duration": context.get(
                                "schedule_ramp_down_duration"
                            ),
                            "schedule_photoperiod_hours": context.get("schedule_photoperiod_hours"),
                            "pid_kp": context.get("pid_kp"),
                            "pid_ki": context.get("pid_ki"),
                            "pid_kd": context.get("pid_kd"),
                        }
                    )
                    record_keys.append(key)
                    last_logged_updates[key] = logged_value

        if records:
            accepted_count = self._automation_state_pending.offer(records)
            for index in range(accepted_count):
                key = record_keys[index]
                self._last_logged[key] = last_logged_updates[key]

        while self._automation_state_pending:
            batch = self._automation_state_pending.peek(500)
            try:
                persisted = await self.database.control_action_repo.log_automation_state_batch(
                    batch
                )
            except Exception as error:
                batch_count = len(batch)
                if is_transient_db_error(error):
                    logger.warning(
                        "Retaining %d automation-state rows after transient database error: %s",
                        batch_count,
                        error,
                    )
                    break

                self._automation_state_pending.consume(batch_count)
                logger.error(
                    "Dropped %d automation-state rows after permanent database error: %s",
                    batch_count,
                    error,
                    exc_info=True,
                )
                continue

            if not persisted:
                batch_count = len(batch)
                self._automation_state_pending.consume(batch_count)
                logger.error(
                    "Dropped %d automation-state rows after an unsuccessful batch result",
                    batch_count,
                )
                continue
            self._automation_state_pending.consume(len(batch))

    async def restore_ramp_state_from_database(self) -> None:
        """Handle ramp state on service startup.

        Light ramp state is time-based and resumes automatically by recalculating
        intensity from elapsed time since schedule start. Climate ramps (setpoints)
        are restored from Redis if available to ensure continuity of environmental
        transitions.
        """
        await self._restore_ramps_on_startup()
