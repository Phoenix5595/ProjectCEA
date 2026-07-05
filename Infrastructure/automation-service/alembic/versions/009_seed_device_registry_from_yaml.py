"""Seed device_registry from automation_config.yaml.

Reads the canonical YAML devices block, canonicalizes device_type aliases,
and idempotently inserts every device into device_registry.

Revision ID: 009_seed_device_registry
Revises: 008_device_registry
Create Date: 2026-07-05

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from alembic import op
from sqlalchemy import text
import yaml

revision = "009_seed_device_registry"
down_revision = "008_device_registry"
branch_labels = None
depends_on = None

_DEVICE_TYPE_ALIASES: dict[str, str] = {
    "heater": "heating",
}

_ROOM_PREFIXES: dict[str, str] = {
    "Flower Room": "f",
    "Veg Room": "v",
    "Lab": "l",
    "Outside": "o",
}


def _canonicalize_device_type(raw: str) -> str:
    return _DEVICE_TYPE_ALIASES.get(raw, raw)


def _generate_light_device_name(room: str, per_room_index: int) -> str:
    return f"light_{_ROOM_PREFIXES[room]}_{per_room_index}"


def _load_yaml_devices() -> dict[str, Any]:
    migration_dir = Path(__file__).resolve().parent
    yaml_path = migration_dir.parent.parent / "automation_config.yaml"
    with open(yaml_path) as f:
        config = yaml.safe_load(f) or {}
    return config.get("devices", {})


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        raise NotImplementedError("Migration 009 requires PostgreSQL")

    devices = _load_yaml_devices()
    conn = op.get_bind()

    for room_name, room in devices.items():
        if not isinstance(room, dict):
            continue
        for cluster_name, cluster in room.items():
            if not isinstance(cluster, dict):
                continue
            for device_key, dev_info in cluster.items():
                if not isinstance(dev_info, dict):
                    continue

                raw_type = dev_info.get("device_type", "")
                device_type = _canonicalize_device_type(raw_type)
                display_name = dev_info.get("display_name")
                channel = dev_info.get("channel")
                pid_enabled = dev_info.get("pid_enabled", False)
                interlock_with = dev_info.get("interlock_with", [])
                pid_setpoints = dev_info.get("pid_setpoints", {})

                dimming_enabled = dev_info.get("dimming_enabled")
                dimming_type = dev_info.get("dimming_type")
                dimming_board_id = dev_info.get("dimming_board_id")
                dimming_channel = dev_info.get("dimming_channel")
                safety_level = dev_info.get("safety_level")
                per_room_index = None

                if device_type == "light":
                    if device_key.startswith("light_"):
                        try:
                            per_room_index = int(device_key.split("_", 1)[1])
                        except (IndexError, ValueError):
                            per_room_index = None
                    device_name = (
                        _generate_light_device_name(room_name, per_room_index)
                        if per_room_index is not None
                        else device_key
                    )
                else:
                    device_name = device_key

                conn.execute(
                    text(
                        """INSERT INTO device_registry
                            (location, cluster, device_name, display_name, device_type,
                             channel, dimming_enabled, dimming_type, dimming_board_id,
                             dimming_channel, safety_level, pid_enabled, interlock_with,
                             pid_setpoints, per_room_index, created_at, updated_at)
                           VALUES
                            (:location, :cluster, :device_name, :display_name, :device_type,
                             :channel, :dimming_enabled, :dimming_type, :dimming_board_id,
                             :dimming_channel, :safety_level, :pid_enabled, :interlock_with,
                             :pid_setpoints, :per_room_index, NOW(), NOW())
                           ON CONFLICT (location, cluster, device_name) DO NOTHING"""
                    ),
                    {
                        "location": room_name,
                        "cluster": cluster_name,
                        "device_name": device_name,
                        "display_name": display_name,
                        "device_type": device_type,
                        "channel": channel,
                        "dimming_enabled": dimming_enabled,
                        "dimming_type": dimming_type,
                        "dimming_board_id": dimming_board_id,
                        "dimming_channel": dimming_channel,
                        "safety_level": safety_level,
                        "pid_enabled": pid_enabled,
                        "interlock_with": json.dumps(interlock_with),
                        "pid_setpoints": json.dumps(pid_setpoints),
                        "per_room_index": per_room_index,
                    },
                )


def downgrade() -> None:
    op.execute("DELETE FROM device_registry")
