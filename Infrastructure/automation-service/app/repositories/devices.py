from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from app.models.device_registry import LightDevice

from .base import BaseRepository, logger

if TYPE_CHECKING:
    from asyncpg import Pool

_ROOM_PREFIXES: dict[str, str] = {
    "Flower Room": "f",
    "Veg Room": "v",
    "Lab": "l",
    "Outside": "o",
}


def _room_prefix(room: str) -> str:
    """Return the one-letter prefix for a room name."""
    if room not in _ROOM_PREFIXES:
        raise ValueError(f"Unknown room: {room!r}")
    return _ROOM_PREFIXES[room]


def _generate_light_device_name(room: str, per_room_index: int) -> str:
    """Generate canonical device_name for a light."""
    return f"light_{_room_prefix(room)}_{per_room_index}"


class DeviceRepository(BaseRepository):
    """Repository for device state and mapping operations."""

    def __init__(self, pool: Pool | None = None) -> None:
        super().__init__(pool)

    async def get_device_state(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get current device state."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, device_name, channel, state, mode, updated_at
                       FROM device_states
                       WHERE location = $1 AND cluster = $2 AND device_name = $3""",
                    location,
                    cluster,
                    device_name,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get device state: {e}")
        return None

    async def set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        state: bool,
        mode: str = "auto",
    ) -> bool:
        """Set device state in database."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO device_states (location, cluster, device_name, channel, state, mode, updated_at)
                       VALUES ($1, $2, $3, $4, $5, $6, NOW())
                       ON CONFLICT (location, cluster, device_name)
                       DO UPDATE SET channel = $4, state = $5, mode = $6, updated_at = NOW()""",
                    location,
                    cluster,
                    device_name,
                    channel,
                    state,
                    mode,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set device state: {e}")
            return False

    async def get_device_states(self, location: str, cluster: str) -> dict[str, dict[str, Any]]:
        """Get device states for a location and cluster."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT device_name, channel, state, mode, updated_at
                       FROM device_states
                       WHERE location = $1 AND cluster = $2""",
                    location,
                    cluster,
                )
                return {row["device_name"]: dict(row) for row in rows}
        except Exception as e:
            logger.error(f"Failed to get device states: {e}")
            return {}

    async def get_all_device_states(self) -> list[dict[str, Any]]:
        """Get all device states."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM device_states ORDER BY location, cluster, device_name"
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all device states: {e}")
            return []

    async def get_device_mapping(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get device hardware mapping."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT location, cluster, device_name, channel, active_high, safe_state, mcp_board_id
                       FROM device_mappings
                       WHERE location = $1 AND cluster = $2 AND device_name = $3""",
                    location,
                    cluster,
                    device_name,
                )
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Failed to get device mapping: {e}")
        return None

    async def set_device_mapping(
        self,
        location: str,
        cluster: str,
        device_name: str,
        channel: int,
        active_high: bool = True,
        safe_state: bool = False,
        mcp_board_id: int = 0,
    ) -> bool:
        """Set device hardware mapping."""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """INSERT INTO device_mappings (location, cluster, device_name, channel, active_high, safe_state, mcp_board_id)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (location, cluster, device_name)
                       DO UPDATE SET channel = $4, active_high = $5, safe_state = $6, mcp_board_id = $7""",
                    location,
                    cluster,
                    device_name,
                    channel,
                    active_high,
                    safe_state,
                    mcp_board_id,
                )
                return True
        except Exception as e:
            logger.error(f"Failed to set device mapping: {e}")
            return False

    async def get_all_device_mappings(self) -> list[dict[str, Any]]:
        """Get all device mappings."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM device_mappings ORDER BY location, cluster, device_name"
                )
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get all device mappings: {e}")
            return []

    async def get_latest_light_intensity(
        self, location: str, cluster: str, device_name: str
    ) -> float | None:
        """Get latest light intensity for a device."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT effective_light_intensity FROM effective_setpoints
                       WHERE location = $1 AND cluster = $2 AND device_name = $3
                       ORDER BY timestamp DESC LIMIT 1""",
                    location,
                    cluster,
                    device_name,
                )
                if row and row["effective_light_intensity"] is not None:
                    return float(row["effective_light_intensity"])
        except Exception as e:
            logger.error(f"Failed to get light intensity: {e}")
        return None

    async def get_all_as_hierarchy(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        """Return nested dict: {location: {cluster: {device_name: {fields}}}}."""
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """SELECT location, cluster, device_name, display_name, device_type,
                              channel, dimming_enabled, dimming_type, dimming_board_id,
                              dimming_channel, safety_level, pid_enabled, interlock_with,
                              pid_setpoints, per_room_index, created_at, updated_at
                       FROM device_registry
                       ORDER BY location, cluster, device_name"""
                )
                hierarchy: dict[str, dict[str, dict[str, dict[str, Any]]]] = {}
                for row in rows:
                    loc = row["location"]
                    clu = row["cluster"]
                    name = row["device_name"]
                    hierarchy.setdefault(loc, {}).setdefault(clu, {})[name] = dict(row)
                return hierarchy
        except Exception as e:
            logger.error(f"Failed to get device registry hierarchy: {e}")
            return {}

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
                    json.dumps([]),
                    json.dumps({}),
                    per_room_index,
                )
                return _row_to_light_device(dict(row))
        except Exception as e:
            logger.error(f"Failed to create light: {e}")
            raise

    async def update_light(self, device_id: int, **fields: Any) -> LightDevice | None:
        """Update light fields. If room or per_room_index changes, regenerate device_name."""
        allowed = {"display_name", "room", "per_room_index", "relay_channel", "safety_level"}
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid fields for update_light: {invalid}")

        try:
            async with self.pool.acquire() as conn:
                current = await conn.fetchrow(
                    "SELECT location, per_room_index FROM device_registry WHERE device_id = $1",
                    device_id,
                )
                if current is None:
                    return None

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

                if not set_parts:
                    row = await conn.fetchrow(
                        "SELECT * FROM device_registry WHERE device_id = $1", device_id
                    )
                    return _row_to_light_device(dict(row)) if row else None

                set_parts.append("updated_at = NOW()")
                sql = f"UPDATE device_registry SET {', '.join(set_parts)} WHERE device_id = ${arg_idx} RETURNING *"
                args.append(device_id)

                row = await conn.fetchrow(sql, *args)
                return _row_to_light_device(dict(row)) if row else None
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

    async def clear_relay_binding_only(self, device_id: int) -> bool:
        """Root-cause #1 fix: NULL the channel field, NEVER delete the row."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE device_registry SET channel = NULL, updated_at = NOW() WHERE device_id = $1",
                    device_id,
                )
                return result.startswith("UPDATE") and "1" in result
        except Exception as e:
            logger.error(f"Failed to clear relay binding: {e}")
            return False

    async def bind_relay(self, device_id: int, channel: int) -> bool:
        """Bind a light to a relay channel with 1:1 conflict check."""
        try:
            async with self.pool.acquire() as conn:
                conflict = await conn.fetchrow(
                    """SELECT device_id FROM device_registry
                       WHERE channel = $1 AND device_type = 'light' AND device_id != $2
                       LIMIT 1""",
                    channel,
                    device_id,
                )
                if conflict is not None:
                    raise ValueError(
                        f"Relay channel {channel} already bound to device_id {conflict['device_id']}"
                    )

                result = await conn.execute(
                    "UPDATE device_registry SET channel = $1, updated_at = NOW() WHERE device_id = $2",
                    channel,
                    device_id,
                )
                return result.startswith("UPDATE") and "1" in result
        except Exception as e:
            logger.error(f"Failed to bind relay: {e}")
            return False

    async def rename_and_regenerate_device_name(
        self, device_id: int, new_room: str, new_index: int
    ) -> LightDevice | None:
        """Cascade trigger: update room, index, and regenerate device_name."""
        new_device_name = _generate_light_device_name(new_room, new_index)
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """UPDATE device_registry
                       SET location = $1, per_room_index = $2, device_name = $3, updated_at = NOW()
                       WHERE device_id = $4
                       RETURNING *""",
                    new_room,
                    new_index,
                    new_device_name,
                    device_id,
                )
                return _row_to_light_device(dict(row)) if row else None
        except Exception as e:
            logger.error(f"Failed to rename and regenerate device name: {e}")
            return None


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
