from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.state import StateManager, get_state_manager  # type: ignore

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


class PIDRepository(BaseRepository):
    """Repository for PID parameter operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_pid_parameters(self, device_type: str) -> dict[str, Any] | None:
        """Get PID parameters for a device type with cache-aside (StateManager)."""
        # 1) Check StateManager cache first
        state: StateManager | None = None
        try:
            state = get_state_manager()
            if state is not None:
                cached = await state.get_pid_params(device_type)
                if cached is not None:
                    return dict(cached)
        except Exception as e:
            logger.debug(f"StateManager unavailable for pid.get_pid_parameters: {e}")

        # 2) DB lookup on cache miss
        try:
            state: StateManager | None = get_state_manager()
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT device_type, kp, ki, kd, control_mode, hysteresis_high, hysteresis_low,
                              source, updated_by, updated_at
                       FROM pid_parameters WHERE device_type = $1""",
                    device_type,
                )
                if row:
                    result = dict(row)
                    # 3) Populate cache in StateManager for future fast reads
                    try:
                        if state is not None:
                            kp = result.get("kp")
                            ki = result.get("ki")
                            kd = result.get("kd")
                            if kp is not None and ki is not None and kd is not None:
                                await state.set_pid_params(device_type, kp, ki, kd, source="db")
                    except Exception as e:
                        logger.debug(f"PID cache populate failed for {device_type}: {e}")
                    return result
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
        updated_by: str = "system",
    ) -> bool:
        """Set PID parameters."""
        try:
            async with self.pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT kp, ki, kd FROM pid_parameters WHERE device_type = $1", device_type
                )
                await conn.execute(
                    """INSERT INTO pid_parameters (device_type, kp, ki, kd, source, updated_by, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, NOW())
                       ON CONFLICT (device_type)
                       DO UPDATE SET kp = $2, ki = $3, kd = $4, source = $5, updated_by = $6, updated_at = NOW()""",
                    device_type,
                    kp,
                    ki,
                    kd,
                    source,
                    updated_by,
                )
                if existing:
                    await conn.execute(
                        """INSERT INTO pid_parameter_history
                           (device_type, kp, ki, kd, source, updated_by, changed_at)
                           VALUES ($1, $2, $3, $4, $5, $6, NOW())""",
                        device_type,
                        existing["kp"],
                        existing["ki"],
                        existing["kd"],
                        source,
                        updated_by,
                    )
                    # Invalidate PID parameter caches on write
                    try:
                        state = get_state_manager()
                        if state is not None:
                            await state.delete(f"pid:parameters:{device_type}")
                            await state.delete("pid:parameters:all")
                    except Exception as e:
                        logger.debug(f"PID cache invalidation failed for {device_type}: {e}")
                return True
        except Exception as e:
            logger.error(f"Failed to set PID parameters: {e}")
            return False

    async def get_pid_parameter_history(
        self, device_type: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get PID parameter history."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT device_type, kp, ki, kd, source, updated_by, changed_at
                       FROM pid_parameter_history
                       WHERE device_type = $1
                       ORDER BY changed_at DESC LIMIT $2""",
                    device_type,
                    limit,
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get PID history: {e}")
            return []

    async def get_all_pid_parameters(self) -> list[dict[str, Any]]:
        """Get all PID parameters with cache-aside support."""
        # Try StateManager cache first
        try:
            state = get_state_manager()
            if state is not None:
                cached_all = await state.get("pid:parameters:all")
                if cached_all is not None:
                    data = cached_all
                    if isinstance(data, (bytes, bytearray)):
                        data = data.decode()
                    if isinstance(data, str):
                        try:
                            data_list = json.loads(data)
                            if isinstance(data_list, list):
                                return data_list
                        except Exception as e:
                            logger.debug(f"PID cache JSON decode failed (treating as miss): {e}")
                    if isinstance(data, list):
                        return data
        except Exception as e:
            logger.debug(f"PID get_all cache lookup failed (falling back to DB): {e}")

        # DB lookup on miss
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT device_type, kp, ki, kd, control_mode, hysteresis_high, hysteresis_low,
                              source, updated_by, updated_at
                       FROM pid_parameters ORDER BY device_type"""
                )
                data = [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all PID parameters: {e}")
            return []

        # Populate cache
        try:
            state = get_state_manager()
            if state is not None:
                await state.set("pid:parameters:all", json.dumps(data), ttl=300)
        except Exception as e:
            logger.debug(f"PID get_all cache populate failed: {e}")

        return data

    async def get_pid_control_mode(self, device_type: str) -> dict[str, Any] | None:
        """Get PID control mode and hysteresis for device type."""
        # 1) Try StateManager cache first
        try:
            state = get_state_manager()
            if state is not None:
                cached = await state.get_pid_params(device_type)
                if isinstance(cached, dict):
                    return {
                        "control_mode": cached.get("control_mode"),
                        "hysteresis_high": cached.get("hysteresis_high"),
                        "hysteresis_low": cached.get("hysteresis_low"),
                    }
        except Exception as e:
            logger.debug(f"PID control-mode cache lookup failed for {device_type}: {e}")

        # 2) DB lookup on cache miss
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT control_mode, hysteresis_high, hysteresis_low FROM pid_parameters WHERE device_type = $1",
                    device_type,
                )
                if row:
                    return {
                        "control_mode": row["control_mode"],
                        "hysteresis_high": row["hysteresis_high"],
                        "hysteresis_low": row["hysteresis_low"],
                    }
        except Exception as e:
            logger.error(f"Failed to get control mode: {e}")
        return None

    async def set_pid_control_mode(
        self,
        device_type: str,
        control_mode: str,
        hysteresis_high: float | None = None,
        hysteresis_low: float | None = None,
        updated_by: str = "system",
    ) -> bool:
        """Set PID control mode."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """UPDATE pid_parameters
                       SET control_mode = $2, hysteresis_high = COALESCE($3, hysteresis_high),
                           hysteresis_low = COALESCE($4, hysteresis_low), updated_by = $5, updated_at = NOW()
                       WHERE device_type = $1""",
                    device_type,
                    control_mode,
                    hysteresis_high,
                    hysteresis_low,
                    updated_by,
                )
                # Invalidate caches on write
                try:
                    state = get_state_manager()
                    if state is not None:
                        await state.delete(f"pid:parameters:{device_type}")
                        await state.delete("pid:parameters:all")
                except Exception as e:
                    logger.debug(
                        f"PID control-mode cache invalidation failed for {device_type}: {e}"
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to set control mode: {e}")
            return False

    async def get_autotune_state(self, device_type: str) -> dict[str, Any] | None:
        """Get autotune state for device type with cache-aside."""
        state: StateManager | None = None
        # Check cache first via StateManager
        try:
            state = get_state_manager()
            if state is not None:
                cached = await state.get_autotune_state(device_type)
                if cached is not None:
                    return dict(cached)
        except Exception as e:
            logger.debug(f"Autotune cache lookup failed for {device_type}: {e}")
        # DB lookup
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT * FROM pid_autotune_state WHERE device_type = $1""", device_type
                )
                if row:
                    result = dict(row)
                    # Cache result
                    try:
                        if state is not None:
                            await state.set_autotune_state(device_type, result, ttl=300)
                    except Exception as e:
                        logger.debug(f"Autotune cache populate failed for {device_type}: {e}")
                    return result
        except Exception as e:
            logger.error(f"Failed to get autotune state: {e}")
        return None

    async def update_autotune_state(self, device_type: str, **kwargs: Any) -> bool:
        """Update autotune state with provided fields."""
        if not kwargs:
            return True

        # Map API parameter names to actual database column names
        column_mapping = {
            "state": "status",  # API uses 'state', DB has 'status'
        }

        # Known columns in pid_autotune_state table
        valid_columns = {
            "is_active",
            "status",
            "cycles_completed",
            "started_at",
            "current_amplitude",
            "current_period",
            "current_ku",
            "current_tu",
            "suggested_kp",
            "suggested_ki",
            "suggested_kd",
            "last_change_reason",
            "last_update",
        }

        try:
            async with self.pool.acquire() as conn:
                updates = []
                params = [device_type]
                param_idx = 2

                for key, value in kwargs.items():
                    if value is not None:
                        # Map parameter name to column name
                        column_name = column_mapping.get(key, key)
                        # Skip unknown columns
                        if column_name not in valid_columns:
                            continue
                        updates.append(f"{column_name} = ${param_idx}")
                        params.append(value)
                        param_idx += 1

                if updates:
                    update_clause = ", ".join(updates)
                    await conn.execute(
                        f"UPDATE pid_autotune_state SET {update_clause}, last_update = NOW() WHERE device_type = $1",
                        *params,
                    )
                # Invalidate autotune cache on write
                try:
                    st = get_state_manager()
                    if st is not None:
                        await st.delete(f"pid:autotune:{device_type}")
                except Exception as e:
                    logger.debug(f"Autotune cache invalidation failed for {device_type}: {e}")
                return True
        except Exception as e:
            logger.error(f"Failed to update autotune state: {e}")
            return False

    async def set_pid_parameters_with_reason(
        self,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        change_reason: str,
        source: str = "auto_pid",
        updated_by: str | None = None,
    ) -> bool:
        """Set PID parameters with a change reason (for auto-tuning).

        Args:
            device_type: Device type (e.g., 'heater', 'co2')
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            change_reason: Explanation for why values changed
            source: Source of update ('auto_pid', 'api', 'config')
            updated_by: Optional identifier of who made the update

        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                # Get existing parameters for history
                existing = await conn.fetchrow(
                    "SELECT kp, ki, kd FROM pid_parameters WHERE device_type = $1", device_type
                )

                # Update or insert PID parameters
                await conn.execute(
                    """
                    INSERT INTO pid_parameters (device_type, kp, ki, kd, updated_at, updated_by, source)
                    VALUES ($1, $2, $3, $4, NOW(), $5, $6)
                    ON CONFLICT (device_type)
                    DO UPDATE SET
                        kp = EXCLUDED.kp,
                        ki = EXCLUDED.ki,
                        kd = EXCLUDED.kd,
                        updated_at = NOW(),
                        updated_by = EXCLUDED.updated_by,
                        source = EXCLUDED.source
                """,
                    device_type,
                    kp,
                    ki,
                    kd,
                    updated_by,
                    source,
                )

                # Log to history with change reason if values changed
                if (
                    existing is None
                    or existing["kp"] != kp
                    or existing["ki"] != ki
                    or existing["kd"] != kd
                ):
                    await conn.execute(
                        """
                        INSERT INTO pid_parameter_history (timestamp, device_type, kp, ki, kd, updated_by, source, change_reason)
                        VALUES (NOW(), $1, $2, $3, $4, $5, $6, $7)
                    """,
                        device_type,
                        kp,
                        ki,
                        kd,
                        updated_by,
                        source,
                        change_reason,
                    )
                    logger.info(
                        f"PID parameters updated for {device_type}: Kp={kp}, Ki={ki}, Kd={kd} (reason: {change_reason})"
                    )

                # Invalidate caches on write
                try:
                    state = get_state_manager()
                    if state is not None:
                        await state.delete(f"pid:parameters:{device_type}")
                        await state.delete("pid:parameters:all")
                except Exception as e:
                    logger.debug(
                        f"PID set-with-reason cache invalidation failed for {device_type}: {e}"
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to set PID parameters with reason: {e}")
            return False
