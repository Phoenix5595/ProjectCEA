"""Helper functions for device repository operations.

Centralizes row-to-model conversion and device name generation to avoid
circular imports between the DeviceRepository core, registry mixin, and
hierarchy helpers.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.device_registry import Device, LightDevice
from shared.cluster_topology import _room_prefix


def _generate_light_device_name(room: str, per_room_index: int) -> str:
    """Generate canonical device_name for a light."""
    return f"light_{_room_prefix(room)}_{per_room_index}"


def _generate_device_name(room: str, canonical_type: str, per_room_index: int) -> str:
    """Generate canonical device_name for a non-light device."""
    return f"{canonical_type}_{_room_prefix(room)}_{per_room_index}"


def _row_to_typed_device(row: dict[str, Any]) -> LightDevice | Device:
    """Convert a DB row dict to the correct typed Pydantic model."""
    if row.get("device_type") == "light":
        return _row_to_light_device(row)
    return _row_to_device(row)


def _row_to_device(row: dict[str, Any]) -> Device:
    """Convert a DB row dict to a Device Pydantic model (non-light)."""
    interlock_raw = row.get("interlock_with")
    if isinstance(interlock_raw, str):
        interlock_with = json.loads(interlock_raw) if interlock_raw else []
    else:
        interlock_with = interlock_raw if interlock_raw is not None else []

    setpoints_raw = row.get("pid_setpoints")
    if isinstance(setpoints_raw, str):
        pid_setpoints = json.loads(setpoints_raw) if setpoints_raw else {}
    else:
        pid_setpoints = setpoints_raw if setpoints_raw is not None else {}

    return Device(
        device_id=row.get("device_id"),
        device_type=row["device_type"],
        channel=row["channel"],
        pid_enabled=row["pid_enabled"] if row["pid_enabled"] is not None else False,
        interlock_with=interlock_with,
        pid_setpoints=pid_setpoints,
        display_name=row.get("display_name") or None,
        device_name=row["device_name"],
        location=row["location"],
        cluster=row["cluster"] or "main",
    )


def _row_to_light_device(row: dict[str, Any]) -> LightDevice:
    """Convert a DB row dict to a LightDevice Pydantic model."""
    return LightDevice(
        device_id=row.get("device_id"),
        device_type=row["device_type"],
        board_id=row["dimming_board_id"],
        dimming_channel=row["dimming_channel"],
        dimming_enabled=row["dimming_enabled"] if row["dimming_enabled"] is not None else True,
        dimming_type=row["dimming_type"] if row["dimming_type"] is not None else "dfr0971",
        safety_level=row["safety_level"] if row["safety_level"] is not None else 0,
        per_room_index=row["per_room_index"],
        relay_channel=row["channel"],
        display_name=row["display_name"] or "",
        device_name=row["device_name"],
        location=row["location"],
        cluster=row["cluster"],
    )
