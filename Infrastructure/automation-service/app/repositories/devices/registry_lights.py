from __future__ import annotations

from typing import Any

from app.models.device_registry import LightDevice
from app.repositories.devices._helpers import _generate_light_device_name, _row_to_light_device

from ..base import logger


class LightRegistryMixin:
    async def assert_dfr_free(
        self,
        connection: Any,
        board_id: int,
        dimming_channel: int,
        exclude_device_id: int | None = None,
    ) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            """SELECT * FROM device_registry WHERE device_type = 'light'
               AND dimming_board_id = $1 AND dimming_channel = $2
               AND ($3::integer IS NULL OR device_id != $3) FOR UPDATE""",
            board_id,
            dimming_channel,
            exclude_device_id,
        )
        return dict(row) if row is not None else None

    async def create_light_locked(
        self,
        connection: Any,
        *,
        board_id: int,
        dimming_channel: int,
        room: str,
        display_name: str,
        per_room_index: int,
        relay_channel: int | None,
    ) -> LightDevice:
        device_name = _generate_light_device_name(room, per_room_index)
        row = await connection.fetchrow(
            """INSERT INTO device_registry
                   (location, cluster, device_name, display_name, device_type, channel,
                    dimming_enabled, dimming_type, dimming_board_id, dimming_channel,
                    safety_level, pid_enabled, interlock_with, pid_setpoints,
                    per_room_index, created_at, updated_at)
               VALUES ($1, 'main', $2, $3, 'light', $4, TRUE, 'dfr0971', $5, $6,
                       0, FALSE, '[]', '{}', $7, NOW(), NOW()) RETURNING *""",
            room,
            device_name,
            display_name,
            relay_channel,
            board_id,
            dimming_channel,
            per_room_index,
        )
        return _row_to_light_device(dict(row))

    async def update_light_locked(
        self, connection: Any, device_id: int, fields: dict[str, Any]
    ) -> LightDevice | None:
        current = await connection.fetchrow(
            "SELECT * FROM device_registry WHERE device_id = $1 AND device_type = 'light'",
            device_id,
        )
        if current is None:
            return None
        current_row = dict(current)
        column_map = {
            "display_name": "display_name",
            "relay_channel": "channel",
            "dimming_board_id": "dimming_board_id",
            "dimming_channel": "dimming_channel",
        }
        assignments = [
            (column_map[field], value) for field, value in fields.items() if field in column_map
        ]
        if not assignments:
            return _row_to_light_device(current_row)
        set_parts = [
            f"{column} = ${index}" for index, (column, _value) in enumerate(assignments, start=1)
        ]
        values = [value for _column, value in assignments]
        row = await connection.fetchrow(
            f"UPDATE device_registry SET {', '.join(set_parts)}, updated_at = NOW() "
            f"WHERE device_id = ${len(values) + 1} RETURNING *",
            *values,
            device_id,
        )
        return _row_to_light_device(dict(row)) if row is not None else None

    async def delete_light_locked(self, connection: Any, device_id: int) -> bool:
        result = await connection.execute(
            "DELETE FROM device_registry WHERE device_id = $1 AND device_type = 'light'", device_id
        )
        return result.endswith("1")

    async def get_lights_by_room(self, room: str) -> list[LightDevice]:
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
        except Exception as error:
            logger.error("Failed to get lights by room: %s", error)
            return []

    async def get_light_by_id(self, device_id: int) -> LightDevice | None:
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
        except Exception as error:
            logger.error("Failed to get light by id: %s", error)
            return None
