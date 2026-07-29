"""Relay manager backed by the installed immutable runtime device snapshot."""

import asyncio
from contextvars import ContextVar, Token
from typing import Any

from app.automation.interlock_manager import InterlockManager
from app.control.relay_board_state_manager import RelayBoardStateManager
from app.control.runtime_device_registry import RuntimeDeviceRegistry
from app.control.runtime_device_snapshot import DeviceKey, RuntimeDeviceSnapshot
from app.hardware.mcp23017 import MCP23017Driver
from app.repositories.control_actions import ControlActionRepository
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class RelayManager:
    """Manages relay control with device mapping, interlocks, and safety features."""

    def __init__(
        self,
        mcp23017: MCP23017Driver,
        runtime_device_registry: RuntimeDeviceRegistry,
        interlock_manager: InterlockManager,
        relay_board_state_manager: RelayBoardStateManager | None = None,
        control_action_repository: ControlActionRepository | None = None,
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
        self._active_snapshot: ContextVar[RuntimeDeviceSnapshot | None] = ContextVar(
            "active_runtime_device_snapshot", default=None
        )

        # Track current states
        self._current_states: dict[
            tuple[str, str, str], int
        ] = {}  # (location, cluster, device) -> state
        self._current_modes: dict[
            tuple[str, str, str], str
        ] = {}  # (location, cluster, device) -> mode
        self._state_identities: dict[DeviceKey, int] = {}
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
        self._current_modes = {
            key: mode for key, mode in self._current_modes.items() if key in retained_keys
        }
        self._state_identities = identities

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
            self._current_modes[key] = mode
            if self._relay_board_state_manager is not None:
                await self._relay_board_state_manager.on_write_done()
            logger.debug(
                f"Device {location}/{cluster}/{device_name} (channel {channel}) set to {'ON' if state == 1 else 'OFF'}"
            )
            return (True, None)
        await self._record_hardware_failure(key, channel, state, mode)
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
        """Get current device mode.

        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name

        Returns:
            Mode ('manual', 'auto', 'scheduled') or None
        """
        key = (location, cluster, device_name)
        return self._current_modes.get(key)

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
        success = await asyncio.to_thread(self.mcp23017.set_channel, channel, state == 1)
        if success:
            # Update internal state if this channel is mapped to a device
            device_key = self._snapshot().by_channel.get(channel)
            if device_key:
                self._current_states[device_key] = state
            if self._relay_board_state_manager is not None:
                await self._relay_board_state_manager.on_write_done()
            logger.info(f"Channel {channel} set to {'ON' if state == 1 else 'OFF'}")
        else:
            device_key = self._snapshot().by_channel.get(channel)
            if device_key is not None:
                await self._record_hardware_failure(device_key, channel, state, "manual")
        return success

    async def set_all_channels(self, states: tuple[bool, ...]) -> bool:
        """Set all MCP outputs and sample the board once after a successful write."""
        if len(states) != 16:
            return False
        success = await asyncio.to_thread(self.mcp23017.set_all_channels, list(states))
        if success and self._relay_board_state_manager is not None:
            await self._relay_board_state_manager.on_write_done()
        return success

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
