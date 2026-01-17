from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class PIDRepository(BaseRepository):
    """Repository for PID parameter operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_pid_parameters(self, device_type: str) -> dict[str, Any] | None:
        """Get PID parameters for a device type."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT device_type, kp, ki, kd, control_mode, hysteresis_high, hysteresis_low,
                              source, updated_by, updated_at
                       FROM pid_parameters WHERE device_type = $1""",
                    device_type
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get PID parameters: {e}")
        return None

    async def set_pid_parameters(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        source: str = "manual",
        updated_by: str = "system"
    ) -> bool:
        """Set PID parameters."""
        try:
            async with self.pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT kp, ki, kd FROM pid_parameters WHERE device_type = $1",
                    device_type
                )
                await conn.execute(
                    """INSERT INTO pid_parameters (device_type, kp, ki, kd, source, updated_by, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, NOW())
                       ON CONFLICT (device_type)
                       DO UPDATE SET kp = $2, ki = $3, kd = $4, source = $5, updated_by = $6, updated_at = NOW()""",
                    device_type, kp, ki, kd, source, updated_by
                )
                if existing:
                    await conn.execute(
                        """INSERT INTO pid_parameter_history 
                           (device_type, kp, ki, kd, source, updated_by, changed_at)
                           VALUES ($1, $2, $3, $4, $5, $6, NOW())""",
                        device_type, existing["kp"], existing["ki"], existing["kd"], source, updated_by
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to set PID parameters: {e}")
            return False

    async def get_pid_parameter_history(self, device_type: str, limit: int = 10) -> list[dict[str, Any]]:
        """Get PID parameter history."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT device_type, kp, ki, kd, source, updated_by, changed_at
                       FROM pid_parameter_history
                       WHERE device_type = $1
                       ORDER BY changed_at DESC LIMIT $2""",
                    device_type, limit
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get PID history: {e}")
            return []

    async def get_all_pid_parameters(self) -> list[dict[str, Any]]:
        """Get all PID parameters."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT device_type, kp, ki, kd, control_mode, hysteresis_high, hysteresis_low,
                              source, updated_by, updated_at
                       FROM pid_parameters ORDER BY device_type"""
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all PID parameters: {e}")
            return []

    async def get_pid_control_mode(self, device_type: str) -> str | None:
        """Get PID control mode for device type."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT control_mode FROM pid_parameters WHERE device_type = $1",
                    device_type
                )
                if row:
                    return row["control_mode"]
        except Exception as e:
            logger.error(f"Failed to get control mode: {e}")
        return None

    async def set_pid_control_mode(
        self,
        device_type: str,
        control_mode: str,
        hysteresis_high: float | None = None,
        hysteresis_low: float | None = None,
        updated_by: str = "system"
    ) -> bool:
        """Set PID control mode."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """UPDATE pid_parameters 
                       SET control_mode = $2, hysteresis_high = COALESCE($3, hysteresis_high),
                           hysteresis_low = COALESCE($4, hysteresis_low), updated_by = $5, updated_at = NOW()
                       WHERE device_type = $1""",
                    device_type, control_mode, hysteresis_high, hysteresis_low, updated_by
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set control mode: {e}")
            return False

    async def get_autotune_state(self, device_type: str) -> dict[str, Any] | None:
        """Get autotune state for device type."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM pid_autotune_state WHERE device_type = $1""",
                    device_type
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get autotune state: {e}")
        return None

    async def update_autotune_state(self, device_type: str, **kwargs: Any) -> bool:
        """Update autotune state with provided fields."""
        if not kwargs:
            return True

        try:
            async with self.pool.acquire() as conn:
                updates = []
                params = [device_type]
                param_idx = 2

                for key, value in kwargs.items():
                    if value is not None:
                        updates.append(f"{key} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                if updates:
                    update_clause = ", ".join(updates)
                    await conn.execute(
                        f"UPDATE pid_autotune_state SET {update_clause}, updated_at = NOW() WHERE device_type = $1",
                        *params
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to update autotune state: {e}")
            return False
