"""PID parameter management endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.validation import validate_pid_parameters
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class PIDParameterUpdate(BaseModel):
    """Request model for PID parameter update."""

    kp: float | None = None
    ki: float | None = None
    kd: float | None = None
    source: str = "api"  # 'api', 'config'
    updated_by: str | None = None


# Rate limiting storage (in-memory, per device_type)
_rate_limit_cache: dict[str, datetime] = {}
_rate_limit_window = 5  # seconds


def check_rate_limit(device_type: str) -> bool:
    """Check if PID parameter update is allowed (rate limiting).

    Args:
        device_type: Device type to check

    Returns:
        True if update is allowed, False if rate limited
    """
    now = datetime.now()
    last_update = _rate_limit_cache.get(device_type)

    if last_update is None:
        _rate_limit_cache[device_type] = now
        return True

    time_since_last = (now - last_update).total_seconds()
    if time_since_last >= _rate_limit_window:
        _rate_limit_cache[device_type] = now
        return True

    return False


def get_database() -> DatabaseManager:
    """Get database manager."""
    from app.main import container

    return container.get_database()


def get_config() -> ConfigLoader:
    """Get config loader."""
    from app.main import container

    return container.get_config()


@router.get("/api/pid/parameters")
async def get_all_pid_parameters(
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, Any]]:
    """Get all PID parameters for all device types.

    Returns:
        List of dicts with kp, ki, kd, updated_at, updated_by, source
    """
    return await database.pid_repo.get_all_pid_parameters()


@router.get("/api/pid/parameters/{device_type}")
async def get_pid_parameters(
    device_type: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any] | None:
    """Get PID parameters for a specific device type.

    Args:
        device_type: Device type (e.g., 'heater', 'co2')

    Returns:
        Dict with kp, ki, kd, updated_at, updated_by, source
    """
    params = await database.pid_repo.get_pid_parameters(device_type)
    if params is None:
        raise HTTPException(
            status_code=404, detail=f"PID parameters not found for device_type: {device_type}"
        )
    return params


@router.post("/api/pid/parameters/{device_type}")
async def update_pid_parameters(
    device_type: str,
    update: PIDParameterUpdate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any] | None:
    """Update PID parameters for a device type.

    Args:
        device_type: Device type (e.g., 'heater', 'co2')
        update: PID parameter update request

    Returns:
        Updated PID parameters
    """
    # Rate limiting check
    if not check_rate_limit(device_type):
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded. Maximum 1 update per {_rate_limit_window} seconds per device_type.",
        )

    # Get existing parameters to merge with update
    existing = await database.pid_repo.get_pid_parameters(device_type)

    # Determine which parameters to update
    kp = update.kp if update.kp is not None else (existing["kp"] if existing else None)
    ki = update.ki if update.ki is not None else (existing["ki"] if existing else None)
    kd = update.kd if update.kd is not None else (existing["kd"] if existing else None)

    # Validate parameters
    is_valid, error_message, validated = validate_pid_parameters(
        kp, ki, kd, device_type, config._config
    )

    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message)

    # If no parameters provided, return existing
    if not validated:
        if existing:
            return existing
        raise HTTPException(
            status_code=400, detail="No parameters provided and no existing parameters found"
        )

    # Merge validated parameters with existing (for partial updates)
    final_kp = validated.get("kp", existing["kp"] if existing else None)
    final_ki = validated.get("ki", existing["ki"] if existing else None)
    final_kd = validated.get("kd", existing["kd"] if existing else None)

    if final_kp is None or final_ki is None or final_kd is None:
        raise HTTPException(
            status_code=400,
            detail="All parameters (kp, ki, kd) must be provided for new device types",
        )

    # Update in database
    success = await database.pid_repo.set_pid_parameters(
        device_type,
        final_kp,
        final_ki,
        final_kd,
        source=update.source,
        updated_by=update.updated_by if update.updated_by else "system",
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update PID parameters")

    # Return updated parameters
    updated = await database.pid_repo.get_pid_parameters(device_type)
    return updated or {}


@router.get("/api/pid/parameters/{device_type}/history")
async def get_pid_parameter_history(
    device_type: str, limit: int = 100, database: DatabaseManager = Depends(get_database)
) -> list[dict[str, Any]]:
    """Get PID parameter change history for a device type.

    Args:
        device_type: Device type (e.g., 'heater', 'co2')
        limit: Maximum number of history entries to return (default: 100)

    Returns:
        List of history entries with timestamp, kp, ki, kd, updated_by, source
    """
    if limit < 1 or limit > 1000:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 1000")

    history = await database.pid_repo.get_pid_parameter_history(device_type, limit)
    return history


@router.post("/api/pid/parameters/{device_type}/reset")
async def reset_pid_parameters(
    device_type: str,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any] | None:
    """Reset PID parameters to config defaults.

    Args:
        device_type: Device type (e.g., 'heater', 'co2')

    Returns:
        Reset PID parameters from config
    """
    # Get default parameters from config
    device_config = config.get_pid_params_for_device(device_type)

    # config.get_pid_params_for_device returns {'kp': ..., 'ki': ..., 'kd': ...}
    kp = device_config.get("kp")
    ki = device_config.get("ki")
    kd = device_config.get("kd")

    if kp is None or ki is None or kd is None:
        # Fallback to hard defaults if somehow config is broken
        kp = 10.0
        ki = 0.01
        kd = 0.0

    # Update in database with config values
    success = await database.pid_repo.set_pid_parameters(
        device_type, kp, ki, kd, source="config_reset", updated_by="system"
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to reset PID parameters")

    logger.info(
        f"Reset PID parameters for {device_type} to config defaults: kp={kp}, ki={ki}, kd={kd}"
    )

    # Return reset parameters
    updated = await database.pid_repo.get_pid_parameters(device_type)
    return updated or {}


# ============================================================================
# PID Control Mode Endpoints
# ============================================================================


class PIDModeUpdate(BaseModel):
    """Request model for PID control mode update."""

    mode: str  # 'auto_pid', 'pid', 'on_off'
    hysteresis_high: float | None = None
    hysteresis_low: float | None = None
    updated_by: str | None = None


class PIDModeResponse(BaseModel):
    """Response model for PID control mode."""

    device_type: str
    mode: str
    hysteresis_high: float
    hysteresis_low: float
    autotune_active: bool
    updated_at: str | None = None


class AutotuneStatusResponse(BaseModel):
    """Response model for autotune status."""

    device_type: str
    is_active: bool
    status: str  # 'idle', 'running', 'calculating', 'complete', 'error'
    cycles_completed: int
    estimated_remaining_cycles: int
    current_ku: float | None = None
    current_tu: float | None = None
    suggested_kp: float | None = None
    suggested_ki: float | None = None
    suggested_kd: float | None = None
    last_change_reason: str | None = None


@router.get("/api/pid/mode/{device_type}")
async def get_pid_mode(
    device_type: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Get PID control mode for a device type.

    Args:
        device_type: Device type (e.g., 'heater', 'co2', 'fan')

    Returns:
        Dict with mode, hysteresis settings, and autotune status
    """
    mode_info = await database.pid_repo.get_pid_control_mode(device_type)
    if mode_info is None:
        raise HTTPException(
            status_code=404, detail=f"PID mode not found for device_type: {device_type}"
        )

    # Get autotune state to check if active
    autotune_state = await database.pid_repo.get_autotune_state(device_type)
    is_autotune_active = autotune_state.get("is_active", False) if autotune_state else False

    # Get updated_at from main PID parameters
    params = await database.pid_repo.get_pid_parameters(device_type)
    updated_at = params.get("updated_at") if params else None

    return {
        "device_type": device_type,
        "mode": mode_info["control_mode"],
        "hysteresis_high": mode_info["hysteresis_high"],
        "hysteresis_low": mode_info["hysteresis_low"],
        "autotune_active": is_autotune_active,
        "updated_at": str(updated_at) if updated_at else None,
    }


@router.post("/api/pid/mode/{device_type}")
async def set_pid_mode(
    device_type: str, update: PIDModeUpdate, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Set PID control mode for a device type.

    When setting mode to 'auto_pid', auto-tuning will start.
    When changing away from 'auto_pid', auto-tuning will stop.

    Args:
        device_type: Device type (e.g., 'heater', 'co2', 'fan')
        update: Mode update request

    Returns:
        Updated mode info
    """
    # Validate mode
    valid_modes = ("auto_pid", "pid", "on_off")
    if update.mode not in valid_modes:
        raise HTTPException(
            status_code=400, detail=f"Invalid mode: {update.mode}. Must be one of: {valid_modes}"
        )

    # Validate hysteresis if on_off mode
    if update.mode == "on_off":
        if update.hysteresis_high is not None and update.hysteresis_high <= 0:
            raise HTTPException(status_code=400, detail="hysteresis_high must be positive")
        if update.hysteresis_low is not None and update.hysteresis_low <= 0:
            raise HTTPException(status_code=400, detail="hysteresis_low must be positive")

    # Get current mode to detect changes
    current_mode_info = await database.pid_repo.get_pid_control_mode(device_type)
    current_mode = current_mode_info["control_mode"] if current_mode_info else "pid"

    # Update mode in database
    success = await database.pid_repo.set_pid_control_mode(
        device_type,
        update.mode,
        hysteresis_high=update.hysteresis_high,
        hysteresis_low=update.hysteresis_low,
        updated_by=update.updated_by if update.updated_by else "system",
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update PID mode")

    # Handle auto-tune state changes
    if update.mode == "auto_pid" and current_mode != "auto_pid":
        # Starting auto-tune
        await database.pid_repo.update_autotune_state(
            device_type, state="running", progress=0.0, current_step="initializing"
        )
        logger.info(f"Auto-tuning started for {device_type}")
    elif update.mode != "auto_pid" and current_mode == "auto_pid":
        # Stopping auto-tune
        await database.pid_repo.update_autotune_state(device_type, state="idle")
        logger.info(f"Auto-tuning stopped for {device_type}")

    # Return updated mode
    return await get_pid_mode(device_type, database)


@router.get("/api/pid/autotune/{device_type}/status")
async def get_autotune_status(
    device_type: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Get auto-tune status for a device type.

    Args:
        device_type: Device type (e.g., 'heater', 'co2', 'fan')

    Returns:
        Auto-tune status including progress, calculated values, and suggestions
    """
    state = await database.pid_repo.get_autotune_state(device_type)

    if state is None:
        # Return default idle state
        return {
            "device_type": device_type,
            "is_active": False,
            "status": "idle",
            "cycles_completed": 0,
            "estimated_remaining_cycles": 5,
            "current_ku": None,
            "current_tu": None,
            "suggested_kp": None,
            "suggested_ki": None,
            "suggested_kd": None,
            "last_change_reason": None,
        }

    # Estimate remaining cycles (typically need 5 total)
    total_cycles_needed = 5
    remaining = max(0, total_cycles_needed - (state.get("cycles_completed") or 0))

    return {
        "device_type": device_type,
        "is_active": state.get("is_active", False),
        "status": state.get("status", "idle"),
        "cycles_completed": state.get("cycles_completed", 0),
        "estimated_remaining_cycles": remaining,
        "current_ku": state.get("current_ku"),
        "current_tu": state.get("current_tu"),
        "suggested_kp": state.get("suggested_kp"),
        "suggested_ki": state.get("suggested_ki"),
        "suggested_kd": state.get("suggested_kd"),
        "last_change_reason": state.get("last_change_reason"),
    }


@router.post("/api/pid/autotune/{device_type}/stop")
async def stop_autotune(
    device_type: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Force stop auto-tuning for a device type.

    This will stop auto-tuning but keep the current K values.
    The mode will be changed to 'pid' (manual).

    Args:
        device_type: Device type (e.g., 'heater', 'co2', 'fan')

    Returns:
        Updated autotune status
    """
    # Update autotune state
    await database.pid_repo.update_autotune_state(device_type, state="idle")

    # Change mode to 'pid' (manual)
    await database.pid_repo.set_pid_control_mode(device_type, "pid", updated_by="system")

    logger.info(f"Auto-tuning force stopped for {device_type}, mode set to 'pid'")
    return await get_autotune_status(device_type, database)
