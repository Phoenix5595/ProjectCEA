"""Device control endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.control.control_snapshot_service import ControlSnapshotService
from app.control.device_command_service import (
    AutoCommand,
    DeviceCommand,
    DeviceCommandAuditError,
    DeviceCommandHardwareError,
    DeviceCommandNotAssignedError,
    DeviceCommandService,
    ManualOffCommand,
    TimedOnCommand,
)
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.schemas.control_snapshot import ControlSnapshotResponse
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


def get_device_command_service() -> DeviceCommandService:
    """Dependency to get the assigned-device command authority."""
    raise RuntimeError("Dependency not injected")


def get_control_snapshot_service() -> ControlSnapshotService:
    """Dependency to get the on-demand composite control read model."""
    raise RuntimeError("Dependency not injected")


@router.get("/api/devices/control-snapshot", response_model=ControlSnapshotResponse)
async def get_control_snapshot(
    control_snapshot_service: ControlSnapshotService = Depends(get_control_snapshot_service),
) -> ControlSnapshotResponse:
    """Return the complete typed control-state projection from its natural owners."""
    return control_snapshot_service.get_snapshot()


@router.get("/api/devices")
async def get_all_devices(
    relay_manager: RelayManager = Depends(get_relay_manager),
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
) -> list[dict[str, Any]]:
    """Get all devices with current state."""
    devices = []
    device_states = relay_manager.get_all_states()

    for (location, cluster, device_name), state in device_states.items():
        mode = device_command_service.get_command_state(location, cluster, device_name).mode
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
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
) -> dict[str, Any]:
    """Get device state and identity from the installed registry snapshot."""
    _validate_device_cluster_or_400(location, cluster)
    devices = {}
    registry_devices = relay_manager.get_devices_for_location_cluster(location, cluster)

    for device_name, device_info in registry_devices.items():
        state = relay_manager.get_device_state(location, cluster, device_name) or 0
        mode = device_command_service.get_command_state(location, cluster, device_name).mode
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
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
) -> dict[str, Any]:
    """Get detailed device status."""
    state = relay_manager.get_device_state(location, cluster, device)
    command_state = device_command_service.get_command_state(location, cluster, device)
    channel = relay_manager.get_channel(location, cluster, device)
    device_info = relay_manager.get_device_info(location, cluster, device)

    if device_info is None:
        raise HTTPException(status_code=404, detail="Device not found")

    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        "state": state,
        "mode": command_state.mode,
        "command_expires_at": command_state.expires_at,
        "channel": channel,
        "device_info": device_info,
    }


@router.post("/api/devices/{location}/{cluster}/{device}/command")
async def command_device(
    location: str,
    cluster: str,
    device: str,
    request: Annotated[DeviceCommand, Body(discriminator="action")],
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
) -> dict[str, Any]:
    """Apply exactly one discriminated command to an assigned relay identity."""
    _validate_device_cluster_or_400(location, cluster)
    try:
        result = await device_command_service.execute(location, cluster, device, request)
    except DeviceCommandNotAssignedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (DeviceCommandHardwareError, DeviceCommandAuditError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "location": location,
        "cluster": cluster,
        "device": device,
        "mode": result.mode,
        "expires_at": result.expires_at,
        "success": True,
    }


@router.post("/api/devices/{location}/{cluster}/{device}/control")
async def control_device(
    location: str,
    cluster: str,
    device: str,
    request: DeviceControlRequest,
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
) -> dict[str, Any]:
    """Translate the legacy manual shape into one assigned-device command."""
    if request.state not in [0, 1]:
        raise HTTPException(status_code=400, detail="State must be 0 (OFF) or 1 (ON)")
    match request.state:
        case 0:
            command = ManualOffCommand(reason=request.reason or "Manual OFF")
        case 1:
            if request.duration_seconds is None:
                raise HTTPException(status_code=400, detail="Manual ON requires duration_seconds")
            command = TimedOnCommand(
                duration_seconds=request.duration_seconds,
                reason=request.reason or "Timed manual ON",
            )
        case unreachable:
            raise AssertionError(f"Validated device state became invalid: {unreachable}")
    return await command_device(location, cluster, device, command, device_command_service)


@router.post("/api/devices/{location}/{cluster}/{device}/mode")
async def set_device_mode(
    location: str,
    cluster: str,
    device: str,
    request: DeviceModeRequest,
    device_command_service: DeviceCommandService = Depends(get_device_command_service),
) -> dict[str, Any]:
    """Translate legacy mode writes into a single command-service operation."""
    match request.mode:
        case "manual":
            command = ManualOffCommand(reason="Legacy manual mode")
        case "auto" | "scheduled":
            command = AutoCommand(reason="Legacy automatic mode")
        case _:
            raise HTTPException(
                status_code=400, detail="Mode must be 'manual', 'auto', or 'scheduled'"
            )
    return await command_device(location, cluster, device, command, device_command_service)


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
