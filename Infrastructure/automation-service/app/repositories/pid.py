from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.state import StateManager, get_state_manager  # type: ignore

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool


DEFAULT_PID_PARAMS: dict[str, Any] = {
    "kp": 1.0,
    "ki": 0.0,
    "kd": 0.0,
    "control_mode": "pid",
    "binary_hysteresis": 0.1,
    "hysteresis_high": None,
    "hysteresis_low": None,
    "source": "default",
    "updated_by": "system",
    "updated_at": None,
}


class PIDRepository(BaseRepository):
    """Repository for PID parameter operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    def _default_pid_row(self, location: str, cluster: str, device_type: str) -> dict[str, Any]:
        """Return a default PID parameter row for missing entries."""
        return {
            "location": location,
            "cluster": cluster,
            "device_type": device_type,
            **DEFAULT_PID_PARAMS,
        }

    async def get_pid_parameters(
        self, location: str, cluster: str, device_type: str
    ) -> dict[str, Any]:
        """Get PID parameters for a location/cluster/device_type with cache-aside (StateManager)."""
        # 1) Check StateManager cache first
        state: StateManager | None = None
        try:
            state = get_state_manager()
            if state is not None:
                cached = await state.get_pid_params(location, cluster, device_type)
                if cached is not None:
                    return dict(cached)
        except Exception as e:
            logger.debug(f"StateManager unavailable for pid.get_pid_parameters: {e}")

        # 2) DB lookup on cache miss
        try:
            state = get_state_manager()
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, device_type, kp, ki, kd, control_mode,
                               binary_hysteresis, hysteresis_high, hysteresis_low,
                               source, updated_by, updated_at
                        FROM pid_parameters
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
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
                            binary_hysteresis = result.get("binary_hysteresis")
                            if kp is not None and ki is not None and kd is not None:
                                await state.set_pid_params(
                                    location,
                                    cluster,
                                    device_type,
                                    kp,
                                    ki,
                                    kd,
                                    binary_hysteresis=binary_hysteresis,
                                    source="db",
                                )
                    except Exception as e:
                        logger.debug(f"PID cache populate failed for {device_type}: {e}")
                    return result
        except Exception as e:
            logger.error(f"Failed to get PID parameters: {e}")
        # 4) Return defaults on missing row or error
        return self._default_pid_row(location, cluster, device_type)

    async def set_pid_parameters(
        self,
        location: str,
        cluster: str,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        binary_hysteresis: float | None = None,
        source: str = "manual",
        updated_by: str = "system",
    ) -> bool:
        """Set PID parameters."""
        try:
            async with self.pool.acquire() as conn:
                existing = await conn.fetchrow(
                    """SELECT kp, ki, kd, binary_hysteresis
                        FROM pid_parameters
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
                    device_type,
                )
                await conn.execute(
                    """INSERT INTO pid_parameters
                        (location, cluster, device_type, kp, ki, kd, binary_hysteresis, source, updated_by, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                        ON CONFLICT (location, cluster, device_type)
                        DO UPDATE SET kp = $4, ki = $5, kd = $6, binary_hysteresis = COALESCE($7, EXCLUDED.binary_hysteresis),
                                      source = $8, updated_by = $9, updated_at = NOW()""",
                    location,
                    cluster,
                    device_type,
                    kp,
                    ki,
                    kd,
                    binary_hysteresis,
                    source,
                    updated_by,
                )
                if existing:
                    await conn.execute(
                        """INSERT INTO pid_parameter_history
                            (location, cluster, device_type, kp, ki, kd, binary_hysteresis, source, updated_by, changed_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())""",
                        location,
                        cluster,
                        device_type,
                        existing["kp"],
                        existing["ki"],
                        existing["kd"],
                        existing.get("binary_hysteresis"),
                        source,
                        updated_by,
                    )
                    # Invalidate PID parameter caches on write
                    try:
                        state = get_state_manager()
                        if state is not None:
                            await state.delete(f"pid:parameters:{location}:{cluster}:{device_type}")
                            await state.delete("pid:parameters:all")
                    except Exception as e:
                        logger.debug(f"PID cache invalidation failed for {device_type}: {e}")
                return True
        except Exception as e:
            logger.error(f"Failed to set PID parameters: {e}")
            return False

    async def get_pid_parameter_history(
        self, location: str, cluster: str, device_type: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Get PID parameter history."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT location, cluster, device_type, kp, ki, kd, binary_hysteresis,
                               source, updated_by, changed_at
                        FROM pid_parameter_history
                        WHERE location = $1 AND cluster = $2 AND device_type = $3
                        ORDER BY changed_at DESC LIMIT $4""",
                    location,
                    cluster,
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
                    """SELECT location, cluster, device_type, kp, ki, kd, control_mode,
                               binary_hysteresis, hysteresis_high, hysteresis_low,
                               source, updated_by, updated_at
                        FROM pid_parameters
                        ORDER BY location, cluster, device_type"""
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

    async def get_pid_control_mode(
        self, location: str, cluster: str, device_type: str
    ) -> dict[str, Any]:
        """Get PID control mode and hysteresis for device type."""
        # 1) Try StateManager cache first
        try:
            state = get_state_manager()
            if state is not None:
                cached = await state.get_pid_params(location, cluster, device_type)
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
                    """SELECT control_mode, hysteresis_high, hysteresis_low
                        FROM pid_parameters
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
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
        # 3) Return defaults on missing row or error
        return {
            "control_mode": DEFAULT_PID_PARAMS["control_mode"],
            "hysteresis_high": DEFAULT_PID_PARAMS["hysteresis_high"],
            "hysteresis_low": DEFAULT_PID_PARAMS["hysteresis_low"],
        }

    async def set_pid_control_mode(
        self,
        location: str,
        cluster: str,
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
                        SET control_mode = $4, hysteresis_high = COALESCE($5, hysteresis_high),
                            hysteresis_low = COALESCE($6, hysteresis_low), updated_by = $7, updated_at = NOW()
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
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
                        await state.delete(f"pid:parameters:{location}:{cluster}:{device_type}")
                        await state.delete("pid:parameters:all")
                except Exception as e:
                    logger.debug(
                        f"PID control-mode cache invalidation failed for {device_type}: {e}"
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to set control mode: {e}")
            return False

    async def get_binary_hysteresis(
        self, location: str, cluster: str, device_type: str
    ) -> dict[str, Any]:
        """Get binary hysteresis for a location/cluster/device_type.

        Returns the stored binary_hysteresis value, falling back to the
        default (0.1) when no row exists.
        """
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT binary_hysteresis FROM pid_parameters
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
                    device_type,
                )
                if row and row["binary_hysteresis"] is not None:
                    return {
                        "location": location,
                        "cluster": cluster,
                        "device_type": device_type,
                        "binary_hysteresis": row["binary_hysteresis"],
                    }
        except Exception as e:
            logger.error(f"Failed to get binary hysteresis: {e}")
        return {
            "location": location,
            "cluster": cluster,
            "device_type": device_type,
            "binary_hysteresis": DEFAULT_PID_PARAMS["binary_hysteresis"],
        }

    async def get_autotune_state(
        self, location: str, cluster: str, device_type: str
    ) -> dict[str, Any] | None:
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
                    """SELECT * FROM pid_autotune_state
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
                    device_type,
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

    async def update_autotune_state(
        self, location: str, cluster: str, device_type: str, **kwargs: Any
    ) -> bool:
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
                params = [location, cluster, device_type]
                param_idx = 4

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
                        f"UPDATE pid_autotune_state SET {update_clause}, last_update = NOW() WHERE location = $1 AND cluster = $2 AND device_type = $3",
                        *params,
                    )
                # Invalidate autotune cache on write
                try:
                    st = get_state_manager()
                    if st is not None:
                        await st.delete(f"pid:autotune:{location}:{cluster}:{device_type}")
                except Exception as e:
                    logger.debug(f"Autotune cache invalidation failed for {device_type}: {e}")
                return True
        except Exception as e:
            logger.error(f"Failed to update autotune state: {e}")
            return False

    async def set_pid_parameters_with_reason(
        self,
        location: str,
        cluster: str,
        device_type: str,
        kp: float,
        ki: float,
        kd: float,
        change_reason: str,
        binary_hysteresis: float | None = None,
        source: str = "auto_pid",
        updated_by: str | None = None,
    ) -> bool:
        """Set PID parameters with a change reason (for auto-tuning).

        Args:
            location: Room/location name
            cluster: Cluster name
            device_type: Device type (e.g., 'heater', 'co2')
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            change_reason: Explanation for why values changed
            binary_hysteresis: Optional binary hysteresis value
            source: Source of update ('auto_pid', 'api', 'config')
            updated_by: Optional identifier of who made the update

        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                # Get existing parameters for history
                existing = await conn.fetchrow(
                    """SELECT kp, ki, kd, binary_hysteresis
                        FROM pid_parameters
                        WHERE location = $1 AND cluster = $2 AND device_type = $3""",
                    location,
                    cluster,
                    device_type,
                )

                # Update or insert PID parameters
                await conn.execute(
                    """
                    INSERT INTO pid_parameters
                        (location, cluster, device_type, kp, ki, kd, binary_hysteresis, updated_at, updated_by, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), $8, $9)
                    ON CONFLICT (location, cluster, device_type)
                    DO UPDATE SET
                        kp = EXCLUDED.kp,
                        ki = EXCLUDED.ki,
                        kd = EXCLUDED.kd,
                        binary_hysteresis = COALESCE(EXCLUDED.binary_hysteresis, pid_parameters.binary_hysteresis),
                        updated_at = NOW(),
                        updated_by = EXCLUDED.updated_by,
                        source = EXCLUDED.source
                """,
                    location,
                    cluster,
                    device_type,
                    kp,
                    ki,
                    kd,
                    binary_hysteresis,
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
                        INSERT INTO pid_parameter_history
                            (location, cluster, device_type, timestamp, kp, ki, kd, binary_hysteresis, updated_by, source, change_reason)
                        VALUES ($1, $2, $3, NOW(), $4, $5, $6, $7, $8, $9, $10)
                    """,
                        location,
                        cluster,
                        device_type,
                        kp,
                        ki,
                        kd,
                        existing.get("binary_hysteresis") if existing else None,
                        updated_by,
                        source,
                        change_reason,
                    )
                    logger.info(
                        f"PID parameters updated for {location}/{cluster}/{device_type}: Kp={kp}, Ki={ki}, Kd={kd} (reason: {change_reason})"
                    )

                # Invalidate caches on write
                try:
                    state = get_state_manager()
                    if state is not None:
                        await state.delete(f"pid:parameters:{location}:{cluster}:{device_type}")
                        await state.delete("pid:parameters:all")
                except Exception as e:
                    logger.debug(
                        f"PID set-with-reason cache invalidation failed for {device_type}: {e}"
                    )
                return True
        except Exception as e:
            logger.error(f"Failed to set PID parameters with reason: {e}")
            return False
