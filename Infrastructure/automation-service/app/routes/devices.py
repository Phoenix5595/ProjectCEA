"""Device control endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.cluster_config import ensure_configured_cluster, iter_flower_main_merged_devices
from app.config import ConfigLoader
from app.control.relay_manager import RelayManager
from app.database import DatabaseManager
from app.redis.schema import RELAY_TIMESTAMPS
from app.redis_client import AutomationRedisClient
from app.schemas.device import (
    DeviceConfigUpdate,
    DeviceControlRequest,
    DeviceMappingUpdate,
    DeviceModeRequest,
)
from app.validation import validate_device_mapping
from shared.cluster_topology import (
    ClusterMismatchError,
    UnknownRoomError,
    assert_device_cluster,
)
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


def _api_cluster_for_device_row(location: str, cluster: str) -> str:
    """Devices UI and control plane use ``main`` for Flower; legacy YAML may still tag ``front``/``back``."""
    if location == "Flower Room" and cluster in ("front", "back"):
        return "main"
    return cluster


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


def get_automation_redis() -> AutomationRedisClient:
    """Dependency to get AutomationRedisClient."""
    raise RuntimeError("Dependency not injected")


def get_config() -> ConfigLoader:
    """Dependency to get config loader."""
    from app.main import container

    return container.get_config()


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
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Get devices for a specific location/cluster with configuration."""
    _validate_device_cluster_or_400(location, cluster)
    device_configs = await config.get_devices()
    ensure_configured_cluster(device_configs, location, cluster)
    devices = {}
    device_states = relay_manager.get_all_states()
    device_configs = await config.get_devices()

    # Get all devices from config for this location/cluster
    location_config = device_configs.get(location, {})
    if location == "Flower Room" and cluster == "main":
        cluster_config_items = iter_flower_main_merged_devices(location_config)
    else:
        raw = location_config.get(cluster, {})
        cluster_config_items = [
            (cluster, name, info) for name, info in raw.items() if isinstance(info, dict)
        ]

    # Iterate over all devices in config (not just those with states)
    seen_names: set[str] = set()
    for src_cluster, device_name, device_info in cluster_config_items:
        if device_name in seen_names:
            continue
        seen_names.add(device_name)
        # Get state from relay manager (default to 0 if not found)
        key = (location, src_cluster, device_name)
        state = device_states.get(key, 0)
        mode = relay_manager.get_device_mode(location, src_cluster, device_name) or "auto"
        channel = relay_manager.get_channel(location, src_cluster, device_name)

        devices[device_name] = {
            "state": state,
            "mode": mode,
            "channel": channel,
            "device_type": device_info.get("device_type"),
            "display_name": device_info.get("display_name"),
            "dimming_enabled": device_info.get("dimming_enabled", False),
            "dimming_type": device_info.get("dimming_type"),
            "dimming_board_id": device_info.get("dimming_board_id"),
            "dimming_channel": device_info.get("dimming_channel"),
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
    automation_redis: AutomationRedisClient = Depends(get_automation_redis),
) -> dict[str, Any]:
    """Manually control a device (turn ON/OFF)."""
    if request.state not in [0, 1]:
        raise HTTPException(status_code=400, detail="State must be 0 (OFF) or 1 (ON)")

    current_state = relay_manager.get_device_state(location, cluster, device) or 0
    channel = relay_manager.get_channel(location, cluster, device)

    if channel is None:
        raise HTTPException(status_code=404, detail="Device not found")

    # Set device state
    success, reason = relay_manager.set_device_state(
        location, cluster, device, bool(request.state), "manual"
    )

    # Update database
    await database.device_repo.set_device_state(
        location, cluster, device, channel, bool(request.state), "manual"
    )

    if not success:
        raise HTTPException(status_code=400, detail=reason or "Failed to set device state")

    # Update RELAY_TIMESTAMPS in Redis when the state actually changed
    old_state = bool(current_state)
    new_state = bool(request.state)
    if old_state != new_state and automation_redis.redis_client is not None:
        try:
            raw_ts = automation_redis.get(RELAY_TIMESTAMPS)
            timestamps: list[str | None] = json.loads(raw_ts) if raw_ts else [None] * 16
            now_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            timestamps[channel] = now_iso
            await asyncio.to_thread(
                automation_redis.redis_client.set,
                RELAY_TIMESTAMPS,
                json.dumps(timestamps),
            )
        except Exception as e:
            logger.warning(f"Failed to update RELAY_TIMESTAMPS for channel {channel}: {e}")

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
    config: ConfigLoader = Depends(get_config),
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
    ensure_configured_cluster(await config.get_devices(), location, cluster)
    try:
        return await database.control_action_repo.get_control_history_filtered(
            location, cluster, limit, channel, since, until
        )
    except Exception as e:
        logger.warning(f"get_control_history failed: {e}")
        return []


@router.get("/api/devices/mappings")
async def get_all_device_mappings(
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, Any]]:
    """Get all device mappings.

    Returns:
        List of device mapping dicts with location, cluster, device_name, channel, active_high, safe_state, mcp_board_id
    """
    return await database.device_repo.get_all_device_mappings()


@router.get("/api/devices/{location}/{cluster}/{device}/mapping")
async def get_device_mapping(
    location: str, cluster: str, device: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Get device mapping for a specific device.

    Returns:
        Device mapping dict with channel, active_high, safe_state, mcp_board_id, updated_at
    """
    mapping = await database.device_repo.get_device_mapping(location, cluster, device)
    if mapping is None:
        raise HTTPException(status_code=404, detail="Device mapping not found")
    return {"location": location, "cluster": cluster, "device_name": device, **mapping}


@router.post("/api/devices/{location}/{cluster}/{device}/mapping")
async def update_device_mapping(
    location: str,
    cluster: str,
    device: str,
    mapping: DeviceMappingUpdate,
    database: DatabaseManager = Depends(get_database),
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Update device mapping for a device.

    Backend validates and persists all device mappings.

    Returns:
        Updated device mapping
    """
    # Validate mapping
    # Get existing mappings to check for duplicates
    existing_mappings_list = await database.device_repo.get_all_device_mappings()
    existing_mappings = {}
    for m in existing_mappings_list:
        key = (m["location"], m["cluster"], m["device_name"])
        if key != (location, cluster, device):  # Exclude current device
            existing_mappings[key] = m

    is_valid, error_message = validate_device_mapping(
        mapping.channel, mapping.mcp_board_id, config._config, existing_mappings
    )

    if not is_valid:
        raise HTTPException(status_code=400, detail=error_message or "Invalid device mapping")

    # Validate safe_state
    if mapping.safe_state not in [0, 1]:
        raise HTTPException(status_code=400, detail="safe_state must be 0 or 1")

    # Update mapping in database
    success = await database.device_repo.set_device_mapping(
        location,
        cluster,
        device,
        mapping.channel,
        mapping.active_high,
        bool(mapping.safe_state),
        mapping.mcp_board_id if mapping.mcp_board_id is not None else 0,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update device mapping")

    # Return updated mapping
    updated = await database.device_repo.get_device_mapping(location, cluster, device)
    result = {"location": location, "cluster": cluster, "device_name": device}
    if updated is not None:
        result.update(updated)
    return result


@router.post("/api/devices/{location}/{cluster}/{device}/config")
async def update_device_config(
    location: str,
    cluster: str,
    device: str,
    config_update: DeviceConfigUpdate,
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Update device configuration (display_name, device_type).

    Args:
        location: Location name
        cluster: Cluster name
        device: Device name
        config_update: Configuration update request

    Returns:
        Updated device configuration
    """
    # Validate device exists
    device_configs = await config.get_devices()
    if location not in device_configs:
        raise HTTPException(status_code=404, detail=f"Location {location} not found")
    if cluster not in device_configs.get(location, {}):
        raise HTTPException(status_code=404, detail=f"Cluster {cluster} not found in {location}")
    if device not in device_configs[location][cluster]:
        raise HTTPException(
            status_code=404, detail=f"Device {device} not found in {location}/{cluster}"
        )

    # Validate device_type if provided
    if config_update.device_type is not None:
        valid_types = [
            "heater",
            "fan",
            "dehumidifier",
            "humidifier",
            "light",
            "pump",
            "co2",
            "vent",
        ]
        if config_update.device_type not in valid_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid device_type. Must be one of: {', '.join(valid_types)}",
            )

    # Update config
    success = config.update_device_config(
        location,
        cluster,
        device,
        display_name=config_update.display_name,
        device_type=config_update.device_type,
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to update device configuration")

    # Reload config to get updated values
    config.reload()

    # Get fresh device configs after reload
    device_configs = await config.get_devices()

    # Return updated device info
    device_info = device_configs[location][cluster][device]
    return {
        "location": location,
        "cluster": cluster,
        "device_name": device,
        "display_name": device_info.get("display_name"),
        "device_type": device_info.get("device_type"),
        "success": True,
    }


@router.get("/api/devices/channels")
async def get_all_channels(
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Get all 16 MCP channels (0-15) with their current device assignments.

    Reads from the DB-backed device registry.
    """
    channels: dict[str, dict[str, Any]] = {}
    for ch in range(16):
        channels[str(ch)] = {
            "channel": ch,
            "device_name": None,
            "display_name": None,
            "device_type": None,
            "location": None,
            "cluster": None,
            "light_name": None,
        }

    hierarchy = await database.device_repo.get_all_as_hierarchy()
    light_names: list[dict[str, Any]] = []

    for location, clusters in hierarchy.items():
        for cluster, devices in clusters.items():
            api_cluster = _api_cluster_for_device_row(location, cluster)
            for device_name, device_info in devices.items():
                channel = device_info.get("channel")
                device_type = device_info.get("device_type")
                display_name = device_info.get("display_name")

                if device_type == "light":
                    light_names.append(
                        {
                            "name": display_name,
                            "device_name": device_name,
                            "location": location,
                            "cluster": api_cluster,
                            "bound_relay_channel": channel,
                            "device_id": device_info.get("device_id"),
                        }
                    )

                if channel is not None and 0 <= channel < 16:
                    channels[str(channel)] = {
                        "channel": channel,
                        "device_name": device_name,
                        "display_name": display_name,
                        "device_type": device_type,
                        "location": location,
                        "cluster": api_cluster,
                        "light_name": display_name if device_type == "light" else None,
                    }

    return {"channels": channels, "light_names": light_names}
