from __future__ import annotations

import json
import re
from typing import Any

from app.models.device_registry import _UI_TO_DB_DEVICE_TYPES, Device, DeviceCreate, DeviceUpdate
from app.repositories.devices._helpers import _generate_device_name, _row_to_device
from shared.cluster_topology import _room_prefix

from ..base import logger


class RegistryMixin:
    @staticmethod
    def _lowest_free_positive_index(device_names: list[str], prefix: str) -> int:
        """Return the first reusable canonical suffix for one room/type."""
        pattern = re.compile(rf"^{re.escape(prefix)}_(\d+)$")
        occupied = {
            int(match.group(1))
            for device_name in device_names
            if (match := pattern.fullmatch(device_name)) is not None and int(match.group(1)) > 0
        }
        index = 1
        while index in occupied:
            index += 1
        return index

    async def lowest_free_index_locked(
        self, connection: Any, room: str, canonical_type: str
    ) -> int:
        """Lock a room/type identity set and return its first free generated index."""
        rows = await connection.fetch(
            """SELECT device_name FROM device_registry
               WHERE location = $1 AND device_type = $2 FOR UPDATE""",
            room,
            canonical_type,
        )
        prefix = f"{canonical_type}_{_room_prefix(room)}"
        return self._lowest_free_positive_index([str(row["device_name"]) for row in rows], prefix)

    async def get_device_for_update(self, connection: Any, device_id: int) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            "SELECT * FROM device_registry WHERE device_id = $1 FOR UPDATE", device_id
        )
        return dict(row) if row is not None else None

    async def find_relay_owner_for_update(
        self, connection: Any, channel: int, exclude_device_id: int | None = None
    ) -> dict[str, Any] | None:
        row = await connection.fetchrow(
            """SELECT * FROM device_registry WHERE channel = $1
               AND ($2::integer IS NULL OR device_id != $2)
               FOR UPDATE""",
            channel,
            exclude_device_id,
        )
        return dict(row) if row is not None else None

    async def clear_relay_binding(self, connection: Any, device_id: int) -> None:
        await connection.execute(
            "UPDATE device_registry SET channel = NULL, updated_at = NOW() WHERE device_id = $1",
            device_id,
        )

    async def assign_relay_steal(
        self, connection: Any, device_id: int, channel: int, displaced_device_id: int
    ) -> None:
        await self.clear_relay_binding(connection, displaced_device_id)
        await connection.execute(
            "UPDATE device_registry SET channel = $1, updated_at = NOW() WHERE device_id = $2",
            channel,
            device_id,
        )

    async def create_device_locked(self, connection: Any, create: DeviceCreate) -> Device:
        canonical_type = _UI_TO_DB_DEVICE_TYPES.get(create.device_type, create.device_type)
        per_room_index = await self.lowest_free_index_locked(
            connection, create.room, canonical_type
        )
        device_name = _generate_device_name(create.room, canonical_type, per_room_index)
        row = await connection.fetchrow(
            """INSERT INTO device_registry
                   (location, cluster, device_name, display_name, device_type, channel,
                    pid_enabled, interlock_with, pid_setpoints, created_at, updated_at)
               VALUES ($1, 'main', $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, NOW(), NOW())
               RETURNING *""",
            create.room,
            device_name,
            create.display_name,
            canonical_type,
            create.channel,
            create.pid_enabled,
            json.dumps(create.interlock_with),
            json.dumps(create.pid_setpoints),
        )
        return _row_to_device(dict(row))

    async def update_device_locked(
        self, connection: Any, device_id: int, update: DeviceUpdate
    ) -> Device | None:
        fields = update.model_dump(exclude_unset=True)
        assignments: list[tuple[str, Any]] = []
        for column in ("channel", "display_name", "pid_enabled"):
            if column in fields:
                assignments.append((column, fields[column]))
        for column in ("interlock_with", "pid_setpoints"):
            if column in fields:
                assignments.append((f"{column}::jsonb", json.dumps(fields[column])))
        if not assignments:
            row = await connection.fetchrow(
                "SELECT * FROM device_registry WHERE device_id = $1", device_id
            )
            return _row_to_device(dict(row)) if row is not None else None
        set_parts = [
            f"{column.split('::')[0]} = ${index}{'::jsonb' if '::' in column else ''}"
            for index, (column, _value) in enumerate(assignments, start=1)
        ]
        values = [value for _column, value in assignments]
        row = await connection.fetchrow(
            f"UPDATE device_registry SET {', '.join(set_parts)}, updated_at = NOW() "
            f"WHERE device_id = ${len(values) + 1} RETURNING *",
            *values,
            device_id,
        )
        return _row_to_device(dict(row)) if row is not None else None

    async def delete_device_locked(self, connection: Any, device_id: int) -> bool:
        result = await connection.execute(
            "DELETE FROM device_registry WHERE device_id = $1 AND device_type != 'light'", device_id
        )
        return result.endswith("1")

    async def delete_current_state_locked(self, connection: Any, device: dict[str, Any]) -> None:
        await connection.execute(
            """DELETE FROM device_states WHERE location = $1 AND cluster = $2 AND device_name = $3""",
            device["location"],
            device["cluster"],
            device["device_name"],
        )
        await connection.execute(
            """DELETE FROM effective_setpoints WHERE location = $1 AND cluster = $2 AND device_name = $3""",
            device["location"],
            device["cluster"],
            device["device_name"],
        )

    async def delete_device_dependents_locked(self, connection: Any, device_id: int) -> None:
        """Remove only current device-linked rows; schedules and history intentionally remain."""
        await connection.execute(
            "DELETE FROM light_target_intensity WHERE device_id = $1", device_id
        )
        await connection.execute("DELETE FROM light_programs WHERE device_id = $1", device_id)

    async def get_device_id(self, location: str, cluster: str, device_name: str) -> int | None:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT device_id FROM device_registry
                       WHERE location = $1 AND cluster = $2 AND device_name = $3""",
                    location,
                    cluster,
                    device_name,
                )
                return row["device_id"] if row else None
        except Exception as error:
            logger.error("Failed to get device_id: %s", error)
            return None

    async def get_device_type_by_id(self, device_id: int) -> str | None:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT device_type FROM device_registry WHERE device_id = $1", device_id
                )
                return row["device_type"] if row else None
        except Exception as error:
            logger.error("Failed to get device_type: %s", error)
            return None

    async def get_device_count_by_type_location(self, device_type: str, location: str) -> int:
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM device_registry WHERE device_type = $1 AND location = $2",
                    device_type,
                    location,
                )
                return row["cnt"] if row else 0
        except Exception as error:
            logger.error("Failed to get device count: %s", error)
            return 0
