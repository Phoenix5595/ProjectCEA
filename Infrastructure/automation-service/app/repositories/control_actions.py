from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class ControlActionRepository(BaseRepository):
    """Repository for control action logging."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def log_control_action(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        old_state: bool,
        new_state: bool,
        mode: str = "auto",
        reason: str | None = None,
        sensor_value: float | None = None,
        setpoint: float | None = None
    ) -> bool:
        """Log a control action."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO control_actions 
                       (timestamp, location, cluster, device_name, channel, old_state, new_state,
                        mode, reason, sensor_value, setpoint)
                       VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
                    location, cluster, device_name, channel, old_state, new_state,
                    mode, reason, sensor_value, setpoint
                )
                return True
        except Exception as e:
            logger.error(f"Failed to log control action: {e}")
            return False

    async def log_automation_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_state: bool,
        device_mode: str,
        pid_output: float | None = None,
        duty_cycle_percent: float | None = None,
        active_rule_ids: list[int] | None = None,
        active_schedule_ids: list[int] | None = None,
        control_reason: str | None = None,
        schedule_ramp_up_duration: int | None = None,
        schedule_ramp_down_duration: int | None = None,
        schedule_photoperiod_hours: float | None = None,
        pid_kp: float | None = None,
        pid_ki: float | None = None,
        pid_kd: float | None = None
    ) -> bool:
        """Log automation state for a device."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO automation_state_log
                       (timestamp, location, cluster, device_name, device_state, device_mode,
                        pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids,
                        control_reason, schedule_ramp_up_duration, schedule_ramp_down_duration,
                        schedule_photoperiod_hours, pid_kp, pid_ki, pid_kd)
                       VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)""",
                    location, cluster, device_name, device_state, device_mode,
                    pid_output, duty_cycle_percent, active_rule_ids, active_schedule_ids,
                    control_reason, schedule_ramp_up_duration, schedule_ramp_down_duration,
                    schedule_photoperiod_hours, pid_kp, pid_ki, pid_kd
                )
                return True
        except Exception as e:
            logger.error(f"Failed to log automation state: {e}")
            return False

    async def log_config_version(
        self,
        config_type: str,
        author: str = "system",
        comment: str | None = None,
        location: str | None = None,
        cluster: str | None = None,
        changes: dict[str, Any] | None = None
    ) -> int | None:
        """Log a config version change."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO config_versions 
                       (config_type, author, comment, location, cluster, changes, created_at)
                       VALUES ($1, $2, $3, $4, $5, $6, NOW())
                       RETURNING id""",
                    config_type, author, comment, location, cluster, 
                    str(changes) if changes else None
                )
                return row["id"] if row else None
        except Exception as e:
            logger.error(f"Failed to log config version: {e}")
            return None

    async def log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        mode: str,
        effective_heating_setpoint: float,
        effective_cooling_setpoint: float,
        effective_humidity_setpoint: float,
        effective_co2_setpoint: float,
        effective_vpd_setpoint: float,
        nominal_heating_setpoint: float,
        nominal_cooling_setpoint: float,
        nominal_humidity_setpoint: float,
        nominal_co2_setpoint: float,
        nominal_vpd_setpoint: float,
        ramp_progress_heating: float,
        ramp_progress_cooling: float,
        ramp_progress_humidity: float,
        ramp_progress_co2: float,
        ramp_progress_vpd: float,
        device_name: str = "main",
        effective_light_intensity: float | None = None,
        nominal_light_intensity: float | None = None,
        ramp_progress_light: float | None = None,
        timestamp: datetime | None = None
    ) -> bool:
        """Log effective setpoints."""
        try:
            ts = timestamp or datetime.now()
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO effective_setpoints
                       (timestamp, location, cluster, device_name, mode,
                        effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
                        effective_co2_setpoint, effective_vpd_setpoint,
                        nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
                        nominal_co2_setpoint, nominal_vpd_setpoint,
                        ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
                        ramp_progress_co2, ramp_progress_vpd,
                        effective_light_intensity, nominal_light_intensity, ramp_progress_light)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)""",
                    ts, location, cluster, device_name, mode,
                    effective_heating_setpoint, effective_cooling_setpoint, effective_humidity_setpoint,
                    effective_co2_setpoint, effective_vpd_setpoint,
                    nominal_heating_setpoint, nominal_cooling_setpoint, nominal_humidity_setpoint,
                    nominal_co2_setpoint, nominal_vpd_setpoint,
                    ramp_progress_heating, ramp_progress_cooling, ramp_progress_humidity,
                    ramp_progress_co2, ramp_progress_vpd,
                    effective_light_intensity, nominal_light_intensity, ramp_progress_light
                )
                return True
        except Exception as e:
            logger.error(f"Failed to log effective setpoints: {e}")
            return False
