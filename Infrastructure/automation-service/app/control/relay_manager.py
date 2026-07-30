"""Relay manager backed by the installed immutable runtime device snapshot."""

import asyncio
from collections.abc import Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from app.automation.interlock_manager import InterlockManager
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import DeviceKey, RuntimeDeviceSnapshot
from app.hardware.mcp23017 import MCP23017Driver
from app.repositories.control_actions import ControlActionRepository
from shared.infra_logging import get_logger

logger = get_logger(__name__)

_MISMATCH_ALARM_AFTER_SECONDS = 5.0
_STALE_WARNING_AFTER_SECONDS = 5.0
_STALE_CRITICAL_AFTER_SECONDS = 30.0
_SYSTEM_HARDWARE_LOCATION = "System"
_SYSTEM_HARDWARE_CLUSTER = "hardware"


@dataclass(frozen=True, slots=True)
class RelayControlState:
    """Desired physical relay command and its independent MCP reconciliation state."""

    desired_state: int
    mode: str
    expires_at: datetime | None = None
    prior_mode: str | None = None
    syncing: bool = False
    mismatch_started_at: datetime | None = None
    last_command_succeeded: bool | None = None
    recovery_pending: bool = False


class RelayManager:
    """Manages relay control with device mapping, interlocks, and safety features."""

    def __init__(
        self,
        mcp23017: MCP23017Driver,
        runtime_device_registry: RuntimeDeviceRegistry,
        interlock_manager: InterlockManager,
        relay_board_state_manager: RelayBoardStateManager | None = None,
        control_action_repository: ControlActionRepository | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        """Initialize relay manager.

        Args:
            mcp23017: MCP23017 driver instance
            runtime_device_registry: Installed immutable device snapshot provider
            interlock_manager: Interlock manager instance
        """
        self.mcp23017 = mcp23017
        self._runtime_device_registry = runtime_device_registry
        self.interlock_manager = interlock_manager
        self._relay_board_state_manager = relay_board_state_manager
        self._control_action_repository = control_action_repository
        self._now = now
        self._active_snapshot: ContextVar[RuntimeDeviceSnapshot | None] = ContextVar(
            "active_runtime_device_snapshot", default=None
        )

        # Track current states
        self._current_states: dict[
            tuple[str, str, str], int
        ] = {}  # (location, cluster, device) -> state
        self._state_identities: dict[DeviceKey, int] = {}
        self._channel_control_states: dict[int, RelayControlState] = {}
        self._active_mismatch_alarms: set[tuple[str, str, str]] = set()
        self._stale_alarm_severity: dict[tuple[str, str], str] = {}
        self._runtime_device_registry.subscribe(self.install_snapshot)

    def install_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> None:
        """Keep state only for identities that survived the atomic replacement."""
        identities = dict(snapshot.by_device)
        retained_keys = {
            key
            for key, device_id in self._state_identities.items()
            if identities.get(key) == device_id
        }
        self._current_states = {
            key: state for key, state in self._current_states.items() if key in retained_keys
        }
        self._state_identities = identities

    def get_channel_control_state(self, channel: int) -> RelayControlState | None:
        """Return desired command state for one physical channel without claiming it was observed."""
        return self._channel_control_states.get(channel)

    def get_channel_control_states(self) -> dict[int, RelayControlState]:
        """Return a copy of all desired relay command states for read projections."""
        return self._channel_control_states.copy()

    def record_command_state(
        self,
        key: DeviceKey,
        mode: str,
        expires_at: datetime | None,
        prior_mode: str | None,
    ) -> None:
        """Project assigned command authority into its channel's physical command read state."""
        channel = self.get_channel(*key)
        if channel is None:
            return
        state = self._channel_control_states.get(channel)
        desired_state = (
            state.desired_state if state is not None else self._current_states.get(key, 0)
        )
        self._channel_control_states[channel] = replace(
            state or RelayControlState(desired_state=desired_state, mode=mode),
            desired_state=desired_state,
            mode=mode,
            expires_at=expires_at,
            prior_mode=prior_mode,
        )

    def bind_snapshot(self, snapshot: RuntimeDeviceSnapshot) -> Token[RuntimeDeviceSnapshot | None]:
        """Bind one snapshot to the current control task for the entire tick."""
        return self._active_snapshot.set(snapshot)

    def release_snapshot(self, token: Token[RuntimeDeviceSnapshot | None]) -> None:
        """Release the tick-local snapshot after control processing completes."""
        self._active_snapshot.reset(token)

    def _snapshot(self) -> RuntimeDeviceSnapshot:
        """Use the tick-local reference when present, otherwise the installed reference."""
        return self._active_snapshot.get() or self._runtime_device_registry.snapshot

    def get_channel(self, location: str, cluster: str, device_name: str) -> int | None:
        """Get channel number for a device.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name

        Returns:
            Channel number or None if not found
        """
        info = self._snapshot().device_info.get((location, cluster, device_name))
        channel = info.get("channel") if info is not None else None
        return channel if isinstance(channel, int) else None

    def get_device_info(
        self, location: str, cluster: str, device_name: str
    ) -> dict[str, Any] | None:
        """Get device info.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name

        Returns:
            Device info dict or None
        """
        key = (location, cluster, device_name)
        info = self._snapshot().device_info.get(key)
        return dict(info) if info is not None else None

    def get_devices_for_location_cluster(
        self, location: str, cluster: str
    ) -> dict[str, dict[str, Any]]:
        """Return the installed registry projection for one device cluster."""
        devices = self._snapshot().hierarchy.get(location, {}).get(cluster, {})
        return {device_name: dict(device_info) for device_name, device_info in devices.items()}

    async def set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        state: int,
        mode: str = "auto",
        check_interlock: bool = True,
    ) -> tuple[bool, str | None]:
        """Set device state (ON/OFF).

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            state: 0 = OFF, 1 = ON
            mode: Control mode ('manual', 'auto', 'scheduled')
            check_interlock: Whether to check interlocks

        Returns:
            Tuple of (success, reason)
        """
        key = (location, cluster, device_name)
        snapshot = self._snapshot()
        device_info = snapshot.device_info.get(key)
        channel = device_info.get("channel") if device_info is not None else None

        if channel is None:
            return (False, f"Device not found: {location}/{cluster}/{device_name}")

        if state == 1 and self._relay_observation_is_stale():
            return (False, "Relay observation is stale; only OFF commands are allowed")

        # Check interlock if turning ON
        if state == 1 and check_interlock:
            can_turn_on, reason = self.interlock_manager.check_interlock(
                location,
                cluster,
                device_name,
                self._current_states,
                requested_load=None,
                snapshot=snapshot,
            )
            if not can_turn_on:
                return (False, reason or "Interlock blocked")

        # Set hardware channel
        success = await asyncio.to_thread(self.mcp23017.set_channel, channel, state == 1)

        if success:
            self._current_states[key] = state
            self._record_desired_command(channel, state, mode, succeeded=True)
            if self._relay_board_state_manager is not None:
                await self._relay_board_state_manager.on_write_done()
            self._reconcile_observed_channels()
            logger.debug(
                f"Device {location}/{cluster}/{device_name} (channel {channel}) set to {'ON' if state == 1 else 'OFF'}"
            )
            return (True, None)
        await self._record_hardware_failure(key, channel, state, mode)
        self._record_failed_command(channel)
        return (False, "Hardware error")

    def get_device_state(self, location: str, cluster: str, device_name: str) -> int | None:
        """Get current device state.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name

        Returns:
            State (0 = OFF, 1 = ON) or None if not found
        """
        key = (location, cluster, device_name)
        return self._current_states.get(key)

    def get_device_mode(self, location: str, cluster: str, device_name: str) -> str | None:
        """Retain the legacy accessor without introducing mode authority here."""
        del location, cluster, device_name
        return None

    def get_all_states(self) -> dict[tuple[str, str, str], int]:
        """Get all device states.

        Returns:
            Dict mapping (location, cluster, device) -> state
        """
        return self._current_states.copy()

    async def set_channel_state(self, channel: int, state: int) -> bool:
        """Set channel state directly by channel number.

        Args:
            channel: Channel number (0-15)
            state: 0 = OFF, 1 = ON

        Returns:
            True if successful, False otherwise.
        """
        if state == 1 and self._relay_observation_is_stale():
            return False

        success = await asyncio.to_thread(self.mcp23017.set_channel, channel, state == 1)
        if success:
            # Update internal state if this channel is mapped to a device
            device_key = self._snapshot().by_channel.get(channel)
            if device_key:
                self._current_states[device_key] = state
            self._record_desired_command(channel, state, "manual", succeeded=True)
            if self._relay_board_state_manager is not None:
                await self._relay_board_state_manager.on_write_done()
            self._reconcile_observed_channels()
            logger.info(f"Channel {channel} set to {'ON' if state == 1 else 'OFF'}")
        else:
            device_key = self._snapshot().by_channel.get(channel)
            if device_key is not None:
                await self._record_hardware_failure(device_key, channel, state, "manual")
            self._record_failed_command(channel)
        return success

    def is_channel_observed_off(self, channel: int) -> bool:
        """Return whether the latest successful board sample proves this channel is OFF."""
        if self._relay_board_state_manager is None:
            return False
        snapshot = self._relay_board_state_manager.get_snapshot()
        if snapshot.channels is None or channel >= len(snapshot.channels):
            return False
        return not snapshot.channels[channel]

    async def command_channel_off_and_observe(self, channel: int) -> bool:
        """Command a channel OFF and require its immediately following board sample to agree."""
        success = await asyncio.to_thread(self.mcp23017.set_channel, channel, False)
        if not success or self._relay_board_state_manager is None:
            return False
        self._record_desired_command(channel, 0, "safe", succeeded=True)
        if not await self._relay_board_state_manager.on_write_done():
            return False
        if not self.is_channel_observed_off(channel):
            return False
        device_key = self._snapshot().by_channel.get(channel)
        if device_key is not None:
            self._current_states[device_key] = 0
        return True

    async def set_all_channels(self, states: tuple[bool, ...]) -> bool:
        """Set all MCP outputs and sample the board once after a successful write."""
        if len(states) != 16:
            return False
        if self._relay_observation_is_stale() and any(states):
            return False
        success = await asyncio.to_thread(self.mcp23017.set_all_channels, list(states))
        if success:
            for channel, state in enumerate(states):
                self._record_desired_command(channel, int(state), "safe", succeeded=True)
            if self._relay_board_state_manager is not None:
                await self._relay_board_state_manager.on_write_done()
            self._reconcile_observed_channels()
        return success

    async def retry_unresolved(self) -> None:
        """Retry each desired/observed disagreement at most once for the current tick."""
        self._reconcile_observed_channels()
        if self._relay_observation_is_stale():
            return
        for channel, state in tuple(self._channel_control_states.items()):
            if not state.syncing:
                continue
            success = await asyncio.to_thread(
                self.mcp23017.set_channel, channel, state.desired_state == 1
            )
            self._channel_control_states[channel] = replace(state, last_command_succeeded=success)
            if success and self._relay_board_state_manager is not None:
                await self._relay_board_state_manager.on_write_done()
            self._reconcile_observed_channels()

    async def evaluate_observation(
        self, alarm_manager: Any, device_command_service: Any | None = None
    ) -> None:
        """Raise or clear only transition alarms after the tick's MCP sample completes."""
        self._reconcile_observed_channels()
        if self._relay_observation_is_stale():
            self._evaluate_stale_alarms(alarm_manager)
            return

        self._clear_stale_alarms(alarm_manager)
        for channel, state in tuple(self._channel_control_states.items()):
            location, cluster, device_name, alarm_name = self._alarm_target(channel)
            alarm_key = (location, cluster, alarm_name)
            if state.recovery_pending:
                alarm_manager.clear_alarm(location, cluster, alarm_name)
                self._active_mismatch_alarms.discard(alarm_key)
                mode = self._recovery_mode(
                    location, cluster, device_name, state, device_command_service
                )
                await self._record_recovery(location, cluster, device_name, channel, state, mode)
                self._channel_control_states[channel] = replace(state, recovery_pending=False)
                continue
            if (
                state.syncing
                and state.mismatch_started_at is not None
                and (self._now() - state.mismatch_started_at).total_seconds()
                >= _MISMATCH_ALARM_AFTER_SECONDS
                and alarm_key not in self._active_mismatch_alarms
            ):
                severity = "critical" if device_name is not None else "warning"
                alarm_manager.raise_alarm(
                    location,
                    cluster,
                    alarm_name,
                    severity,
                    f"Relay channel {channel} desired {state.desired_state} disagrees with MCP GPIO",
                )
                self._active_mismatch_alarms.add(alarm_key)

    def _record_desired_command(
        self, channel: int, state: int, mode: str, *, succeeded: bool
    ) -> None:
        """Replace command metadata while preserving a mismatch start for unchanged disagreement."""
        previous = self._channel_control_states.get(channel)
        self._channel_control_states[channel] = RelayControlState(
            desired_state=state,
            mode=mode,
            expires_at=previous.expires_at if previous is not None else None,
            prior_mode=previous.prior_mode if previous is not None else None,
            last_command_succeeded=succeeded,
            recovery_pending=previous.syncing if previous is not None else False,
        )

    def _record_failed_command(self, channel: int) -> None:
        """Expose failure outcome without fabricating a new desired physical command."""
        state = self._channel_control_states.get(channel)
        if state is not None:
            self._channel_control_states[channel] = replace(state, last_command_succeeded=False)

    def _reconcile_observed_channels(self) -> None:
        """Update syncing transitions from fresh MCP GPIO without mutating observed values."""
        if self._relay_board_state_manager is None:
            return
        if self._relay_board_state_manager.get_freshness().status == "STALE":
            return
        channels = self._relay_board_state_manager.get_snapshot().channels
        if channels is None:
            return
        for channel, state in tuple(self._channel_control_states.items()):
            observed_state = int(channels[channel])
            if observed_state == state.desired_state:
                self._channel_control_states[channel] = replace(
                    state,
                    syncing=False,
                    mismatch_started_at=None,
                    recovery_pending=state.recovery_pending or state.syncing,
                )
                continue
            if state.syncing:
                continue
            self._channel_control_states[channel] = replace(
                state,
                syncing=True,
                mismatch_started_at=self._now(),
                recovery_pending=False,
            )

    def _relay_observation_is_stale(self) -> bool:
        return (
            self._relay_board_state_manager is not None
            and self._relay_board_state_manager.get_freshness().status == "STALE"
        )

    def _alarm_target(self, channel: int) -> tuple[str, str, str | None, str]:
        device_key = self._snapshot().by_channel.get(channel)
        if device_key is None:
            return (
                _SYSTEM_HARDWARE_LOCATION,
                _SYSTEM_HARDWARE_CLUSTER,
                None,
                f"unassigned_relay_mismatch_channel_{channel}",
            )
        location, cluster, device_name = device_key
        return location, cluster, device_name, f"relay_mismatch_channel_{channel}"

    def _evaluate_stale_alarms(self, alarm_manager: Any) -> None:
        board_state_manager = self._relay_board_state_manager
        if board_state_manager is None:
            return
        freshness = board_state_manager.get_freshness()
        if freshness.stale_since is None:
            return
        stale_seconds = (self._now() - freshness.stale_since).total_seconds()
        if stale_seconds < _STALE_WARNING_AFTER_SECONDS:
            return
        severity = "critical" if stale_seconds >= _STALE_CRITICAL_AFTER_SECONDS else "warning"
        rooms = {
            (location, cluster) for location, cluster, _device_name in self._snapshot().by_device
        }
        for location, cluster in rooms:
            room_key = (location, cluster)
            if self._stale_alarm_severity.get(room_key) == severity:
                continue
            alarm_manager.raise_alarm(
                location,
                cluster,
                "relay_board_stale",
                severity,
                "MCP relay-board observation is stale; only OFF commands are permitted",
            )
            self._stale_alarm_severity[room_key] = severity

    def _clear_stale_alarms(self, alarm_manager: Any) -> None:
        for location, cluster in tuple(self._stale_alarm_severity):
            alarm_manager.clear_alarm(location, cluster, "relay_board_stale")
            del self._stale_alarm_severity[(location, cluster)]

    async def _record_recovery(
        self,
        location: str,
        cluster: str,
        device_name: str | None,
        channel: int,
        state: RelayControlState,
        mode: str,
    ) -> None:
        if self._control_action_repository is None:
            return
        await self._control_action_repository.record_relay_recovery(
            location,
            cluster,
            device_name or f"relay_channel_{channel}",
            channel,
            state.desired_state,
            mode,
        )

    @staticmethod
    def _recovery_mode(
        location: str,
        cluster: str,
        device_name: str | None,
        state: RelayControlState,
        device_command_service: Any | None,
    ) -> str:
        if device_name is None or device_command_service is None:
            return state.mode
        return device_command_service.get_command_state(location, cluster, device_name).mode

    async def _record_hardware_failure(
        self, key: tuple[str, str, str], channel: int, requested_state: int, mode: str
    ) -> None:
        if self._control_action_repository is None:
            return
        prior_state = self._current_states.get(key, 0)
        await self._control_action_repository.record_failed_control_action(
            key[0], key[1], key[2], channel, prior_state, mode, requested_state
        )

    async def restore_states(self, states: dict[tuple[str, str, str], dict[str, Any]]):
        """Restore device states from database.

        Args:
            states: Dict mapping (location, cluster, device) -> {state, mode, channel}
        """
        for key, info in states.items():
            location, cluster, device_name = key
            state = info.get("state", 0)
            mode = info.get("mode", "auto")

            # Set state without interlock check (restoring from database)
            success, reason = await self.set_device_state(
                location, cluster, device_name, state, mode, check_interlock=False
            )
            if not success:
                logger.warning(
                    f"Failed to restore state for {location}/{cluster}/{device_name}: {reason}"
                )
