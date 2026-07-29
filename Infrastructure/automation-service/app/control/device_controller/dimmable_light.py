"""Dimmable light control via DFR0971 with relay synchronization."""

from __future__ import annotations

import asyncio
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class DimmableLightMixin:
    """Mixin for DFR0971 dimmable light control."""

    async def _control_dimmable_light(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: dict[str, Any],
        intensity: float,
        batch_executor: Any | None = None,
    ) -> None:
        """Control a dimmable light with relay synchronization.

        The relay provides POWER to the light, the dimmer provides 0-10V SIGNAL.
        - If intensity > 0: Turn relay ON first, then set dimmer
        - If intensity = 0: Set dimmer to 0, then turn relay OFF
        """
        if not self.dfr0971_manager:
            logger.warning(f"No DFR0971 manager available for {device_name}")
            return

        board_id = device_info.get("dimming_board_id")
        dimming_channel = device_info.get("dimming_channel")
        relay_channel = device_info.get("channel")  # Relay channel for power control

        if board_id is None or dimming_channel is None:
            logger.warning(
                f"Incomplete DFR0971 config for {device_name}: board_id={board_id}, channel={dimming_channel}"
            )
            return

        # If batch_executor provided, queue operations for parallel execution
        if batch_executor is not None:
            intensity_percent = round(intensity * 100)
            if intensity > 0:
                batch_executor.queue_light_on(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    intensity=intensity_percent,
                    relay_manager=self.relay_manager,
                    dfr0971_manager=self.dfr0971_manager,
                    board_id=board_id,
                    dimming_channel=dimming_channel,
                    relay_channel=relay_channel,
                )
            else:
                batch_executor.queue_light_off(
                    location=location,
                    cluster=cluster,
                    device_name=device_name,
                    relay_manager=self.relay_manager,
                    dfr0971_manager=self.dfr0971_manager,
                    board_id=board_id,
                    dimming_channel=dimming_channel,
                    relay_channel=relay_channel,
                )
            return

        try:
            # Convert 0.0-1.0 to 0-100% intensity
            intensity_percent = round(intensity * 100)
            light_key = (location, cluster, device_name)

            # Idempotent command handling: skip duplicate absolute commands.
            prev_cmd = self._last_light_command.get(light_key)
            if prev_cmd == intensity_percent:
                logger.debug(
                    f"Skipping duplicate light command for {device_name} "
                    f"({location}/{cluster}) at {intensity_percent}%"
                )
                return
            relay_ok = True
            dimmer_ok = False

            # CRITICAL: Sync relay state with dimmer
            # Relay ON when intensity > 0, OFF when intensity = 0
            if relay_channel is not None and self.relay_manager:
                if intensity > 0:
                    # Turn relay ON first, then set dimmer (power before signal)
                    relay_ok, relay_reason = await self.relay_manager.set_device_state(
                        location, cluster, device_name, 1
                    )
                    if not relay_ok:
                        logger.warning(
                            f"Relay ON failed for {device_name} ({location}/{cluster}): {relay_reason}"
                        )
                    if relay_ok:
                        dimmer_ok = await asyncio.to_thread(
                            self.dfr0971_manager.set_intensity,
                            board_id,
                            dimming_channel,
                            intensity_percent,
                        )
                    if relay_ok and not dimmer_ok:
                        logger.warning(
                            f"Dimmer set failed for {device_name} ({location}/{cluster}) "
                            f"board={board_id} ch={dimming_channel}"
                        )
                else:
                    # Set dimmer to 0 first, then turn relay OFF (signal before power)
                    dimmer_ok = await asyncio.to_thread(
                        self.dfr0971_manager.set_intensity,
                        board_id,
                        dimming_channel,
                        0,
                    )
                    if not dimmer_ok:
                        logger.warning(
                            f"Dimmer set to 0 failed for {device_name} ({location}/{cluster})"
                        )
                    if dimmer_ok:
                        relay_ok, relay_reason = await self.relay_manager.set_device_state(
                            location, cluster, device_name, 0
                        )
                        if not relay_ok:
                            logger.warning(
                                f"Relay OFF failed for {device_name} ({location}/{cluster}): {relay_reason}"
                            )
                logger.debug(
                    f"Relay channel {relay_channel} set to {'ON' if intensity > 0 else 'OFF'} for {device_name}"
                )
            else:
                # No relay configured, just set dimmer
                dimmer_ok = await asyncio.to_thread(
                    self.dfr0971_manager.set_intensity,
                    board_id,
                    dimming_channel,
                    intensity_percent,
                )
                if not dimmer_ok:
                    logger.warning(
                        f"Dimmer set failed for {device_name} ({location}/{cluster}) "
                        f"board={board_id} ch={dimming_channel}"
                    )

            # Keep last known good hardware level for hold-last behavior on failures.
            hw_ok = relay_ok and dimmer_ok
            if hw_ok:
                self._last_light_command[light_key] = intensity_percent
                self._last_applied_light[light_key] = intensity_percent
                self.write_light_telemetry(
                    location,
                    cluster,
                    device_name,
                    intensity_percent,
                    board_id,
                    dimming_channel,
                )
            else:
                hold_percent = self._last_applied_light.get(light_key, 0)
                self.write_light_telemetry(
                    location,
                    cluster,
                    device_name,
                    hold_percent,
                    board_id,
                    dimming_channel,
                )

            if hw_ok:
                logger.debug(
                    f"Set {device_name} ({location}/{cluster}) to {intensity_percent}% "
                    f"(intensity: {intensity})"
                )
            else:
                logger.warning(
                    f"Hardware control failed for {device_name} ({location}/{cluster}); "
                    f"holding last known light state at {self._last_applied_light.get(light_key, 0)}%"
                )

        except Exception as e:
            logger.error(f"Failed to set dimmable light {device_name}: {e}")
            light_key = (location, cluster, device_name)
            hold_percent = self._last_applied_light.get(light_key, 0)
            self.write_light_telemetry(
                location,
                cluster,
                device_name,
                hold_percent,
                board_id,
                dimming_channel,
            )
            logger.warning(
                f"Exception while controlling {device_name}; "
                f"holding last known light state at {hold_percent}%"
            )
