"""VPD Cascade Controller with Leaf Temperature Input.

Implements VPD (Vapor Pressure Deficit) as the master controller that:
1. Calculates current VPD from air temp, humidity, and leaf temp delta
2. Adjusts humidity setpoints to achieve target VPD
3. Uses cascade control: VPD -> Humidity setpoint

VPD Formula:
  SVP_leaf = 0.6108 * exp((17.27 * T_leaf) / (T_leaf + 237.3))
  SVP_air = 0.6108 * exp((17.27 * T_air) / (T_air + 237.3))
  VPD = SVP_leaf - (SVP_air * RH / 100)
"""
import math
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class VPDState:
    vpd_kpa: float
    air_temp_c: float
    leaf_temp_c: float
    humidity_pct: float
    svp_leaf: float
    svp_air: float


class VPDController:
    DEFAULT_LEAF_DELTA_C = -2.0
    VPD_MIN = 0.4
    VPD_MAX = 1.6
    VPD_RANGES = {
        'propagation': (0.4, 0.8),
        'vegetative': (0.8, 1.2),
        'flowering': (1.0, 1.5),
        'late_flowering': (1.2, 1.6),
    }
    
    def __init__(self, leaf_temp_delta: float = DEFAULT_LEAF_DELTA_C,
                 kp: float = 5.0, ki: float = 0.1, kd: float = 0.5):
        self.leaf_temp_delta = leaf_temp_delta
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self._integral = 0.0
        self._last_error = 0.0
        self._integral_max = 20.0
        
    @staticmethod
    def calculate_svp(temp_c: float) -> float:
        return 0.6108 * math.exp((17.27 * temp_c) / (temp_c + 237.3))
    
    def calculate_vpd(self, air_temp_c: float, humidity_pct: float,
                      leaf_temp_c: Optional[float] = None) -> VPDState:
        if leaf_temp_c is None:
            leaf_temp_c = air_temp_c + self.leaf_temp_delta
        svp_leaf = self.calculate_svp(leaf_temp_c)
        svp_air = self.calculate_svp(air_temp_c)
        vpd = max(0.0, svp_leaf - (svp_air * humidity_pct / 100.0))
        return VPDState(vpd_kpa=vpd, air_temp_c=air_temp_c, leaf_temp_c=leaf_temp_c,
                        humidity_pct=humidity_pct, svp_leaf=svp_leaf, svp_air=svp_air)
    
    def calculate_target_humidity(self, target_vpd: float, air_temp_c: float,
                                   leaf_temp_c: Optional[float] = None) -> float:
        if leaf_temp_c is None:
            leaf_temp_c = air_temp_c + self.leaf_temp_delta
        svp_leaf = self.calculate_svp(leaf_temp_c)
        svp_air = self.calculate_svp(air_temp_c)
        if svp_air <= 0:
            return 50.0
        target_rh = 100.0 * (svp_leaf - target_vpd) / svp_air
        return max(40.0, min(85.0, target_rh))
    
    def update(self, current_vpd: float, target_vpd: float, dt: float = 1.0) -> Tuple[float, Dict]:
        error = target_vpd - current_vpd
        self._integral = max(-self._integral_max, min(self._integral_max, self._integral + error * dt))
        derivative = (error - self._last_error) / dt if dt > 0 else 0.0
        output = -(self.kp * error + self.ki * self._integral + self.kd * derivative)
        output = max(-10.0, min(10.0, output))
        self._last_error = error
        return output, {'error': error, 'integral': self._integral, 'derivative': derivative}
    
    def get_optimal_vpd(self, growth_stage: str = 'vegetative') -> float:
        vpd_range = self.VPD_RANGES.get(growth_stage, self.VPD_RANGES['vegetative'])
        return (vpd_range[0] + vpd_range[1]) / 2.0
    
    def reset(self):
        self._integral = 0.0
        self._last_error = 0.0


def calculate_vpd(air_temp_c: float, humidity_pct: float, leaf_temp_delta: float = -2.0) -> float:
    controller = VPDController(leaf_temp_delta=leaf_temp_delta)
    return controller.calculate_vpd(air_temp_c, humidity_pct).vpd_kpa
