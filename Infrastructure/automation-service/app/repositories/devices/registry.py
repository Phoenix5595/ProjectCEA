"""Registry-specific queries for the device repository.

Provides CRUD operations for the device_registry table, including light and
non-light device creation, updates, deletions, and relay binding management.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.device_registry import (
    _UI_TO_DB_DEVICE_TYPES,
    Device,
    DeviceCreate,
    DeviceUpdate,
    LightDevice,
)
from app.repositories.devices._helpers import (
    _generate_device_name,
    _generate_light_device_name,
    _row_to_device,
    _row_to_light_device,
)

from ..base import logger


class RegistryMixin:
    """Mixin adding device registry CRUD methods to DeviceRepository."""

    async def get_device_for_update(self, connection: Any, device_id: int) -> dict[str, Any] | None:
        """Lock and return one registry row for a serialized assignment mutation."""
        row = await connection.fetchrow(
            "SELECT * FROM device_registry WHERE device_id = $1 FOR UPDATE", device_id
        )
        return dict(row) if row is not None else None

    async def find_relay_owner_for_update(
        self, connection: Any, channel: int, exclude_device_id: int | None = None
    ) -> dict[str, Any] | None:
        """Lock the current relay owner so conflict decisions and steals are atomic."""
        row = await connection.fetchrow(
            """SELECT * FROM device_registry WHERE channel = $1
               AND ($2::integer IS NULL OR device_id != $2)
               FOR UPDATE""",
            channel,
            exclude_device_id,
        )
        return dict(row) if row is not None else None

    async def clear_relay_binding(self, connection: Any, device_id: int) -> None:
        """Clear exactly one locked registry relay binding without deleting its device."""
        await connection.execute(
            "UPDATE device_registry SET channel = NULL, updated_at = NOW() WHERE device_id = $1",
            device_id,
        )

    async def assign_relay_steal(
        self, connection: Any, device_id: int, channel: int, displaced_device_id: int
    ) -> None:
        """Atomically clear the locked displaced binding and assign its relay to the requester."""
        await self.clear_relay_binding(connection, displaced_device_id)
        await connection.execute(
            "UPDATE device_registry SET channel = $1, updated_at = NOW() WHERE device_id = $2",
            channel,
            device_id,
        )

    async def create_device_locked(self, connection: Any, create: DeviceCreate) -> Device:
        """Create a non-light device using the caller's serialized transaction."""
        canonical_type = _UI_TO_DB_DEVICE_TYPES.get(create.device_type, create.device_type)
        locked_rows = await connection.fetch(
            """SELECT device_id FROM device_registry
               WHERE location = $1 AND device_type = $2 FOR UPDATE""",
            create.room,
            canonical_type,
        )
        device_name = _generate_device_name(create.room, canonical_type, len(locked_rows) + 1)
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
        """Update a locked non-light row without opening a second transaction."""
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
        """Delete a previously locked non-light registry row."""
        result = await connection.execute(
            "DELETE FROM device_registry WHERE device_id = $1 AND device_type != 'light'", device_id
        )
        return result.endswith("1")

    async def delete_current_state_locked(self, connection: Any, device: dict[str, Any]) -> None:
        """Clean device state and effective light setpoints in the mutation transaction."""
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

    async def get_device_id(self, location: str, cluster: str, device_name: str) -> int | None:
        """Get device_id by location/cluster/device_name."""
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
        except Exception as e:
            logger.error(f"Failed to get device_id: {e}")
            return None

    async def get_device_type_by_id(self, device_id: int) -> str | None:
        """Get device_type by device_id."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT device_type FROM device_registry WHERE device_id = $1", device_id
                )
                return row["device_type"] if row else None
        except Exception as e:
            logger.error(f"Failed to get device_type: {e}")
            return None

    async def get_device_count_by_type_location(self, device_type: str, location: str) -> int:
        """Count devices of a given canonical type in a room."""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as cnt FROM device_registry WHERE device_type = $1 AND location = $2",
                    device_type,
                    location,
                )
                return row["cnt"] if row else 0
        except Exception as e:
            logger.error(f"Failed to get device count: {e}")
            return 0

    async def create_device(self, create: DeviceCreate) -> Device:
        """Create a new non-light device. Auto-generates device_name."""
        canonical_type = _UI_TO_DB_DEVICE_TYPES.get(create.device_type, create.device_type)
        room = create.room

        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    count_row = await conn.fetchrow(
                        "SELECT COUNT(*) as cnt FROM device_registry WHERE location = $1 AND device_type = $2",
                        room,
                        canonical_type,
                    )
                    n = (count_row["cnt"] if count_row else 0) + 1
                    device_name = _generate_device_name(room, canonical_type, n)

                    row = await conn.fetchrow(
                        """INSERT INTO device_registry
                            (location, cluster, device_name, display_name, device_type,
                             channel, pid_enabled, interlock_with, pid_setpoints,
                             created_at, updated_at)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, NOW(), NOW())
                           RETURNING *""",
                        room,
                        "main",
                        device_name,
                        create.display_name,
                        canonical_type,
                        create.channel,
                        create.pid_enabled,
                        json.dumps(create.interlock_with),
                        json.dumps(create.pid_setpoints),
                    )
                    return _row_to_device(dict(row))
        except Exception as e:
            logger.error(f"Failed to create device: {e}")
            raise

    async def update_device(self, device_id: int, update: DeviceUpdate) -> Device | None:
        """Update non-light device fields. Does NOT update room."""
        allowed = {
            "channel",
            "display_name",
            "pid_enabled",
            "interlock_with",
            "pid_setpoints",
        }
        fields = update.model_dump(exclude_unset=True)
        invalid = set(fields.keys()) - allowed
        if invalid:
            raise ValueError(f"Invalid fields for update_device: {invalid}")

        try:
            async with self.pool.acquire() as conn:
                current = await conn.fetchrow(
                    "SELECT device_type FROM device_registry WHERE device_id = $1", device_id
                )
                if current is None:
                    return None
                if current["device_type"] == "light":
                    raise ValueError("Use update_light() for light devices")

                if "channel" in fields:
                    conflict = await conn.fetchrow(
                        """SELECT device_id FROM device_registry
                           WHERE channel = $1 AND device_id != $2
                           LIMIT 1""",
                        fields["channel"],
                        device_id,
                    )
                    if conflict is not None:
                        raise ValueError(
                            f"Relay channel {fields['channel']} already in use by device_id {conflict['device_id']}"
                        )

                set_parts: list[str] = []
                args: list[Any] = []
                arg_idx = 1

                if "channel" in fields:
                    set_parts.append(f"channel = ${arg_idx}")
                    args.append(fields["channel"])
                    arg_idx += 1

                if "display_name" in fields:
                    set_parts.append(f"display_name = ${arg_idx}")
                    args.append(fields["display_name"])
                    arg_idx += 1

                if "pid_enabled" in fields:
                    set_parts.append(f"pid_enabled = ${arg_idx}")
                    args.append(fields["pid_enabled"])
                    arg_idx += 1

                if "interlock_with" in fields:
                    set_parts.append(f"interlock_with = ${arg_idx}::jsonb")
                    args.append(json.dumps(fields["interlock_with"]))
                    arg_idx += 1

                if "pid_setpoints" in fields:
                    set_parts.append(f"pid_setpoints = ${arg_idx}::jsonb")
                    args.append(json.dumps(fields["pid_setpoints"]))
                    arg_idx += 1

                if not set_parts:
                    row = await conn.fetchrow(
                        "SELECT * FROM device_registry WHERE device_id = $1", device_id
                    )
                    return _row_to_device(dict(row)) if row else None

                set_parts.append("updated_at = NOW()")
                sql = f"UPDATE device_registry SET {', '.join(set_parts)} WHERE device_id = ${arg_idx} RETURNING *"
                args.append(device_id)

                row = await conn.fetchrow(sql, *args)
                return _row_to_device(dict(row)) if row else None
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to update device: {e}")
            return None

    async def delete_device(self, device_id: int) -> bool:
        """Delete a non-light device."""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM device_registry WHERE device_id = $1 AND device_type != 'light'",
                    device_id,
                )
                return result.startswith("DELETE") and "1" in result
        except Exception as e:
            logger.error(f"Failed to delete device: {e}")
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

    async def cascade_device_name_change(
        self,
        old_name: str,
        new_name: str,
        location: str,
        cluster: str,
        redis_client: Any | None = None,
    ) -> None:
        """Update all references to old_name in effective_setpoints and Redis.

        Args:
            old_name: Previous device_name (e.g. 'light_f_1').
            new_name: New device_name (e.g. 'light_v_2').
            location: Room location.
            cluster: Cluster name.
            redis_client: Optional Redis client for key rename operations.
        """
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """UPDATE effective_setpoints
                           SET device_name = $1
                           WHERE location = $2 AND cluster = $3 AND device_name = $4""",
                        new_name,
                        location,
                        cluster,
                        old_name,
                    )
        except Exception as e:
            logger.error(f"Failed to cascade device_name change in DB: {e}")
            raise

        if redis_client is not None:
            try:
                old_pattern = f"effective_setpoint:{location}:{cluster}:light:{old_name}:*"
                for key in redis_client.scan_iter(match=old_pattern):
                    suffix = key.split(":")[-1]
                    new_key = f"effective_setpoint:{location}:{cluster}:light:{new_name}:{suffix}"
                    redis_client.rename(key, new_key)

                old_light_key = f"light:{location}:{cluster}:{old_name}"
                new_light_key = f"light:{location}:{cluster}:{new_name}"
                if redis_client.exists(old_light_key):
                    redis_client.rename(old_light_key, new_light_key)
            except Exception as e:
                logger.error(f"Failed to cascade device_name change in Redis: {e}")
