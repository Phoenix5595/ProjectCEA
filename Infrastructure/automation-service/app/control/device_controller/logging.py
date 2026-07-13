"""Control action logging, state restore, and telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class LoggingMixin:
    """Mixin for control action logging, state restore, and telemetry."""

    async def _log_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_type: str,
        channel: int,
        control_output: float,
        current_time: datetime,
        old_state: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log a control action to the database.

        For DFR0971 lights without a relay, ``channel`` may be the dimming_channel value
        (0–1) or ``-1`` if dimming config was incomplete.
        """
        try:
            new_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
            reason = self._reason_for_device_type(device_type, new_state)
            load_percent = None
            if context:
                pid_out = context.get("pid_output")
                if pid_out is not None:
                    load_percent = float(pid_out) * 100.0 if 0 <= pid_out <= 1 else float(pid_out)
                    load_percent = max(0.0, min(100.0, load_percent))

            await self.database.control_action_repo.log_control_action(
                location=location,
                cluster=cluster,
                device_name=device_name,
                channel=channel,
                old_state=old_state,
                new_state=new_state,
                mode="auto",
                reason=reason,
                load_percent=load_percent,
            )
        except Exception as e:
            logger.warning(f"Failed to log control action for {device_name}: {e}")

    async def restore_device_states(self, location: str, cluster: str) -> None:
        """Restore device states from database after restart."""
        try:
            device_states = await self.database.device_repo.get_device_states(location, cluster)

            restored_count = 0
            for device_name, state_info in device_states.items():
                try:
                    channel = state_info.get("channel")
                    state = state_info.get("state", 0)

                    if channel is None:
                        # No channel in DB row; nothing to do.
                        continue

                    # Defense-in-depth: this method is currently not called at startup,
                    # but guard in case it is wired up in the future. Refuse to write
                    # to channels that have no device mapped, so a stale DB row for a
                    # removed device can never toggle an unrelated relay.
                    if channel not in self.relay_manager._channel_map:
                        logger.warning(
                            f"Skipping restore for {device_name} ({location}/{cluster}): "
                            f"channel {channel} is not in the current relay channel map"
                        )
                        continue

                    success = await self.relay_manager.set_channel_state(channel, state)
                    if success:
                        restored_count += 1
                        logger.info(
                            f"Restored {device_name} ({location}/{cluster}) to state {state}"
                        )
                    else:
                        logger.warning(f"Failed to restore {device_name} ({location}/{cluster})")

                except Exception as e:
                    logger.warning(f"Error restoring state for {device_name}: {e}")

            logger.info(f"Restored {restored_count} device states for {location}/{cluster}")

        except Exception as e:
            logger.error(f"Failed to restore device states for {location}/{cluster}: {e}")

    def get_device_status(self) -> dict[str, dict[str, Any]]:
        """Get status of all controllable devices."""
        # This would aggregate status from relay manager and DFR0971 manager
        status = {}

        if self.relay_manager:
            # Add relay status
            status["relays"] = getattr(self.relay_manager, "get_status", lambda: {})()

        if self.dfr0971_manager:
            # Add DFR0971 status
            status["dimmable_lights"] = getattr(self.dfr0971_manager, "get_status", lambda: {})()

        return status

    def write_light_telemetry(
        self,
        location: str,
        cluster: str,
        device_name: str,
        percent: int,
        board_id: str | None,
        channel: int | None,
    ) -> None:
        """Write light telemetry through a single Redis writer path."""
        if not (
            self.database
            and hasattr(self.database, "_automation_redis")
            and self.database._automation_redis
        ):
            return
        safe_percent = int(max(0, min(100, percent)))
        voltage = (safe_percent / 100.0) * 10.0
        try:
            self.database._automation_redis.write_light_intensity(
                location, cluster, device_name, safe_percent, voltage, board_id, channel
            )
        except Exception as redis_err:
            logger.warning(f"Failed to write light telemetry to Redis: {redis_err}")

    def get_last_applied_light_percent(self, location: str, cluster: str, device_name: str) -> int:
        """Return last known good applied percent for hold-last fallback."""
        return int(self._last_applied_light.get((location, cluster, device_name), 0))
