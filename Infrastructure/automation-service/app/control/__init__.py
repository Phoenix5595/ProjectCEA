"""Control Module - Climate and Device Control.

This module provides:
- PID controllers for temperature, humidity, CO2
- VPD cascade controller with leaf temperature input
- Device control (heaters, humidifiers, fans, lights)
- Setpoint management with ramping
- Control engine orchestration
"""

from __future__ import annotations
