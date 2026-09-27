"""Binary (on/off) device control with hysteresis."""

from __future__ import annotations

import math
from typing import Any

from app.control.decision_event_policy import DecisionObservation
from app.control.relay_manager import RelayCommandReason
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
        pid_decision: DecisionObservation | None = None,
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
            transition_threshold = 0.5 - band
            state = 1 if output >= transition_threshold else 0
        elif last_state == 0:
            # Currently OFF: only go ON above the upper threshold.
            transition_threshold = 0.5 + band
            state = 1 if output > transition_threshold else 0
        else:
            # Uninitialized: use the natural threshold so the first call still
            # tracks the output, just without the band protection.
            transition_threshold = 0.5
            state = 1 if output > transition_threshold else 0

        # If hysteresis kept the state, skip the hardware write to prevent chatter.
        if last_state is not None and state == last_state:
            return
        command_reason = self._build_pid_relay_reason(
            pid_decision,
            device_type,
            output,
            state,
            last_state,
            transition_threshold,
        )

        # If batch_executor provided, queue operation for parallel execution
        if batch_executor is not None and self.relay_manager is not None:
            batch_executor.queue_binary_device(
                location=location,
                cluster=cluster,
                device_name=device_name,
                state=state,
                command_reason=command_reason,
                relay_manager=self.relay_manager,
            )
            self._last_binary_state[key] = state
            return

        # Apply the state directly
        success = await self.relay_manager.set_channel_state(
            channel, state, command_reason=command_reason
        )

        if success:
            self._last_binary_state[key] = state
            logger.info(f"{device_name} ({location}/{cluster}) set to {'ON' if state else 'OFF'}")
        else:
            logger.warning(f"Failed to set {device_name} ({location}/{cluster}) state")

    @staticmethod
    def _build_pid_relay_reason(
        observation: DecisionObservation | None,
        device_type: str,
        output: float,
        state: int,
        last_state: int | None,
        transition_threshold: float,
    ) -> RelayCommandReason | None:
        """Describe the recorded PID decision behind this hysteresis transition."""
        controller_names = {
            "pid": "PID",
            "auto_pid": "AUTO PID",
            "on_off": "ON/OFF",
        }
        reason_codes = {
            "pid": "pid.binary_transition",
            "auto_pid": "auto_pid.binary_transition",
            "on_off": "on_off.binary_transition",
        }
        if observation is None:
            return None
        if not isinstance(observation.controller, str):
            return None

        controller_name = controller_names.get(observation.controller)
        reason_code = reason_codes.get(observation.controller)
        if controller_name is None or reason_code is None:
            return None

        def finite_number(value: Any) -> float | None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            try:
                numeric = float(value)
            except OverflowError:
                return None
            return numeric if math.isfinite(numeric) else None

        output_percent = finite_number(observation.output_percent)
        requested_output = finite_number(output)
        if (
            output_percent is None
            or not 0 <= output_percent <= 100
            or requested_output is None
            or output_percent != requested_output * 100.0
        ):
            return None

        threshold = finite_number(transition_threshold * 100.0)
        if threshold is None:
            return None
        if state == 1:
            threshold_text = f"crossed >{threshold:g}% ON threshold"
        elif last_state == 1:
            threshold_text = f"crossed <{threshold:g}% OFF threshold"
        else:
            threshold_text = f"did not cross >{threshold:g}% ON threshold"

        unit = {"heating": "°C", "cooling": "°C", "co2": "ppm"}.get(device_type, "")
        facts = []
        for label, value in (
            ("sensor", finite_number(observation.sensor_value)),
            ("target", finite_number(observation.effective_setpoint)),
        ):
            if value is not None:
                facts.append(f"{label} {value:g}{unit}")
        facts.append(f"output {output_percent:g}% {threshold_text}")
        relay_state = "ON" if state else "OFF"
        reason_text = f"{controller_name} {device_type} relay {relay_state}: {'; '.join(facts)}."
        return RelayCommandReason(reason_code=reason_code, reason_text=reason_text)
