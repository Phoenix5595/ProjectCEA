"""Canonical transactional mutations for live device registry assignments."""

# SIZE_OK — one audited service owns the complete transaction-to-snapshot boundary.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.control.relay_manager import RelayManager
from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.hardware.dfr0971 import DFR0971Manager
from app.models.device_registry import (
    Device,
    DeviceCreate,
    DeviceUpdate,
    LightDevice,
    LightDeviceCreate,
    LightDeviceUpdate,
)
from app.repositories.devices import DeviceRepository
from shared.cluster_topology import _room_prefix


@dataclass(frozen=True, slots=True)
class RegistryConflictError(Exception):
    """Raised when a locked hardware assignment belongs to a different device."""

    assignment: str
    owner: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RegistryNotFoundError(Exception):
    """Raised when a requested registry row does not exist while locked."""

    device_id: int


@dataclass(frozen=True, slots=True)
class SafeOutputError(Exception):
    """Raised when an old hardware assignment cannot be made safe before commit."""

    output: str


@dataclass(frozen=True, slots=True)
class DeviceMutation:
    """The canonical mutation result returned to HTTP boundaries."""

    device: Device | LightDevice | None
    displaced_device_id: int | None = None


class DeviceRegistryService:
    """Owns every registry write and its required safe-output transition."""

    def __init__(
        self,
        device_repo: DeviceRepository,
        runtime_device_registry: RuntimeDeviceRegistry,
        relay_manager: RelayManager,
        dfr0971_manager: DFR0971Manager | None,
    ) -> None:
        self._device_repo = device_repo
        self._runtime_device_registry = runtime_device_registry
        self._relay_manager = relay_manager
        self._dfr0971_manager = dfr0971_manager

    async def list_devices(self) -> list[Device | LightDevice]:
        """Return the typed read projection without giving routes repository access."""
        return await self._device_repo.get_all_devices_flat()

    async def create_device(
        self, create: DeviceCreate, *, confirmed_relay_steal: bool = False
    ) -> DeviceMutation:
        """Create a non-light after atomically reserving its optional relay channel."""
        _room_prefix(create.room)
        return await self._runtime_device_registry.mutate(
            lambda connection: self._create_device(connection, create, confirmed_relay_steal)
        )

    async def create_light(
        self, create: LightDeviceCreate, *, confirmed_relay_steal: bool = False
    ) -> DeviceMutation:
        """Create a light, its room-scoped targets, and optional relay assignment."""
        _room_prefix(create.room)
        return await self._runtime_device_registry.mutate(
            lambda connection: self._create_light(connection, create, confirmed_relay_steal)
        )

    async def update_device(
        self,
        device_id: int,
        update: DeviceUpdate,
        *,
        confirmed_relay_steal: bool = False,
    ) -> DeviceMutation:
        """Update one non-light using locked current assignments."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._update_device(
                connection, device_id, update, confirmed_relay_steal
            )
        )

    async def update_light(
        self,
        device_id: int,
        update: LightDeviceUpdate,
        *,
        confirmed_relay_steal: bool = False,
    ) -> DeviceMutation:
        """Update one light using locked relay and DFR assignments."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._update_light(
                connection, device_id, update, confirmed_relay_steal
            )
        )

    async def update_registry_device(
        self,
        device_id: int,
        fields: dict[str, Any],
        *,
        confirmed_relay_steal: bool = False,
    ) -> DeviceMutation:
        """Parse a route update against its locked device kind before mutating it."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._update_registry_device(
                connection, device_id, fields, confirmed_relay_steal
            )
        )

    async def delete_device(self, device_id: int) -> DeviceMutation:
        """Safely disable and delete a non-light and its current state."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._delete_device(connection, device_id, expected_type="non-light")
        )

    async def delete_light(self, device_id: int) -> DeviceMutation:
        """Safely disable and delete a light and its current state."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._delete_device(connection, device_id, expected_type="light")
        )

    async def delete_registry_device(self, device_id: int) -> DeviceMutation:
        """Delete the locked row using its canonical device kind."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._delete_registry_device(connection, device_id)
        )

    async def unbind_relay(self, device_id: int) -> DeviceMutation:
        """Turn an existing relay off before atomically clearing its binding."""
        return await self._runtime_device_registry.mutate(
            lambda connection: self._unbind_relay(connection, device_id)
        )

    async def move_dfr(self, device_id: int, board_id: int, dimming_channel: int) -> DeviceMutation:
        """Move a light DFR assignment after the old DFR output reaches zero."""
        update = LightDeviceUpdate(board_id=board_id, dimming_channel=dimming_channel)
        return await self.update_light(device_id, update)

    async def _create_device(
        self, connection: Any, create: DeviceCreate, confirmed_relay_steal: bool
    ) -> DeviceMutation:
        displaced = await self._steal_relay_if_confirmed(
            connection, create.channel, None, confirmed_relay_steal
        )
        device = await self._device_repo.create_device_locked(connection, create)
        return DeviceMutation(device=device, displaced_device_id=displaced)

    async def _create_light(
        self, connection: Any, create: LightDeviceCreate, confirmed_relay_steal: bool
    ) -> DeviceMutation:
        dfr_owner = await self._device_repo.assert_dfr_free(
            connection, create.board_id, create.dimming_channel
        )
        if dfr_owner is not None:
            raise RegistryConflictError("DFR", dfr_owner)
        displaced = await self._steal_relay_if_confirmed(
            connection, create.relay_channel, None, confirmed_relay_steal
        )
        per_room_index = create.per_room_index
        if per_room_index is None:
            rows = await connection.fetch(
                """SELECT per_room_index FROM device_registry
                   WHERE location = $1 AND device_type = 'light' FOR UPDATE""",
                create.room,
            )
            per_room_index = max((row["per_room_index"] for row in rows), default=0) + 1
        light = await self._device_repo.create_light_locked(
            connection,
            board_id=create.board_id,
            dimming_channel=create.dimming_channel,
            room=create.room,
            display_name=create.display_name,
            per_room_index=per_room_index,
            relay_channel=create.relay_channel,
        )
        if light.device_id is None:
            raise RuntimeError("Created light did not receive a device_id")
        await self._create_default_targets(
            connection, light.device_id, light.location, light.cluster
        )
        return DeviceMutation(device=light, displaced_device_id=displaced)

    async def _update_device(
        self,
        connection: Any,
        device_id: int,
        update: DeviceUpdate,
        confirmed_relay_steal: bool,
    ) -> DeviceMutation:
        current = await self._locked_device(connection, device_id, "non-light")
        fields = update.model_dump(exclude_unset=True)
        displaced = await self._prepare_relay_change(
            connection,
            current,
            fields.get("channel"),
            "channel" in fields,
            confirmed_relay_steal,
        )
        if displaced is not None:
            fields.pop("channel")
        updated = await self._device_repo.update_device_locked(
            connection, device_id, DeviceUpdate(**fields)
        )
        return DeviceMutation(device=updated, displaced_device_id=displaced)

    async def _update_registry_device(
        self,
        connection: Any,
        device_id: int,
        fields: dict[str, Any],
        confirmed_relay_steal: bool,
    ) -> DeviceMutation:
        current = await self._locked_device(connection, device_id, "any")
        try:
            if current["device_type"] == "light":
                return await self._update_light(
                    connection,
                    device_id,
                    LightDeviceUpdate(**fields),
                    confirmed_relay_steal,
                )
            return await self._update_device(
                connection,
                device_id,
                DeviceUpdate(**fields),
                confirmed_relay_steal,
            )
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

    async def _update_light(
        self,
        connection: Any,
        device_id: int,
        update: LightDeviceUpdate,
        confirmed_relay_steal: bool,
    ) -> DeviceMutation:
        current = await self._locked_device(connection, device_id, "light")
        fields = update.model_dump(exclude_unset=True)
        if "board_id" in fields:
            dfr_owner = await self._device_repo.assert_dfr_free(
                connection, fields["board_id"], fields["dimming_channel"], device_id
            )
            if dfr_owner is not None:
                raise RegistryConflictError("DFR", dfr_owner)
            await self._safe_zero_dfr(current)
            fields["dimming_board_id"] = fields.pop("board_id")
        displaced = await self._prepare_relay_change(
            connection,
            current,
            fields.get("relay_channel"),
            "relay_channel" in fields,
            confirmed_relay_steal,
        )
        if displaced is not None:
            fields.pop("relay_channel")
        updated = await self._device_repo.update_light_locked(connection, device_id, fields)
        if updated is None:
            raise RegistryNotFoundError(device_id)
        if updated.device_name != current["device_name"]:
            await connection.execute(
                """UPDATE effective_setpoints SET device_name = $1
                   WHERE location = $2 AND cluster = $3 AND device_name = $4""",
                updated.device_name,
                current["location"],
                current["cluster"],
                current["device_name"],
            )
        return DeviceMutation(device=updated, displaced_device_id=displaced)

    async def _delete_device(
        self, connection: Any, device_id: int, expected_type: str
    ) -> DeviceMutation:
        current = await self._locked_device(connection, device_id, expected_type)
        await self._safe_turn_off_relay(current.get("channel"))
        await self._safe_zero_dfr(current)
        await self._device_repo.delete_current_state_locked(connection, current)
        if expected_type == "light":
            deleted = await self._device_repo.delete_light_locked(connection, device_id)
        else:
            deleted = await self._device_repo.delete_device_locked(connection, device_id)
        if not deleted:
            raise RegistryNotFoundError(device_id)
        return DeviceMutation(device=None)

    async def _delete_registry_device(self, connection: Any, device_id: int) -> DeviceMutation:
        current = await self._locked_device(connection, device_id, "any")
        expected_type = "light" if current["device_type"] == "light" else "non-light"
        return await self._delete_device(connection, device_id, expected_type)

    async def _unbind_relay(self, connection: Any, device_id: int) -> DeviceMutation:
        current = await self._locked_device(connection, device_id, "any")
        await self._safe_turn_off_relay(current.get("channel"))
        await self._device_repo.clear_relay_binding(connection, device_id)
        if current["device_type"] == "light":
            updated = await self._device_repo.update_light_locked(connection, device_id, {})
        else:
            updated = await self._device_repo.update_device_locked(
                connection, device_id, DeviceUpdate()
            )
        return DeviceMutation(device=updated)

    async def _locked_device(
        self, connection: Any, device_id: int, expected_type: str
    ) -> dict[str, Any]:
        current = await self._device_repo.get_device_for_update(connection, device_id)
        if current is None:
            raise RegistryNotFoundError(device_id)
        if expected_type == "light" and current["device_type"] != "light":
            raise RegistryNotFoundError(device_id)
        if expected_type == "non-light" and current["device_type"] == "light":
            raise RegistryNotFoundError(device_id)
        return current

    async def _prepare_relay_change(
        self,
        connection: Any,
        current: dict[str, Any],
        requested_channel: int | None,
        changes_channel: bool,
        confirmed_relay_steal: bool,
    ) -> int | None:
        if not changes_channel:
            return None
        current_channel = current.get("channel")
        owner = None
        if requested_channel is not None:
            owner = await self._device_repo.find_relay_owner_for_update(
                connection, requested_channel, current["device_id"]
            )
            if owner is not None and not confirmed_relay_steal:
                raise RegistryConflictError("relay", owner)
        if current_channel != requested_channel:
            await self._safe_turn_off_relay(current_channel)
        if owner is None or requested_channel is None:
            return None
        await self._safe_turn_off_relay(requested_channel)
        await self._device_repo.assign_relay_steal(
            connection, current["device_id"], requested_channel, owner["device_id"]
        )
        return owner["device_id"]

    async def _steal_relay_if_confirmed(
        self,
        connection: Any,
        channel: int | None,
        device_id: int | None,
        confirmed_relay_steal: bool,
    ) -> int | None:
        if channel is None:
            return None
        owner = await self._device_repo.find_relay_owner_for_update(connection, channel, device_id)
        if owner is None:
            return None
        if not confirmed_relay_steal:
            raise RegistryConflictError("relay", owner)
        await self._safe_turn_off_relay(channel)
        if device_id is None:
            await self._device_repo.clear_relay_binding(connection, owner["device_id"])
        else:
            await self._device_repo.assign_relay_steal(
                connection, device_id, channel, owner["device_id"]
            )
        return owner["device_id"]

    async def _create_default_targets(
        self, connection: Any, device_id: int, location: str, cluster: str
    ) -> None:
        rows = await connection.fetch(
            """SELECT mode_id FROM mode_parameters WHERE location = $1 AND cluster = $2
               FOR SHARE""",
            location,
            cluster,
        )
        if not rows:
            raise RuntimeError(f"No mode parameters exist for {location}/{cluster}")
        for row in rows:
            await connection.execute(
                """INSERT INTO light_target_intensity (device_id, mode_id, target_intensity, updated_at)
                   VALUES ($1, $2, 10.0, NOW()) ON CONFLICT (device_id, mode_id) DO NOTHING""",
                device_id,
                row["mode_id"],
            )

    async def _safe_turn_off_relay(self, channel: Any) -> None:
        if not isinstance(channel, int):
            return
        if not await self._relay_manager.set_channel_state(channel, 0):
            raise SafeOutputError(f"relay channel {channel}")

    async def _safe_zero_dfr(self, device: dict[str, Any]) -> None:
        board_id = device.get("dimming_board_id")
        channel = device.get("dimming_channel")
        if not isinstance(board_id, int) or not isinstance(channel, int):
            return
        if self._dfr0971_manager is None or not self._dfr0971_manager.set_intensity(
            board_id, channel, 0.0
        ):
            raise SafeOutputError(f"DFR {board_id}/{channel}")
