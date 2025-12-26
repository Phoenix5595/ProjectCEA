"""Relay manager for device-to-channel mapping and state management."""
import logging
from typing import Dict, Optional, Tuple, Set
from app.hardware.mcp23017 import MCP23017Driver
from app.automation.interlock_manager import InterlockManager

logger = logging.getLogger(__name__)


class RelayManager:
    """Manages relay control with device mapping, interlocks, and safety features."""
    
    def __init__(
        self,
        mcp23017: MCP23017Driver,
        device_config: Dict[str, any],
        interlock_manager: InterlockManager
    ):
        """Initialize relay manager.
        
        Args:
            mcp23017: MCP23017 driver instance
            device_config: Device configuration from config
            interlock_manager: Interlock manager instance
        """
        self.mcp23017 = mcp23017
        self.device_config = device_config
        self.interlock_manager = interlock_manager
        
        # Build device lookup dictionaries
        self._device_map: Dict[Tuple[str, str, str], int] = {}  # (location, cluster, device) -> channel
        self._channel_map: Dict[int, Tuple[str, str, str]] = {}  # channel -> (location, cluster, device)
        self._device_info: Dict[Tuple[str, str, str], Dict] = {}  # (location, cluster, device) -> device info
        
        self._build_device_maps()
        
        # Track current states
        self._current_states: Dict[Tuple[str, str, str], int] = {}  # (location, cluster, device) -> state
        self._current_modes: Dict[Tuple[str, str, str], str] = {}  # (location, cluster, device) -> mode
    
    def _build_device_maps(self):
        """Build device-to-channel mapping from config."""
        for location, clusters in self.device_config.items():
            for cluster, devices in clusters.items():
                for device_name, device_info in devices.items():
                    channel = device_info.get('channel')
                    if channel is not None:
                        key = (location, cluster, device_name)
                        self._device_map[key] = channel
                        self._channel_map[channel] = key
                        self._device_info[key] = device_info
    
    def get_channel(self, location: str, cluster: str, device_name: str) -> Optional[int]:
        """Get channel number for a device.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
        
        Returns:
            Channel number or None if not found
        """
        key = (location, cluster, device_name)
        return self._device_map.get(key)
    
    def get_device_info(self, location: str, cluster: str, device_name: str) -> Optional[Dict]:
        """Get device info.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
        
        Returns:
            Device info dict or None
        """
        key = (location, cluster, device_name)
        return self._device_info.get(key)
    
    def set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        state: int,
        mode: str = 'auto',
        check_interlock: bool = True
    ) -> Tuple[bool, Optional[str]]:
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
        channel = self._device_map.get(key)
        
        if channel is None:
            return (False, f"Device not found: {location}/{cluster}/{device_name}")
        
        # Check interlock if turning ON
        if state == 1 and check_interlock:
            can_turn_on, reason = self.interlock_manager.check_interlock(
                location, cluster, device_name, self._current_states, requested_load=None
            )
            if not can_turn_on:
                return (False, reason or "Interlock blocked")
        
        # Set hardware channel
        success = self.mcp23017.set_channel(channel, state == 1)
        
        if success:
            old_state = self._current_states.get(key, 0)
            self._current_states[key] = state
            self._current_modes[key] = mode
            logger.info(f"Device {location}/{cluster}/{device_name} (channel {channel}) set to {'ON' if state == 1 else 'OFF'}")
            return (True, None)
        else:
            return (False, "Hardware error")
    
    def get_device_state(self, location: str, cluster: str, device_name: str) -> Optional[int]:
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
    
    def get_device_mode(self, location: str, cluster: str, device_name: str) -> Optional[str]:
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
    
    def get_all_states(self) -> Dict[Tuple[str, str, str], int]:
        """Get all device states.
        
        Returns:
            Dict mapping (location, cluster, device) -> state
        """
        return self._current_states.copy()
    
    def restore_states(self, states: Dict[Tuple[str, str, str], Dict[str, any]]):
        """Restore device states from database.
        
        Args:
            states: Dict mapping (location, cluster, device) -> {state, mode, channel}
        """
        for key, info in states.items():
            location, cluster, device_name = key
            state = info.get('state', 0)
            mode = info.get('mode', 'auto')
            
            # Set state without interlock check (restoring from database)
            success, reason = self.set_device_state(
                location, cluster, device_name, state, mode, check_interlock=False
            )
            if not success:
                logger.warning(f"Failed to restore state for {location}/{cluster}/{device_name}: {reason}")

