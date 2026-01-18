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
        old_state: int | None,
        new_state: int | None,
        mode: str = "auto",
        reason: str | None = None,
        sensor_value: float | None = None,
        setpoint: float | None = None
    ) -> bool:
        """Log a control action to control_history table."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO control_history 
                    (timestamp, location, cluster, device_name, channel, old_state, new_state, mode, reason, sensor_value, setpoint)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """, location, cluster, device_name, channel, old_state, new_state, mode, reason, sensor_value, setpoint)
                return True
        except Exception as e:
            logger.error(f"Failed to log control action: {e}")
            return False

    async def log_automation_state(
        self,
        location: str,
        cluster: str,
        state: str,
        reason: str | None = None
    ) -> bool:
        """Log automation state change."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO automation_state_history (timestamp, location, cluster, state, reason)
                    VALUES (NOW(), $1, $2, $3, $4)
                """, location, cluster, state, reason)
                return True
        except Exception as e:
            logger.error(f"Failed to log automation state: {e}")
            return False

    async def log_config_version(
        self,
        location: str,
        cluster: str,
        config_type: str,
        version: int,
        config_hash: str | None = None,
        changed_by: str | None = None
    ) -> bool:
        """Log configuration version change."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO config_versions (timestamp, location, cluster, config_type, version, config_hash, changed_by)
                    VALUES (NOW(), $1, $2, $3, $4, $5, $6)
                """, location, cluster, config_type, version, config_hash, changed_by)
                return True
        except Exception as e:
            logger.error(f"Failed to log config version: {e}")
            return False

    async def log_effective_setpoints(
        self,
        location: str,
        cluster: str,
        mode: str | None,
        effective_heating_setpoint: float | None = None,
        effective_cooling_setpoint: float | None = None,
        effective_humidity_setpoint: float | None = None,
        effective_co2_setpoint: float | None = None,
        effective_vpd_setpoint: float | None = None,
        nominal_heating_setpoint: float | None = None,
        nominal_cooling_setpoint: float | None = None,
        nominal_humidity_setpoint: float | None = None,
        nominal_co2_setpoint: float | None = None,
        nominal_vpd_setpoint: float | None = None,
        ramp_progress_heating: float | None = None,
        ramp_progress_cooling: float | None = None,
        ramp_progress_humidity: float | None = None,
        ramp_progress_co2: float | None = None,
        ramp_progress_vpd: float | None = None,
        device_name: str | None = None,
        effective_light_intensity: float | None = None,
        nominal_light_intensity: float | None = None,
        ramp_progress_light: float | None = None,
        timestamp: datetime | None = None
    ) -> bool:
        """Log effective setpoints to effective_setpoints table."""
        try:
            async with self.pool.acquire() as conn:
                ts = timestamp or datetime.now()
                await conn.execute("""
                    INSERT INTO effective_setpoints (
                        timestamp, location, cluster, mode, device_name,
                        effective_heating_setpoint, effective_cooling_setpoint,
                        effective_humidity_setpoint, effective_co2_setpoint, effective_vpd_setpoint,
                        effective_light_intensity,
                        nominal_heating_setpoint, nominal_cooling_setpoint,
                        nominal_humidity_setpoint, nominal_co2_setpoint, nominal_vpd_setpoint,
                        nominal_light_intensity,
                        ramp_progress_heating, ramp_progress_cooling,
                        ramp_progress_humidity, ramp_progress_co2, ramp_progress_vpd,
                        ramp_progress_light
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)
                """, ts, location, cluster, mode, device_name,
                    effective_heating_setpoint, effective_cooling_setpoint,
                    effective_humidity_setpoint, effective_co2_setpoint, effective_vpd_setpoint,
                    effective_light_intensity,
                    nominal_heating_setpoint, nominal_cooling_setpoint,
                    nominal_humidity_setpoint, nominal_co2_setpoint, nominal_vpd_setpoint,
                    nominal_light_intensity,
                    ramp_progress_heating, ramp_progress_cooling,
                    ramp_progress_humidity, ramp_progress_co2, ramp_progress_vpd,
                    ramp_progress_light
                )
                return True
        except Exception as e:
            logger.error(f"Failed to log effective setpoints: {e}")
            return False
