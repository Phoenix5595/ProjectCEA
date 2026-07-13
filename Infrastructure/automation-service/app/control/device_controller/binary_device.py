"""Binary (on/off) device control with hysteresis."""

from __future__ import annotations

from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class BinaryDeviceMixin:
    """Mixin for binary relay device control with hysteresis band."""

    async def _control_binary_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_type: str,
        channel: int,
        output: float,
        batch_executor: Any | None = None,
        device_info: dict[str, Any] | None = None,
    ) -> None:
        """Control a binary (on/off) device with hysteresis.

        Hysteresis prevents relay chatter when the control output oscillates
        around the 0.5 threshold. With ``band = binary_hysteresis``:
          - Currently OFF  -> ON  only when output > 0.5 + band
          - Currently ON   -> OFF only when output < 0.5 - band
          - In the band [0.5 - band, 0.5 + band], the prior state is preserved
            and no hardware write is issued.

        ``band`` is taken from ``device_info["binary_hysteresis"]`` if present,
        otherwise from the controller-wide ``self.binary_hysteresis`` (default 0.1).
        """
        # Per-device band override; falls back to controller default.
        band = self.binary_hysteresis
        if device_info is not None:
            override = device_info.get("binary_hysteresis")
            if override is not None:
                band = float(override)

        key = (location, cluster, device_name)
        last_state = self._last_binary_state.get(key)

        if last_state == 1:
            # Currently ON: only go OFF below the lower threshold.
            state = 1 if output >= (0.5 - band) else 0
        elif last_state == 0:
            # Currently OFF: only go ON above the upper threshold.
            state = 1 if output > (0.5 + band) else 0
        else:
            # Uninitialized: use the natural threshold so the first call still
            # tracks the output, just without the band protection.
            state = 1 if output > 0.5 else 0

        # If hysteresis kept the state, skip the hardware write to prevent chatter.
        if last_state is not None and state == last_state:
            return

        # If batch_executor provided, queue operation for parallel execution
        if batch_executor is not None and self.relay_manager is not None:
            batch_executor.queue_binary_device(
                location=location,
                cluster=cluster,
                device_name=device_name,
                state=state,
                relay_manager=self.relay_manager,
            )
            self._last_binary_state[key] = state
            return

        # Apply the state directly
        success = await self.relay_manager.set_channel_state(channel, state)

        if success:
            self._last_binary_state[key] = state
            logger.info(f"{device_name} ({location}/{cluster}) set to {'ON' if state else 'OFF'}")
        else:
            logger.warning(f"Failed to set {device_name} ({location}/{cluster}) state")
