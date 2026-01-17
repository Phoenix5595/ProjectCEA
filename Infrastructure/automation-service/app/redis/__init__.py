"""Redis client module for automation service.

This module provides the AutomationRedisClient class which combines
all Redis functionality through mixins for maintainability.

Usage:
    from app.redis import AutomationRedisClient
    # or for backward compatibility:
    from app.redis_client import AutomationRedisClient
"""
from __future__ import annotations

from .base import RedisConnectionMixin
from .streams import StreamsMixin
from .setpoints import SetpointsMixin
from .modes import ModesMixin
from .failsafe import FailsafeMixin
from .alarms import AlarmsMixin
from .heartbeat import HeartbeatMixin
from .sensors import SensorsMixin
from .pid import PIDMixin
from .lighting import LightingMixin
from .ramps import RampsMixin
from .schedules import SchedulesMixin


class AutomationRedisClient(
    RedisConnectionMixin,
    StreamsMixin,
    SetpointsMixin,
    ModesMixin,
    FailsafeMixin,
    AlarmsMixin,
    HeartbeatMixin,
    SensorsMixin,
    PIDMixin,
    LightingMixin,
    RampsMixin,
    SchedulesMixin,
):
    """Combined Redis client for automation service.
    
    Provides all Redis functionality for the automation control loop:
    - Connection management (connect, close)
    - Stream writes (sensor data, control data)
    - Setpoint management (read/write setpoints, effective setpoints)
    - Mode management (auto, manual, failsafe)
    - Failsafe state
    - Alarms
    - Heartbeats
    - Sensor last-good values
    - PID parameter cache
    - Light intensity
    - Ramp state
    - Schedule state
    """
    
    def __init__(self, redis_url: str | None = None, redis_ttl: int = 10) -> None:
        self._init_connection(redis_url, redis_ttl)


__all__ = ['AutomationRedisClient']
