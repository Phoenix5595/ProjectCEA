"""Device control endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.schemas.device import (
    DeviceControlRequest,
    DeviceModeRequest,
)
from shared.cluster_topology import (
    ClusterMismatchError,
    UnknownRoomError,
    assert_device_cluster,
)
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _validate_device_cluster_or_400(location: str, cluster: str) -> None:
    """Reject device requests whose ``cluster`` is a sensor sub-cluster.

    Phase 5e: the dashboard previously fanned out ``getDashboardPollZones``
    (which includes Flower's ``front`` / ``back`` sensor sub-clusters)
    against the *device* endpoint, which then 404'd with the generic
    ``"Unknown location/cluster"`` message — wasted DB hits and noise
    in every browser console. This check rejects the wrong-type case
    with a 400 + hint **before** ``ensure_configured_cluster`` runs,
    so the caller is told exactly which cluster to use.
    """
    try:
        assert_device_cluster(location, cluster)
    except ClusterMismatchError as exc:
        raise HTTPException(status_code=400, detail=exc.hint) from exc
    except UnknownRoomError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# These will be overridden by main app
def get_relay_manager() -> RelayManager:
    """Dependency to get relay manager."""
    raise RuntimeError("Dependency not injected")


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/devices")
async def get_all_devices(
    relay_manager: RelayManager = Depends(get_relay_manager),
) -> list[dict[str, Any]]:
    """Get all devices with current state."""
    devices = []
    device_states = relay_manager.get_all_states()

    for (location, cluster, device_name), state in device_states.items():
        mode = relay_manager.get_device_mode(location, cluster, device_name) or "auto"
        channel = relay_manager.get_channel(location, cluster, device_name)

        devices.append(
            {
                "location": location,
                "cluster": cluster,
                "device_name": device_name,
                "state": state,
                "mode": mode,
                "channel": channel,
            }
        )

    return devices


@router.get("/api/devices/{location}/{cluster}")
async def get_devices_for_location_cluster(
    location: str,
    cluster: str,
    relay_manager: RelayManager = Depends(get_relay_manager),
) -> dict[str, Any]:
    """Get device state and identity from the installed registry snapshot."""
    _validate_device_cluster_or_400(location, cluster)
    devices = {}
    registry_devices = relay_manager.get_devices_for_location_cluster(location, cluster)

    for device_name, device_info in registry_devices.items():
        state = relay_manager.get_device_state(location, cluster, device_name) or 0
        mode = relay_manager.get_device_mode(location, cluster, device_name) or "auto"
        channel = relay_manager.get_channel(location, cluster, device_name)

        devices[device_name] = {
            "state": state,
            "mode": mode,
            "channel": channel,
            "device_type": device_info["device_type"],
            "display_name": device_info["display_name"],
            "dimming_enabled": device_info["dimming_enabled"],
            "dimming_type": device_info["dimming_type"],
            "dimming_board_id": device_info["dimming_board_id"],
            "dimming_channel": device_info["dimming_channel"],
        }

    return {"location": location, "cluster": cluster, "devices": devices}


@router.get("/api/devices/{location}/{cluster}/{device}")
async def get_device_details(
    location: str,
    cluster: str,
    device: str,
    relay_manager: RelayManager = Depends(get_relay_manager),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Get detailed device status."""
    state = relay_manager.get_device_state(location, cluster, device)
    mode = relay_manager.get_device_mode(location, cluster, device) or "auto"
    channel = relay_manager.get_channel(location, cluster, device)
    device_info = relay_manager.get_device_info(location, cluster, device)

    if state is None:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get device state from database
    db_state = await database.device_repo.get_device_state(location, cluster, device)

    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        "state": state,
        "mode": mode,
        "channel": channel,
        "device_info": device_info,
        "database_state": db_state,
    }


@router.post("/api/devices/{location}/{cluster}/{device}/control")
async def control_device(
    location: str,
    cluster: str,
    device: str,
    request: DeviceControlRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Manually control a device (turn ON/OFF)."""
    if request.state not in [0, 1]:
        raise HTTPException(status_code=400, detail="State must be 0 (OFF) or 1 (ON)")

    current_state = relay_manager.get_device_state(location, cluster, device) or 0
    channel = relay_manager.get_channel(location, cluster, device)

    if channel is None:
        raise HTTPException(status_code=404, detail="Device not found")

    # Set device state
    success, reason = await relay_manager.set_device_state(
        location, cluster, device, request.state, "manual"
    )

    if not success:
        raise HTTPException(status_code=503, detail=reason or "Failed to set device state")

    await database.device_repo.set_device_state(
        location, cluster, device, channel, bool(request.state), "manual"
    )

    manual_expires_at: datetime | None = None
    if request.duration_seconds is not None and request.state == 1:
        manual_expires_at = datetime.now(UTC) + timedelta(seconds=request.duration_seconds)

    await database.control_action_repo.log_control_action(
        location,
        cluster,
        device,
        channel,
        current_state,
        request.state,
        "manual",
        request.reason or "Manual override",
        load_percent=None,
        manual_expires_at=manual_expires_at,
    )

    return {
        "location": location,
        "cluster": cluster,
        "device": device,
        "state": request.state,
        "mode": "manual",
        "success": True,
    }


@router.post("/api/devices/{location}/{cluster}/{device}/mode")
async def set_device_mode(
    location: str,
    cluster: str,
    device: str,
    request: DeviceModeRequest,
    relay_manager: RelayManager = Depends(get_relay_manager),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Set device control mode."""
    if request.mode not in ["manual", "auto", "scheduled"]:
        raise HTTPException(status_code=400, detail="Mode must be 'manual', 'auto', or 'scheduled'")

    current_state = relay_manager.get_device_state(location, cluster, device)
    channel = relay_manager.get_channel(location, cluster, device)

    if channel is None:
        raise HTTPException(status_code=404, detail="Device not found")

    # Update mode in database
    state = current_state or 0
    await database.device_repo.set_device_state(
        location, cluster, device, channel, bool(state), request.mode
    )

    return {
        "location": location,
        "cluster": cluster,
        "device": device,
        "mode": request.mode,
        "success": True,
    }


@router.get("/api/control/history")
async def get_control_history(
    location: str | None = None,
    cluster: str | None = None,
    limit: int = 10,
    channel: int | None = None,
    since: str | None = None,
    until: str | None = None,
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, Any]]:
    if not location or not cluster:
        raise HTTPException(
            status_code=400,
            detail="Query parameters 'location' and 'cluster' are required",
        )
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise HTTPException(
            status_code=400,
            detail="Parameter 'limit' must be an integer between 1 and 100",
        )
    _validate_device_cluster_or_400(location, cluster)
    try:
        return await database.control_action_repo.get_control_history_filtered(
            location, cluster, limit, channel, since, until
        )
    except Exception as e:
        logger.warning(f"get_control_history failed: {e}")
        return []
