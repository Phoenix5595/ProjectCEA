"""Light device registry CRUD mixin for DeviceRepository."""

from __future__ import annotations

from typing import Any, cast

from app.models.device_registry import LightDevice
from app.repositories.devices._helpers import (
    _generate_light_device_name,
    _row_to_light_device,
    _row_to_typed_device,
)

from ..base import logger


class LightRegistryMixin:
    """Mixin adding light device registry CRUD methods to DeviceRepository."""

    async def get_lights_by_room(self, room: str) -> list[LightDevice]:
        """Get all light devices for a room."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT location, cluster, device_name, display_name, device_type,
                              channel, dimming_enabled, dimming_type, dimming_board_id,
                              dimming_channel, safety_level, pid_enabled, interlock_with,
                              pid_setpoints, per_room_index, created_at, updated_at
                       FROM device_registry
                       WHERE location = $1 AND device_type = 'light'
                       ORDER BY per_room_index""",
                    room,
                )
                return [_row_to_light_device(dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get lights by room: {e}")
            return []

    async def get_unbound_lights_by_room(self, room: str) -> list[LightDevice]:
        """Get lights with channel IS NULL (no relay binding)."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT location, cluster, device_name, display_name, device_type,
                              channel, dimming_enabled, dimming_type, dimming_board_id,
                              dimming_channel, safety_level, pid_enabled, interlock_with,
                              pid_setpoints, per_room_index, created_at, updated_at
                       FROM device_registry
                       WHERE location = $1 AND device_type = 'light' AND channel IS NULL
                       ORDER BY per_room_index""",
                    room,
                )
                return [_row_to_light_device(dict(row)) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get unbound lights by room: {e}")
            return []

    async def get_light_by_id(self, device_id: int) -> LightDevice | None:
        """Get a light device by its primary key."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT device_id, location, cluster, device_name, display_name, device_type,
                              channel, dimming_enabled, dimming_type, dimming_board_id,
                              dimming_channel, safety_level, pid_enabled, interlock_with,
                              pid_setpoints, per_room_index, created_at, updated_at
                       FROM device_registry
                       WHERE device_id = $1 AND device_type = 'light'""",
                    device_id,
                )
                return _row_to_light_device(dict(row)) if row else None
        except Exception as e:
            logger.error(f"Failed to get light by id: {e}")
            return None

    async def create_light(
        self,
        board_id: int,
        dimming_channel: int,
        room: str,
        display_name: str,
        per_room_index: int,
    ) -> LightDevice:
        """Create a new light device. Auto-generates device_name as light_{prefix}_{index}."""
        device_name = _generate_light_device_name(room, per_room_index)
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO device_registry
                        (location, cluster, device_name, display_name, device_type,
                         dimming_enabled, dimming_type, dimming_board_id, dimming_channel,
                         safety_level, pid_enabled, interlock_with, pid_setpoints,
                         per_room_index, created_at, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13::jsonb, $14, NOW(), NOW())
                       RETURNING *""",
                    room,
                    "main",
                    device_name,
                    display_name,
                    "light",
                    True,
                    "dfr0971",
                    board_id,
                    dimming_channel,
                    0,
                    False,
                    "[]",
                    "{}",
                    per_room_index,
                )
                return _row_to_light_device(dict(row))
        except Exception as e:
            logger.error(f"Failed to create light: {e}")
            raise

    async def update_light(self, device_id: int, **fields: Any) -> LightDevice | None:
        """Update light fields. If room or per_room_index changes, regenerate device_name."""
        allowed = {
            "display_name",
            "room",
            "per_room_index",
            "relay_channel",
            "safety_level",
            "device_type",
            "dimming_board_id",
            "dimming_channel",
        }
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid fields for update_light: {invalid}")

        try:
            async with self.pool.acquire() as conn:
                current = await conn.fetchrow(
                    "SELECT location, per_room_index, device_type FROM device_registry WHERE device_id = $1",
                    device_id,
                )
                if current is None:
                    return None
                if current["device_type"] != "light":
                    raise ValueError("Use update_device() for non-light devices")

                room = fields.get("room", current["location"])
                per_room_index = fields.get("per_room_index", current["per_room_index"])

                if room != current["location"] or per_room_index != current["per_room_index"]:
                    device_name = _generate_light_device_name(room, per_room_index)
                else:
                    device_name = None

                set_parts: list[str] = []
                args: list[Any] = []
                arg_idx = 1

                if device_name is not None:
                    set_parts.append(f"device_name = ${arg_idx}")
                    args.append(device_name)
                    arg_idx += 1

                if "display_name" in fields:
                    set_parts.append(f"display_name = ${arg_idx}")
                    args.append(fields["display_name"])
                    arg_idx += 1

                if "room" in fields:
                    set_parts.append(f"location = ${arg_idx}")
                    args.append(fields["room"])
                    arg_idx += 1

                if "per_room_index" in fields:
                    set_parts.append(f"per_room_index = ${arg_idx}")
                    args.append(fields["per_room_index"])
                    arg_idx += 1

                if "relay_channel" in fields:
                    set_parts.append(f"channel = ${arg_idx}")
                    args.append(fields["relay_channel"])
                    arg_idx += 1

                if "safety_level" in fields:
                    set_parts.append(f"safety_level = ${arg_idx}")
                    args.append(fields["safety_level"])
                    arg_idx += 1

                if "device_type" in fields:
                    set_parts.append(f"device_type = ${arg_idx}")
                    args.append(fields["device_type"])
                    arg_idx += 1

                if "dimming_board_id" in fields:
                    set_parts.append(f"dimming_board_id = ${arg_idx}")
                    args.append(fields["dimming_board_id"])
                    arg_idx += 1

                if "dimming_channel" in fields:
                    set_parts.append(f"dimming_channel = ${arg_idx}")
                    args.append(fields["dimming_channel"])
                    arg_idx += 1

                if not set_parts:
                    row = await conn.fetchrow(
                        "SELECT * FROM device_registry WHERE device_id = $1", device_id
                    )
                    return cast(LightDevice, _row_to_typed_device(dict(row))) if row else None

                set_parts.append("updated_at = NOW()")
                sql = f"UPDATE device_registry SET {', '.join(set_parts)} WHERE device_id = ${arg_idx} RETURNING *"
                args.append(device_id)

                row = await conn.fetchrow(sql, *args)
                return cast(LightDevice, _row_to_typed_device(dict(row))) if row else None
        except Exception as e:
            logger.error(f"Failed to update light: {e}")
            return None

    async def delete_light(self, device_id: int) -> bool:
        """Delete a light device."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM device_registry WHERE device_id = $1", device_id
                )
                return result.startswith("DELETE") and "1" in result
        except Exception as e:
            logger.error(f"Failed to delete light: {e}")
            return False
