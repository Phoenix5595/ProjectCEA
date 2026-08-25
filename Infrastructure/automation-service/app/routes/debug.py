from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from app.config import ConfigLoader
from app.control.control_engine import ControlEngine
from app.control.scheduler import LOCAL_TZ, Scheduler
from app.database import DatabaseManager
from shared.infra_logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/debug", tags=["debug"])


def get_database() -> DatabaseManager:
    from app.main import container

    return container.get_database()


def get_scheduler() -> Scheduler:
    from app.main import container

    return container.get_scheduler()


def get_control_engine() -> ControlEngine:
    from app.main import container

    return container.get_control_engine()


def get_config() -> ConfigLoader:
    from app.main import container

    return container.get_config()


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
            pattern = f"cea:ramp:{location}:{cluster}:*"
            # Cast to Any to avoid LSP confusion about sync/async redis-py stubs
            keys = cast(Any, redis_client.redis_client.keys(pattern))
            if keys:
                for key in keys:
                    key_str = key.decode() if isinstance(key, bytes) else str(key)

                    parts = key_str.replace("cea:", "").split(":")
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


@router.get("/light-schedule-health/{location}/{cluster}")
async def get_light_schedule_health(
    location: str,
    cluster: str,
    scheduler: Scheduler = Depends(get_scheduler),
    db: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
):
    """Per-light schedule resolution and last Grafana DB sample age (effective_setpoints)."""
    try:
        devices = await config.get_devices()
        cluster_devices = devices.get(location, {}).get(cluster, {})
        now = datetime.now(tz=LOCAL_TZ)
        pool = db.pool

        lights_out: list[dict[str, Any]] = []

        for device_name, device_info in cluster_devices.items():
            if device_info.get("device_type") != "light":
                continue
            if (
                not device_info.get("dimming_enabled")
                or device_info.get("dimming_type") != "dfr0971"
            ):
                continue

            has_any = has_sun = has_moon = False
            for s in scheduler.schedules:
                if s.get("location") != location or s.get("cluster") != cluster:
                    continue
                if s.get("device_name") != device_name:
                    continue
                has_any = True
                mode = str(s.get("mode", "")).upper()
                if mode in ("SUN", "DAY"):
                    has_sun = True
                if mode in ("MOON", "NIGHT"):
                    has_moon = True

            det = scheduler.get_light_intensity_details(location, cluster, device_name, now, 0.0)
            details_resolvable = det is not None
            effective_if_any = float(det["effective_intensity"]) if det else None

            last_light_row_age_sec: float | None = None
            row = None
            if pool:
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """
                        SELECT MAX(timestamp) AS ts FROM effective_setpoints
                        WHERE location = $1 AND cluster = $2 AND device_name = $3
                          AND effective_light_intensity IS NOT NULL
                        """,
                        location,
                        cluster,
                        device_name,
                    )
            if row and row["ts"] is not None:
                ts = row["ts"]
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=LOCAL_TZ)
                else:
                    ts = ts.astimezone(LOCAL_TZ)
                last_light_row_age_sec = (now - ts).total_seconds()

            lights_out.append(
                {
                    "device_name": device_name,
                    "has_any_schedule_row": has_any,
                    "has_sun_or_day_row": has_sun,
                    "has_moon_or_night_row": has_moon,
                    "details_resolvable_now": details_resolvable,
                    "effective_intensity_if_any": effective_if_any,
                    "last_effective_light_row_age_sec": last_light_row_age_sec,
                }
            )

        return {
            "location": location,
            "cluster": cluster,
            "now": now.isoformat(),
            "lights": lights_out,
        }
    except Exception as e:
        logger.error(f"Error in get_light_schedule_health: {e}")
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
