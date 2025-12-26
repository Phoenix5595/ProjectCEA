"""Interlock manager for safety interlocks."""
import logging
from typing import Dict, List, Optional, Set, Tuple, Callable

logger = logging.getLogger(__name__)


class InterlockManager:
    """Manages device interlocks to prevent conflicting states."""
    
    def __init__(
        self, 
        device_config: Dict[str, any], 
        interlock_rules: List[Dict[str, any]],
        device_load_callback: Optional[Callable[[str, str, str], Optional[float]]] = None
    ):
        """Initialize interlock manager.
        
        Args:
            device_config: Device configuration from config
            interlock_rules: Global interlock rules from config
            device_load_callback: Optional callback to get device load percentage (0-100)
                                  Signature: (location, cluster, device_name) -> Optional[float]
        """
        self.device_config = device_config
        self.interlock_rules = interlock_rules
        self.device_load_callback = device_load_callback
        self._build_interlock_map()
        logger.info("Initialized interlock manager with load-based interlock support")
    
    def _build_interlock_map(self):
        """Build interlock mapping from config."""
        self._interlock_map: Dict[Tuple[str, str, str], Set[str]] = {}
        
        # Build from per-device interlock_with
        for location, clusters in self.device_config.items():
            for cluster, devices in clusters.items():
                for device_name, device_info in devices.items():
                    interlock_with = device_info.get('interlock_with', [])
                    if interlock_with:
                        key = (location, cluster, device_name)
                        self._interlock_map[key] = set(interlock_with)
        
        # Add global interlock rules
        for rule in self.interlock_rules:
            when_device = rule.get('when_device')
            then_device = rule.get('then_device')
            if when_device and then_device:
                # Apply to all locations/clusters (simplified)
                # In practice, might need location/cluster context
                logger.debug(f"Global interlock: {when_device} -> {then_device}")
    
    def check_interlock(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_states: Dict[Tuple[str, str, str], int],
        requested_load: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """Check if device can be turned on or set to requested load (not blocked by interlock).
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_states: Dict mapping (location, cluster, device) -> state (0/1)
            requested_load: Optional requested load percentage (0-100) for the device being checked
        
        Returns:
            Tuple of (can_turn_on, reason)
        """
        key = (location, cluster, device_name)
        
        # Check per-device interlocks
        interlock_devices = self._interlock_map.get(key, set())
        for interlock_device in interlock_devices:
            interlock_key = (location, cluster, interlock_device)
            interlock_state = device_states.get(interlock_key, 0)
            
            # Get interlock device load if callback available
            interlock_load = None
            if self.device_load_callback and interlock_state == 1:
                interlock_load = self.device_load_callback(location, cluster, interlock_device)
            
            # Check if interlocked device is ON
            if interlock_state == 1:
                # If we have load information, check if it exceeds threshold
                if interlock_load is not None:
                    # Get max allowed load from device config (default: 0% = full interlock)
                    interlock_device_info = self.device_config.get(location, {}).get(cluster, {}).get(interlock_device, {})
                    max_allowed_load = interlock_device_info.get('interlock_max_allowed_load', 0.0)
                    
                    if interlock_load > max_allowed_load:
                        return (False, f"Interlock: {interlock_device} is at {interlock_load:.1f}% (max allowed: {max_allowed_load:.1f}%)")
                else:
                    # No load info available, use traditional ON/OFF check
                    return (False, f"Interlock: {interlock_device} is ON")
        
        # Check global interlock rules
        for rule in self.interlock_rules:
            when_device = rule.get('when_device')
            then_device = rule.get('then_device')
            max_allowed_load = rule.get('max_allowed_load', 0.0)  # Default: 0% = full interlock
            
            if when_device == device_name or then_device == device_name:
                # Check if the "when" device is ON
                when_key = (location, cluster, when_device)
                when_state = device_states.get(when_key, 0)
                
                if when_state == 1:
                    # Get load of "when" device if callback available
                    when_load = None
                    if self.device_load_callback:
                        when_load = self.device_load_callback(location, cluster, when_device)
                    
                    if then_device == device_name:
                        # This device is blocked by "when" device
                        if when_load is not None:
                            if when_load > max_allowed_load:
                                return (False, f"Global interlock: {when_device} is at {when_load:.1f}% (max allowed: {max_allowed_load:.1f}%)")
                        else:
                            return (False, f"Global interlock: {when_device} is ON")
                    
                    # Also check if requested load would violate interlock
                    if requested_load is not None and requested_load > max_allowed_load:
                        if when_load is not None and when_load > max_allowed_load:
                            return (False, f"Global interlock: Cannot set {device_name} to {requested_load:.1f}% (max allowed: {max_allowed_load:.1f}%) when {when_device} is at {when_load:.1f}%")
        
        return (True, None)

