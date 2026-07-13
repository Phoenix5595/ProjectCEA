"""DFR0971 board/channel assignment endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from app.config import ConfigLoader
from app.hardware.dfr0971 import DFR0971Manager
from app.routes.lights import get_config, get_dfr0971_manager, router
from app.schemas.lights import DfrChannelAssignControl


async def _iter_all_dfr0971_lights(config: ConfigLoader) -> list[dict[str, Any]]:
    devices = await config.get_devices() or {}
    out: list[dict[str, Any]] = []
    for location, clusters in devices.items():
        if not isinstance(clusters, dict):
            continue
        for cluster, devs in clusters.items():
            if not isinstance(devs, dict):
                continue
            for device_name, device_info in devs.items():
                if not isinstance(device_info, dict):
                    continue
                if device_info.get("device_type") != "light":
                    continue
                if (
                    not device_info.get("dimming_enabled")
                    or device_info.get("dimming_type") != "dfr0971"
                ):
                    continue
                out.append(
                    {
                        "location": location,
                        "cluster": cluster,
                        "device_name": device_name,
                        "display_name": device_info.get("display_name"),
                        "dimming_board_id": device_info.get("dimming_board_id"),
                        "dimming_channel": device_info.get("dimming_channel"),
                    }
                )
    return out


@router.get("/api/lights/boards")
async def list_boards(
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
) -> dict[str, Any]:
    """List all configured DFR0971 boards."""
    boards = dfr0971_manager.list_boards()
    return {"boards": boards, "count": len(boards)}


@router.get("/api/lights/dfr/assignments")
async def get_dfr_assignments(
    config: ConfigLoader = Depends(get_config),
    dfr0971_manager: DFR0971Manager = Depends(get_dfr0971_manager),
) -> dict[str, Any]:
    """Return DFR0971 boards + per-board channel assignments + all dimmable DFR lights."""
    boards = dfr0971_manager.list_boards()
    lights = await _iter_all_dfr0971_lights(config)

    # board_id -> {0: assignment|null, 1: assignment|null}
    assignments: dict[str, dict[str, Any | None]] = {}
    for b in boards:
        bid = b.get("board_id")
        if bid is None:
            continue
        assignments[str(bid)] = {"0": None, "1": None}

    for light in lights:
        bid = light.get("dimming_board_id")
        ch = light.get("dimming_channel")
        if bid is None or ch is None:
            continue
        key = str(bid)
        ch_key = str(ch)
        if key not in assignments:
            assignments[key] = {"0": None, "1": None}
        if ch_key not in ("0", "1"):
            continue
        assignments[key][ch_key] = {
            "location": light["location"],
            "cluster": light["cluster"],
            "device_name": light["device_name"],
            "display_name": light.get("display_name"),
        }

    return {"boards": boards, "assignments": assignments, "lights": lights}


@router.put("/api/lights/dfr/assign")
async def assign_dfr_channel(
    control: DfrChannelAssignControl,
    config: ConfigLoader = Depends(get_config),
) -> dict[str, Any]:
    """Assign (or clear) a DFR0971 (board_id, channel) mapping for a dimmable light device."""
    device_configs = await config.get_devices() or {}
    device_info = (
        device_configs.get(control.location, {}).get(control.cluster, {}).get(control.device_name)
    )
    if not isinstance(device_info, dict):
        raise HTTPException(
            status_code=404,
            detail=f"Device not found: {control.location}/{control.cluster}/{control.device_name}",
        )
    if device_info.get("device_type") != "light":
        raise HTTPException(status_code=400, detail="Target device is not a light")
    if not device_info.get("dimming_enabled") or device_info.get("dimming_type") != "dfr0971":
        raise HTTPException(
            status_code=400, detail="Target light is not configured for DFR0971 dimming"
        )

    if control.board_id is None or control.dimming_channel is None:
        ok = config.update_light_dimming_assignment(
            control.location,
            control.cluster,
            control.device_name,
            board_id=None,
            dimming_channel=None,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to clear DFR assignment")
        config.reload()
        return {
            "success": True,
            "location": control.location,
            "cluster": control.cluster,
            "device_name": control.device_name,
            "board_id": None,
            "dimming_channel": None,
        }

    if control.dimming_channel not in (0, 1):
        raise HTTPException(status_code=400, detail="dimming_channel must be 0 or 1")

    # Global uniqueness: (board_id, dimming_channel) can only belong to one light.
    requested_pair = (int(control.board_id), int(control.dimming_channel))
    for light in await _iter_all_dfr0971_lights(config):
        if (
            light["location"] == control.location
            and light["cluster"] == control.cluster
            and light["device_name"] == control.device_name
        ):
            continue
        if (light.get("dimming_board_id"), light.get("dimming_channel")) == requested_pair:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"DFR channel already assigned to "
                    f"{light['location']}/{light['cluster']}/{light['device_name']}"
                ),
            )

    ok = config.update_light_dimming_assignment(
        control.location,
        control.cluster,
        control.device_name,
        board_id=int(control.board_id),
        dimming_channel=int(control.dimming_channel),
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update DFR assignment")
    config.reload()

    return {
        "success": True,
        "location": control.location,
        "cluster": control.cluster,
        "device_name": control.device_name,
        "board_id": int(control.board_id),
        "dimming_channel": int(control.dimming_channel),
    }
