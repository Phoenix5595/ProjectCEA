from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from shared.infra_logging import get_logger

from ..control.control_engine import ControlEngine
from ..control.scheduler import Scheduler
from ..database import DatabaseManager

logger = get_logger(__name__)
router = APIRouter(prefix="/api/debug", tags=["debug"])


def get_database() -> DatabaseManager:
    from ..main import container

    return container.get_database()


def get_scheduler() -> Scheduler:
    from ..main import container

    return container.get_scheduler()


def get_control_engine() -> ControlEngine:
    from ..main import container

    return container.get_control_engine()


@router.get("/mode-state/{location}/{cluster}")
async def get_mode_state(
    location: str,
    cluster: str,
    db: DatabaseManager = Depends(get_database),
    control_engine: ControlEngine = Depends(get_control_engine),
):
    """
    Returns current mode state from all sources for comparison:
    - room_active_mode table (UI mode)
    - climate_periods active period
    - mode_parameters for current mode
    - effective setpoints from memory
    """
    try:
        active_mode = await db.room_mode_repo.get_active_mode(location, cluster)

        climate_periods_key = (location, cluster)
        derived_mode = control_engine._current_climate_mode.get(climate_periods_key)

        active_period = None
        if derived_mode:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            tz = ZoneInfo("America/Toronto")
            now = datetime.now(tz)
            time_str = f"{now.hour:02d}:{now.minute:02d}"
            active_period = await db.climate_periods_repo.get_active_period(
                location, cluster, time_str
            )

        mode_params = None
        if active_mode:
            mode_params = await db.room_mode_repo.get_mode_parameters(
                location,
                cluster,
                active_mode["mode_name"],
                active_mode.get("submode_name"),
            )

        effective_setpoints = control_engine._effective_setpoints.get(climate_periods_key)

        return {
            "location": location,
            "cluster": cluster,
            "ui_mode": active_mode,
            "derived_mode": derived_mode,
            "active_period": active_period,
            "mode_parameters": mode_params,
            "effective_setpoints": effective_setpoints,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error in get_mode_state: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/ramps/{location}/{cluster}")
async def get_ramp_states(
    location: str,
    cluster: str,
    scheduler: Scheduler = Depends(get_scheduler),
    db: DatabaseManager = Depends(get_database),
):
    """
    Returns current ramp states from Redis and scheduler memory.
    """
    try:
        # 1. Light ramps from scheduler memory
        light_ramps = {}
        # Access protected member for debug purposes
        ramp_state_memory = getattr(scheduler, "_light_ramp_state", {})
        for key, state in ramp_state_memory.items():
            if key[0] == location and key[1] == cluster:
                device_name = key[2]
                # Convert datetime to string for JSON serialization
                serializable_state = state.copy()
                if "ramp_start_timestamp" in serializable_state:
                    serializable_state["ramp_start_timestamp"] = serializable_state[
                        "ramp_start_timestamp"
                    ].isoformat()
                light_ramps[device_name] = serializable_state

        # 2. Climate ramps from Redis
        climate_ramps = {}
        redis_client: Any = db.automation_redis
        if redis_client and redis_client.redis_enabled and redis_client.redis_client:
            # Pattern for climate ramps
            pattern = f"ramp:{location}:{cluster}:*"
            # Cast to Any to avoid LSP confusion about sync/async redis-py stubs
            keys = cast(Any, redis_client.redis_client.keys(pattern))
            if keys:
                for key in keys:
                    if isinstance(key, bytes):
                        key_str = key.decode()
                    else:
                        key_str = str(key)

                    parts = key_str.split(":")
                    if len(parts) >= 4:
                        setpoint_type = parts[3]
                        state = redis_client.read_ramp_state(location, cluster, setpoint_type)
                        if state:
                            climate_ramps[setpoint_type] = state

        return {
            "location": location,
            "cluster": cluster,
            "light_ramps": light_ramps,
            "climate_ramps": climate_ramps,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"Error in get_ramp_states: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/mode-history/{location}/{cluster}")
async def get_mode_history(
    location: str,
    cluster: str,
    limit: int = 10,
    db: DatabaseManager = Depends(get_database),
):
    """
    Returns recent mode transitions from mode_transition_history table.
    """
    try:
        pool = db.pool
        if not pool:
            raise HTTPException(status_code=500, detail="Database pool not initialized")

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM mode_transition_history
                WHERE location = $1 AND cluster = $2
                ORDER BY triggered_at DESC
                LIMIT $3
                """,
                location,
                cluster,
                limit,
            )

        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Error in get_mode_history: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
