"""Control Module - Climate and Device Control.

This module provides:
- PID controllers for temperature, humidity, CO2
- VPD cascade controller with leaf temperature input
- Device control (heaters, humidifiers, fans, lights)
- Setpoint management with ramping
- Control engine orchestration

Public API:
    from app.control import (
        ControlEngine,
        PIDControllerManager, 
        VPDController,
        SetpointManager,
        DeviceController,
    )
"""
from app.control.control_engine import ControlEngine
from app.control.pid_controller_manager import PIDControllerManager
from app.control.vpd_controller import VPDController
from app.control.vpd_cascade_controller import VPDCascadeController
from app.control.setpoint_manager import SetpointManager
from app.control.device_controller import DeviceController

__all__ = [
    'ControlEngine',
    'PIDControllerManager',
    'VPDController', 
    'SetpointManager',
    'DeviceController',
    'VPDCascadeController',
]
