"""Atomic command authority for relay-assigned registry devices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final, Literal, final

import anyio
from pydantic import BaseModel, ConfigDict, Field

from app.control.relay_manager import RelayManager
from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import RuntimeDeviceSnapshot
from app.redis.schema import relay_raw_override_key
from app.redis_client import AutomationRedisClient
from app.repositories.control_actions import ControlActionRepository

CommandMode = Literal["auto", "scheduled", "manual_off", "timed_on"]
_MAX_DURATION_SECONDS: Final = 3600
_RAW_RELAY_CHANNELS: Final = tuple(range(16))


class AutoCommand(BaseModel):
    """Release an assigned device back to automatic control."""

    model_config = ConfigDict(frozen=True)

    action: Literal["AUTO"] = "AUTO"
    reason: str = "Manual control released"


class ManualOffCommand(BaseModel):
    """Hold an assigned device safely OFF until released."""

    model_config = ConfigDict(frozen=True)

    action: Literal["MANUAL_OFF"] = "MANUAL_OFF"
    reason: str = "Manual OFF"


class TimedOnCommand(BaseModel):
    """Turn an assigned device ON until its bounded expiry."""

    model_config = ConfigDict(frozen=True)

    action: Literal["TIMED_ON"] = "TIMED_ON"
    duration_seconds: int = Field(ge=1, le=_MAX_DURATION_SECONDS)
    reason: str = "Timed manual ON"


DeviceCommand = AutoCommand | ManualOffCommand | TimedOnCommand
DeviceCommandRequest = Annotated[
    DeviceCommand,
    Field(discriminator="action"),
]


@dataclass(frozen=True, slots=True)
class DeviceCommandState:
    """Current in-process command state for one strict registry identity."""

    mode: CommandMode
    prior_mode: CommandMode | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeviceCommandResult:
    """Result returned after a command state transition succeeds."""

    mode: CommandMode
    expires_at: datetime | None


class DeviceCommandNotAssignedError(RuntimeError):
    """Raised when a command does not resolve to a strict relay assignment."""


class DeviceCommandHardwareError(RuntimeError):
    """Raised when the relay command or its compensating write fails."""


class DeviceCommandAuditError(RuntimeError):
    """Raised when an otherwise successful command cannot be audited."""


@final
class DeviceCommandService:
    """Own assigned-device command mode, expiry, and prior-mode restoration in process."""

    def __init__(
        self,
        runtime_device_registry: RuntimeDeviceRegistry,
        relay_manager: RelayManager,
        control_action_repository: ControlActionRepository,
        automation_redis: AutomationRedisClient | None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._runtime_device_registry = runtime_device_registry
        self._relay_manager = relay_manager
        self._control_action_repository = control_action_repository
        self._automation_redis = automation_redis
        self._now = now
        self._states: dict[tuple[str, str, str], DeviceCommandState] = {}
        self._state_identities: dict[tuple[str, str, str], int] = {}
        self._lock = anyio.Lock()
        self._runtime_device_registry.subscribe(self.install_snapshot)

    def install_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Retain valid command state and initialize newly assigned identities as AUTO."""
        self._states = {
            key: (
                self._states.get(key, DeviceCommandState(mode="auto"))
                if self._state_identities.get(key) == device_id
                else DeviceCommandState(mode="auto")
            )
            for key, device_id in snapshot.by_device.items()
        }
        self._state_identities = dict(snapshot.by_device)

    async def initialize_startup(self) -> None:
        """Discard restart-time commands and raw timers before the first control tick."""
        self.install_snapshot(self._runtime_device_registry.snapshot)
        async with self._lock:
            self._states = {
                key: DeviceCommandState(mode="auto")
                for key in self._runtime_device_registry.snapshot.by_device
            }
            self._state_identities = dict(self._runtime_device_registry.snapshot.by_device)
        redis_client = (
            self._automation_redis.redis_client if self._automation_redis is not None else None
        )
        if redis_client is None:
            return
        for channel in _RAW_RELAY_CHANNELS:
            _ = await anyio.to_thread.run_sync(redis_client.delete, relay_raw_override_key(channel))

    async def execute(
        self,
        location: str,
        cluster: str,
        device_name: str,
        command: DeviceCommand,
    ) -> DeviceCommandResult:
        """Apply exactly one typed command after resolving the installed identity."""
        key, channel = self._assigned_identity(location, cluster, device_name)
        async with self._lock:
            previous = self._states.get(key, DeviceCommandState(mode="auto"))
            match command:
                case AutoCommand(reason=reason):
                    return await self._release_to_auto(key, channel, previous, reason)
                case ManualOffCommand(reason=reason):
                    return await self._write_command(
                        key,
                        channel,
                        previous,
                        state=0,
                        next_state=DeviceCommandState(mode="manual_off"),
                        reason=reason,
                    )
                case TimedOnCommand(duration_seconds=duration_seconds, reason=reason):
                    expiry = self._now() + timedelta(seconds=duration_seconds)
                    return await self._write_command(
                        key,
                        channel,
                        previous,
                        state=1,
                        next_state=DeviceCommandState(
                            mode="timed_on",
                            prior_mode=previous.mode,
                            expires_at=expiry,
                        ),
                        reason=reason,
                    )

    async def expire_commands(self) -> None:
        """Restore every elapsed timed command's captured mode without a database lookup."""
        now = self._now()
        async with self._lock:
            for key, state in tuple(self._states.items()):
                if state.mode != "timed_on" or state.expires_at is None or state.expires_at > now:
                    continue
                restored_mode = state.prior_mode or "auto"
                restored_state = DeviceCommandState(mode=restored_mode)
                self._states[key] = restored_state
                self._relay_manager.record_command_state(key, restored_mode, None, None)

    def get_command_state(
        self, location: str, cluster: str, device_name: str
    ) -> DeviceCommandState:
        """Return the command state owned by one installed registry identity."""
        key, _channel = self._assigned_identity(location, cluster, device_name)
        return self._states.get(key, DeviceCommandState(mode="auto"))

    def accepts_automatic_control(self, location: str, cluster: str, device_name: str) -> bool:
        """Report whether automation may command the current identity this tick."""
        return self.get_command_state(location, cluster, device_name).mode in {"auto", "scheduled"}

    def record_automatic_mode(
        self, location: str, cluster: str, device_name: str, mode: Literal["auto", "scheduled"]
    ) -> None:
        """Record automatic authority only when no manual command currently owns the device."""
        key, _channel = self._assigned_identity(location, cluster, device_name)
        previous = self._states.get(key, DeviceCommandState(mode="auto"))
        if previous.mode in {"auto", "scheduled"}:
            self._states[key] = DeviceCommandState(mode=mode)

    def is_assigned_channel(self, channel: int) -> bool:
        """Return whether the installed strict snapshot assigns this raw relay channel."""
        return channel in self._runtime_device_registry.snapshot.by_channel

    def _assigned_identity(
        self, location: str, cluster: str, device_name: str
    ) -> tuple[tuple[str, str, str], int]:
        key = (location, cluster, device_name)
        snapshot = self._runtime_device_registry.snapshot
        channel = snapshot.device_info.get(key, {}).get("channel")
        if key not in snapshot.by_device or not isinstance(channel, int):
            raise DeviceCommandNotAssignedError(
                f"No assigned relay identity for {location}/{cluster}/{device_name}"
            )
        return key, channel

    async def _release_to_auto(
        self,
        key: tuple[str, str, str],
        channel: int,
        previous: DeviceCommandState,
        reason: str,
    ) -> DeviceCommandResult:
        current_state = self._relay_manager.get_device_state(*key) or 0
        audit_written = await self._control_action_repository.log_control_action(
            *key,
            channel,
            current_state,
            current_state,
            "auto",
            reason,
        )
        if not audit_written:
            raise DeviceCommandAuditError("Could not audit AUTO command")
        del previous
        self._states[key] = DeviceCommandState(mode="auto")
        self._relay_manager.record_command_state(key, "auto", None, None)
        return DeviceCommandResult(mode="auto", expires_at=None)

    async def _write_command(
        self,
        key: tuple[str, str, str],
        channel: int,
        previous: DeviceCommandState,
        *,
        state: int,
        next_state: DeviceCommandState,
        reason: str,
    ) -> DeviceCommandResult:
        prior_relay_state = self._relay_manager.get_device_state(*key) or 0
        success, hardware_reason = await self._relay_manager.set_device_state(
            *key, state, next_state.mode
        )
        if not success:
            raise DeviceCommandHardwareError(hardware_reason or "Relay command failed")
        audit_written = await self._control_action_repository.log_control_action(
            *key,
            channel,
            prior_relay_state,
            state,
            next_state.mode,
            reason,
            manual_expires_at=next_state.expires_at,
        )
        if not audit_written:
            rollback_success, rollback_reason = await self._relay_manager.set_device_state(
                *key, prior_relay_state, previous.mode
            )
            if not rollback_success:
                raise DeviceCommandHardwareError(
                    rollback_reason or "Could not compensate unaudited relay command"
                )
            raise DeviceCommandAuditError("Could not audit relay command")
        self._states[key] = next_state
        self._relay_manager.record_command_state(
            key, next_state.mode, next_state.expires_at, next_state.prior_mode
        )
        return DeviceCommandResult(mode=next_state.mode, expires_at=next_state.expires_at)
