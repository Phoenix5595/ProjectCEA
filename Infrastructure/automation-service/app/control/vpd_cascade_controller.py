"""VPD Cascade Controller with Actuator Selection Logic.

Implements VPD-driven supervisory control:
1. Outer loop: Calculates VPD error (what needs to change)
2. Inner loop: Selects best actuator based on constraints

Decision Hierarchy (energy priority):
1. Passive ventilation (lowest energy)
2. Active dehumidification
3. Thermal manipulation (highest energy)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class ActuatorType(Enum):
    NONE = "none"
    EXHAUST_FAN = "exhaust_fan"
    DEHUMIDIFIER = "dehumidifier"
    HUMIDIFIER = "humidifier"
    HEATER = "heater"
    COOLER = "cooler"


@dataclass
class EnvironmentState:
    air_temp_c: float
    humidity_pct: float
    outside_temp_c: float | None = None
    outside_humidity_pct: float | None = None
    leaf_temp_delta: float = -2.0

    @property
    def leaf_temp_c(self) -> float:
        return self.air_temp_c + self.leaf_temp_delta

    @property
    def outside_abs_humidity(self) -> float | None:
        if self.outside_temp_c is None or self.outside_humidity_pct is None:
            return None
        return self._calc_abs_humidity(self.outside_temp_c, self.outside_humidity_pct)

    @property
    def inside_abs_humidity(self) -> float:
        return self._calc_abs_humidity(self.air_temp_c, self.humidity_pct)

    @staticmethod
    def _calc_abs_humidity(temp_c: float, rh_pct: float) -> float:
        svp = 6.112 * math.exp((17.67 * temp_c) / (temp_c + 243.5))
        return (svp * rh_pct * 2.1674) / (273.15 + temp_c)


@dataclass
class TempConstraints:
    min_temp: float
    max_temp: float
    heating_setpoint: float
    cooling_setpoint: float

    def near_cooling_limit(self, current_temp: float, margin: float = 1.0) -> bool:
        return current_temp >= self.cooling_setpoint - margin

    def near_heating_limit(self, current_temp: float, margin: float = 1.0) -> bool:
        return current_temp <= self.heating_setpoint + margin


@dataclass
class ActuatorCommand:
    actuator: ActuatorType
    output_pct: float
    reason: str
    priority: int = 0


@dataclass
class VPDCascadeOutput:
    vpd_current: float
    vpd_target: float
    vpd_error: float
    primary_command: ActuatorCommand
    secondary_commands: list[ActuatorCommand]
    decision_reason: str


class VPDCascadeController:
    """VPD Cascade Controller with intelligent actuator selection."""

    def __init__(
        self, vpd_deadband: float = 0.05, kp: float = 20.0, ki: float = 0.5, kd: float = 2.0
    ):
        self.vpd_deadband = vpd_deadband
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self._integral = 0.0
        self._last_error = 0.0
        self._integral_max = 50.0

    @staticmethod
    def calculate_svp(temp_c: float) -> float:
        return 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))

    def calculate_vpd(self, env: EnvironmentState) -> float:
        svp_leaf = self.calculate_svp(env.leaf_temp_c)
        svp_air = self.calculate_svp(env.air_temp_c)
        return max(0.0, svp_leaf - (svp_air * env.humidity_pct / 100.0))

    def update(
        self,
        env: EnvironmentState,
        target_vpd: float,
        temp_constraints: TempConstraints,
        dt: float = 1.0,
    ) -> VPDCascadeOutput:
        current_vpd = self.calculate_vpd(env)
        vpd_error = target_vpd - current_vpd

        if abs(vpd_error) < self.vpd_deadband:
            return VPDCascadeOutput(
                current_vpd,
                target_vpd,
                vpd_error,
                ActuatorCommand(ActuatorType.NONE, 0, "Within deadband"),
                [],
                "VPD OK",
            )

        self._integral = max(
            -self._integral_max, min(self._integral_max, self._integral + vpd_error * dt)
        )
        derivative = (vpd_error - self._last_error) / dt if dt > 0 else 0
        pid_output = min(
            100.0, abs(self.kp * vpd_error + self.ki * self._integral + self.kd * derivative)
        )
        self._last_error = vpd_error

        if vpd_error < 0:
            return self._handle_vpd_too_high(
                env, temp_constraints, pid_output, current_vpd, target_vpd, vpd_error
            )
        else:
            return self._handle_vpd_too_low(
                env, temp_constraints, pid_output, current_vpd, target_vpd, vpd_error
            )

    def _handle_vpd_too_low(
        self,
        env: EnvironmentState,
        constraints: TempConstraints,
        output: float,
        current_vpd: float,
        target_vpd: float,
        vpd_error: float,
    ) -> VPDCascadeOutput:
        """VPD too low (humid) -> dry the air. Priority: vent > dehum > heat"""
        outside_drier = False
        if env.outside_abs_humidity is not None:
            outside_drier = env.outside_abs_humidity < env.inside_abs_humidity * 0.9

        # CASE A: Near cooling + outside drier -> VENTILATE
        if constraints.near_cooling_limit(env.air_temp_c) and outside_drier:
            cmd = ActuatorCommand(
                ActuatorType.EXHAUST_FAN, output, "VPD low, near cooling, outside drier", 10
            )
            reason = "Ventilate: temp near cooling limit, outside air drier"
        # CASE B: Temps low -> DEHUMIDIFIER (venting would cool more)
        elif constraints.near_heating_limit(env.air_temp_c):
            cmd = ActuatorCommand(ActuatorType.DEHUMIDIFIER, output, "VPD low, temps low", 10)
            reason = "Dehumidifier: temps low, preserve heat"
        # CASE C: Outside drier -> VENTILATE (energy efficient)
        elif outside_drier:
            cmd = ActuatorCommand(
                ActuatorType.EXHAUST_FAN, output * 0.8, "VPD low, outside drier", 10
            )
            reason = "Ventilate: outside air drier, passive solution"
        # CASE D: Outside humid -> DEHUMIDIFIER
        else:
            cmd = ActuatorCommand(ActuatorType.DEHUMIDIFIER, output, "VPD low, outside humid", 10)
            reason = "Dehumidifier: no passive option"

        return VPDCascadeOutput(current_vpd, target_vpd, vpd_error, cmd, [], reason)

    def _handle_vpd_too_high(
        self,
        env: EnvironmentState,
        constraints: TempConstraints,
        output: float,
        current_vpd: float,
        target_vpd: float,
        vpd_error: float,
    ) -> VPDCascadeOutput:
        """VPD too high (dry) -> humidify"""
        cmd = ActuatorCommand(ActuatorType.HUMIDIFIER, output, "VPD high", 10)
        secondary: list[ActuatorCommand] = []
        reason = "Humidifier: VPD too high"

        if constraints.near_cooling_limit(env.air_temp_c):
            secondary.append(
                ActuatorCommand(ActuatorType.COOLER, output * 0.5, "Supplemental cooling", 5)
            )
            reason += " + Cooling assist"

        return VPDCascadeOutput(current_vpd, target_vpd, vpd_error, cmd, secondary, reason)

    def reset(self):
        self._integral = 0.0
        self._last_error = 0.0
