"""Heating Failure Safety Module.

Implements safety logic for heating system failures:
1. Detects heater failure (temp not responding to demand)
2. Triggers emergency responses (alerts, backup systems)
3. Prevents crop damage from freezing
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from shared.logging import get_logger

logger = get_logger(__name__)


class SafetyState(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


@dataclass
class HeaterStatus:
    demand_pct: float  # 0-100
    temp_response: float  # °C change since demand started
    runtime_seconds: float
    is_responding: bool


class HeatingFailureSafety:
    """Monitors heating system and triggers safety responses on failure."""

    # Thresholds
    MIN_RESPONSE_TEMP_RISE = 0.5  # °C rise expected per minute at 100% demand
    MAX_NO_RESPONSE_TIME = 300  # 5 minutes with no response = warning
    CRITICAL_NO_RESPONSE_TIME = 600  # 10 minutes = critical
    EMERGENCY_TEMP_THRESHOLD = 5.0  # °C - emergency if below this

    def __init__(self, min_safe_temp: float = 10.0, alert_callback: Callable | None = None):
        self.min_safe_temp = min_safe_temp
        self.alert_callback = alert_callback

        self._state = SafetyState.NORMAL
        self._demand_start_time: datetime | None = None
        self._demand_start_temp: float | None = None
        self._last_demand = 0.0
        self._consecutive_failures = 0

    @property
    def state(self) -> SafetyState:
        return self._state

    def update(
        self, current_temp: float, heater_demand: float, current_time: datetime
    ) -> SafetyState:
        """Update safety monitor with current conditions.

        Args:
            current_temp: Current air temperature °C
            heater_demand: Current heater demand 0-100%
            current_time: Current timestamp

        Returns:
            Current safety state
        """
        # Track demand start
        if heater_demand > 10 and self._last_demand <= 10:
            self._demand_start_time = current_time
            self._demand_start_temp = current_temp
            logger.debug(f"Heater demand started: {heater_demand}% at {current_temp}°C")

        # Check for emergency temperature
        if current_temp < self.EMERGENCY_TEMP_THRESHOLD:
            self._set_state(SafetyState.EMERGENCY, current_temp, heater_demand)
            self._trigger_emergency(current_temp)
            return self._state

        # Check heater response if demand is active
        if heater_demand > 10 and self._demand_start_time:
            elapsed = (current_time - self._demand_start_time).total_seconds()
            temp_rise = current_temp - self._demand_start_temp
            expected_rise = (elapsed / 60) * self.MIN_RESPONSE_TEMP_RISE * (heater_demand / 100)

            # Not responding?
            if elapsed > self.MAX_NO_RESPONSE_TIME and temp_rise < expected_rise * 0.3:
                if elapsed > self.CRITICAL_NO_RESPONSE_TIME:
                    self._set_state(SafetyState.CRITICAL, current_temp, heater_demand)
                    self._consecutive_failures += 1
                else:
                    self._set_state(SafetyState.WARNING, current_temp, heater_demand)
            elif temp_rise >= expected_rise * 0.5:
                # Heater is responding
                if self._state != SafetyState.NORMAL:
                    logger.info(f"Heater responding normally, temp rose {temp_rise:.1f}°C")
                self._state = SafetyState.NORMAL
                self._consecutive_failures = 0

        # Reset tracking when demand stops
        if heater_demand <= 10:
            self._demand_start_time = None
            self._demand_start_temp = None

        self._last_demand = heater_demand
        return self._state

    def _set_state(self, state: SafetyState, temp: float, demand: float):
        if state != self._state:
            logger.warning(
                f"Safety state: {self._state.value} -> {state.value} (temp={temp}°C, demand={demand}%)"
            )
            self._state = state
            if self.alert_callback:
                self.alert_callback(state, temp, demand)

    def _trigger_emergency(self, temp: float):
        logger.critical(f"EMERGENCY: Temperature {temp}°C below safety threshold!")
        # In a real system, this would:
        # 1. Send SMS/push alerts
        # 2. Activate backup heating if available
        # 3. Close all vents to preserve heat
        # 4. Log to incident database

    def get_status(self) -> dict:
        return {
            "state": self._state.value,
            "consecutive_failures": self._consecutive_failures,
            "min_safe_temp": self.min_safe_temp,
        }
