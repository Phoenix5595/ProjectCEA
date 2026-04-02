"""Light dimming control endpoints for DFR0971 DAC modules."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from app.config import ConfigLoader
from app.control.schedule_merge import merge_schedules_with_config
from app.database import DatabaseManager
from app.schemas.lights import (
    IntensityControl,
    ScheduleTimeControl,
    TargetIntensityControl,
    VoltageControl,
)
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from app.automation.interlock_manager import InterlockManager
    from app.control.relay_manager import RelayManager
    from app.control.scheduler import Scheduler
    from app.hardware.dfr0971 import DFR0971Manager

router = APIRouter()

logger = get_logger(__name__)


def _read_light_status_payload(
    location: str,
    cluster: str,
    device_name: str,
    device_info: dict[str, Any],
    dfr0971_manager: Any,
    database: DatabaseManager | None,
    scheduler: Any | None,
) -> dict[str, Any] | None:
    """Read Redis/driver intensity and scheduler nominal target. Assumes dimming is configured."""
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")
    if board_id is None or channel is None:
        return None

    intensity = None
    voltage = None

    if database and database._automation_redis:
        redis_data = database._automation_redis.read_light_intensity(location, cluster, device_name)
        if redis_data:
            intensity = redis_data.get("intensity")
            voltage = redis_data.get("voltage")

    if intensity is None or voltage is None:
        intensity = dfr0971_manager.get_intensity(board_id, channel)
        voltage = dfr0971_manager.get_voltage(board_id, channel)

    if intensity is None or voltage is None:
        logger.warning(
            f"Failed to read light hardware state for {device_name} ({location}/{cluster})"
        )
        return None

    boards = dfr0971_manager.list_boards()
    board_info = next((b for b in boards if b["board_id"] == board_id), None)

    target_intensity = None
    if scheduler:
        intensity_details = scheduler.get_light_intensity_details(
            location, cluster, device_name, datetime.now(), intensity
        )
        if intensity_details:
            target_intensity = intensity_details.get("nominal_intensity")

    return {
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": intensity,
        "voltage": voltage,
        "board_id": board_id,
        "channel": channel,
        "board_info": board_info,
        "target_intensity": target_intensity,
    }


# These will be overridden by main app
def get_dfr0971_manager() -> DFR0971Manager:
    """Dependency to get DFR0971 manager."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    raise RuntimeError("Dependency not injected")


def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_interlock_manager() -> InterlockManager:
    """Dependency to get interlock manager."""
    raise RuntimeError("Dependency not injected")


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


def get_scheduler() -> Scheduler:
    """Dependency to get scheduler."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/lights/boards")
async def list_boards(dfr0971_manager=Depends(get_dfr0971_manager)) -> dict[str, Any]:
    """List all configured DFR0971 boards."""
    boards = dfr0971_manager.list_boards()
    return {"boards": boards, "count": len(boards)}


@router.post("/api/lights/{location}/{cluster}/{device_name}/intensity")
async def set_intensity(
    location: str,
    cluster: str,
    device_name: str,
    control: IntensityControl,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config),
    relay_manager=Depends(get_relay_manager),
    interlock_manager=Depends(get_interlock_manager),
    database=Depends(get_database),
) -> dict[str, Any]:
    """
    Set dimming intensity for a light device.

    The device must be configured in automation_config.yaml with:
    - dimming_enabled: true
    - dimming_type: "dfr0971"
    - dimming_board_id: <board_id>
    - dimming_channel: <0 or 1>
    """
    # Validate intensity
    if control.intensity < 0 or control.intensity > 100:
        raise HTTPException(status_code=400, detail="Intensity must be between 0 and 100")

    # Get device configuration
    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    # Check if dimming is enabled
    if not device_info.get("dimming_enabled", False):
        raise HTTPException(status_code=400, detail=f"Dimming not enabled for device {device_name}")

    if device_info.get("dimming_type") != "dfr0971":
        raise HTTPException(
            status_code=400, detail=f"Device {device_name} is not configured for DFR0971 dimming"
        )

    # Get board_id and channel from config
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")

    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"Device {device_name} missing dimming_board_id or dimming_channel configuration",
        )

    if channel not in [0, 1]:
        raise HTTPException(
            status_code=400, detail=f"Invalid dimming_channel: {channel} (must be 0 or 1)"
        )

    # Check interlock before setting intensity
    if relay_manager and interlock_manager:
        # Get current device states for interlock check
        device_states = relay_manager.get_all_states()

        # Check interlock with requested intensity
        can_set_intensity, reason = interlock_manager.check_interlock(
            location, cluster, device_name, device_states, requested_load=control.intensity
        )

        if not can_set_intensity:
            raise HTTPException(
                status_code=409,  # Conflict
                detail=reason
                or "Interlock blocked: Cannot set intensity due to interlock constraint",
            )

    # Sync relay state with dimmer (same order as device_controller._control_dimmable_light)
    if relay_manager:
        if control.intensity > 0:
            relay_manager.set_device_state(location, cluster, device_name, 1)

    # Set intensity (dimmer)
    success = dfr0971_manager.set_intensity(board_id, channel, control.intensity)

    if not success:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set intensity for board {board_id}, channel {channel}",
        )

    if relay_manager and control.intensity == 0:
        relay_manager.set_device_state(location, cluster, device_name, 0)

    # Get current voltage
    voltage = dfr0971_manager.get_voltage(board_id, channel)

    # Store in Redis for persistence across service restarts
    if database and database._automation_redis:
        database._automation_redis.write_light_intensity(
            location, cluster, device_name, control.intensity, voltage, board_id, channel
        )

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": control.intensity,
        "voltage": voltage,
        "board_id": board_id,
        "channel": channel,
    }


@router.get("/api/lights/{location}/{cluster}/zone-status")
async def get_zone_lights_status(
    location: str,
    cluster: str,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config),
    database=Depends(get_database),
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    """Return intensity + targets for all dimmable lights in one round-trip (ZoneConfig Light intensity)."""
    devices = config.get_devices()
    cluster_devices = devices.get(location, {}).get(cluster, {})
    if not cluster_devices:
        return {"lights": []}

    now = datetime.now()

    schedules_list: list[dict[str, Any]] = []
    if database:
        schedules_list = await database.schedule_repo.get_schedules(location, cluster)

    lights_out: list[dict[str, Any]] = []
    for device_name, device_info in cluster_devices.items():
        if device_info.get("device_type") != "light":
            continue
        if not device_info.get("dimming_enabled", False):
            continue
        board_id = device_info.get("dimming_board_id")
        channel = device_info.get("dimming_channel")
        # Channel 0 is valid; only skip when actually missing from config
        if board_id is None or channel is None:
            continue

        payload = _read_light_status_payload(
            location,
            cluster,
            device_name,
            device_info,
            dfr0971_manager,
            database,
            scheduler,
        )
        if not payload:
            # Still list the device so the UI can show sliders (same as per-device /status when read fails)
            target_fallback = None
            if scheduler:
                intensity_details = scheduler.get_light_intensity_details(
                    location, cluster, device_name, datetime.now(), 0.0
                )
                if intensity_details:
                    target_fallback = intensity_details.get("nominal_intensity")
            boards = dfr0971_manager.list_boards()
            board_info = next((b for b in boards if b["board_id"] == board_id), None)
            payload = {
                "location": location,
                "cluster": cluster,
                "device": device_name,
                "intensity": 0.0,
                "voltage": 0.0,
                "board_id": board_id,
                "channel": channel,
                "board_info": board_info,
                "target_intensity": target_fallback,
            }

        scheduler_effective_intensity: float | None = None
        scheduler_nominal_intensity: float | None = None
        scheduler_is_in_photoperiod: bool | None = None
        if scheduler:
            try:
                det = scheduler.get_light_intensity_details(
                    location,
                    cluster,
                    device_name,
                    now,
                    float(payload.get("intensity") or 0.0),
                )
                if det is not None:
                    scheduler_effective_intensity = float(det.get("effective_intensity"))
                    scheduler_nominal_intensity = float(det.get("nominal_intensity"))
                scheduler_is_in_photoperiod = bool(
                    scheduler.is_in_photoperiod(location, cluster, now)
                )
            except Exception:
                # Zone status should remain best-effort even if scheduler details fail.
                pass

        active_schedule_mode: str | None = None
        for s in schedules_list:
            if s.get("device_name") != device_name:
                continue
            if not s.get("enabled", True):
                continue
            # Determine the active row by time window (same rule as Scheduler: [start, end), supports overnight).
            st = s.get("start_time")
            et = s.get("end_time")
            try:
                st_t = scheduler._parse_time(st) if scheduler else None
                et_t = scheduler._parse_time(et) if scheduler else None
            except Exception:
                st_t = None
                et_t = None
            if not st_t or not et_t:
                continue
            t = now.time()
            in_range = (t >= st_t or t < et_t) if st_t > et_t else (st_t <= t < et_t)
            if in_range:
                active_schedule_mode = str(s.get("mode") or "").upper() or None
                break

        sun_day_target: float | None = None
        for s in schedules_list:
            if s.get("device_name") != device_name:
                continue
            if not s.get("enabled", True):
                continue
            mode = str(s.get("mode") or "").upper()
            if mode in ("SUN", "DAY") and s.get("target_intensity") is not None:
                sun_day_target = float(s["target_intensity"])
                break

        day_target_intensity = sun_day_target
        if day_target_intensity is None and payload.get("target_intensity") is not None:
            day_target_intensity = float(payload["target_intensity"])

        lights_out.append(
            {
                **payload,
                "display_name": device_info.get("display_name"),
                "day_target_intensity": day_target_intensity,
                "scheduler_effective_intensity": scheduler_effective_intensity,
                "scheduler_nominal_intensity": scheduler_nominal_intensity,
                "scheduler_is_in_photoperiod": scheduler_is_in_photoperiod,
                "active_schedule_mode": active_schedule_mode,
            }
        )

    return {"lights": lights_out}


@router.get("/api/lights/{location}/{cluster}/{device_name}/status")
async def get_light_status(
    location: str,
    cluster: str,
    device_name: str,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config),
    database=Depends(get_database),
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    """Get current light status (intensity, voltage, board info, target intensity)."""
    # Get device configuration
    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    # Check if dimming is enabled
    if not device_info.get("dimming_enabled", False):
        raise HTTPException(status_code=400, detail=f"Dimming not enabled for device {device_name}")

    # Get board_id and channel
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")

    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400, detail=f"Device {device_name} missing dimming configuration"
        )

    payload = _read_light_status_payload(
        location,
        cluster,
        device_name,
        device_info,
        dfr0971_manager,
        database,
        scheduler,
    )
    if not payload:
        raise HTTPException(
            status_code=500, detail=f"Failed to read status for board {board_id}, channel {channel}"
        )
    return payload


@router.post("/api/lights/{location}/{cluster}/{device_name}/voltage")
async def set_voltage(
    location: str,
    cluster: str,
    device_name: str,
    control: VoltageControl,
    dfr0971_manager=Depends(get_dfr0971_manager),
    config=Depends(get_config),
) -> dict[str, Any]:
    """
    Set voltage directly for a light device (0-10V).

    This is an alternative to set_intensity for direct voltage control.
    """
    # Validate voltage
    if control.voltage < 0 or control.voltage > 10:
        raise HTTPException(status_code=400, detail="Voltage must be between 0 and 10")

    # Get device configuration
    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    if not device_info.get("dimming_enabled", False):
        raise HTTPException(status_code=400, detail=f"Dimming not enabled for device {device_name}")

    # Get board_id and channel
    board_id = device_info.get("dimming_board_id")
    channel = device_info.get("dimming_channel")

    if board_id is None or channel is None:
        raise HTTPException(
            status_code=400, detail=f"Device {device_name} missing dimming configuration"
        )

    # Set voltage
    success = dfr0971_manager.set_voltage(board_id, channel, control.voltage)

    if not success:
        raise HTTPException(
            status_code=500, detail=f"Failed to set voltage for board {board_id}, channel {channel}"
        )

    # Calculate intensity
    intensity = (control.voltage / 10.0) * 100.0

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "intensity": intensity,
        "voltage": control.voltage,
        "board_id": board_id,
        "channel": channel,
    }


@router.post("/api/lights/{location}/{cluster}/{device_name}/target")
async def set_target_intensity(
    location: str,
    cluster: str,
    device_name: str,
    control: TargetIntensityControl,
    config=Depends(get_config),
    database=Depends(get_database),
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    if control.target_intensity < 0 or control.target_intensity > 100:
        raise HTTPException(status_code=400, detail="Target intensity must be between 0 and 100")

    devices = config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    if device_info.get("device_type") != "light":
        raise HTTPException(status_code=400, detail=f"Device {device_name} is not a light")

    updated = await database.schedule_repo.update_light_schedule_target(
        location, cluster, device_name, control.target_intensity
    )

    if not updated:
        raise HTTPException(status_code=404, detail=f"No active schedule found for {device_name}")

    # Refresh scheduler with updated schedules
    if scheduler:
        all_schedules = await database.schedule_repo.get_schedules()
        scheduler.update_schedules(merge_schedules_with_config(all_schedules, config))
        logger.info(f"Scheduler refreshed after {device_name} target intensity update")

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "target_intensity": control.target_intensity,
    }


@router.get("/api/lights/{location}/{cluster}/{device_name}/schedule")
async def get_light_schedule(
    location: str, cluster: str, device_name: str, database=Depends(get_database)
) -> dict[str, Any]:
    schedule = await database.schedule_repo.get_room_light_schedule(location, cluster, device_name)
    if not schedule:
        raise HTTPException(status_code=404, detail=f"No schedule found for {device_name}")
    return schedule


@router.put("/api/lights/{location}/{cluster}/{device_name}/schedule")
async def update_light_schedule(
    location: str,
    cluster: str,
    device_name: str,
    control: ScheduleTimeControl,
    config=Depends(get_config),
    database=Depends(get_database),
    scheduler=Depends(get_scheduler),
) -> dict[str, Any]:
    updated = await database.update_light_schedule_times(
        location, cluster, device_name, control.start_time, control.end_time
    )
    if not updated:
        raise HTTPException(status_code=404, detail=f"No schedule found for {device_name}")

    if scheduler:
        all_schedules = await database.schedule_repo.get_schedules()
        scheduler.update_schedules(merge_schedules_with_config(all_schedules, config))
        logger.info(f"Scheduler refreshed after {device_name} schedule time update")

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "start_time": control.start_time,
        "end_time": control.end_time,
    }
