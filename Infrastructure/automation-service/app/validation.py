"""Validation functions for automation service."""
import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def validate_pid_parameters(
    kp: Optional[float],
    ki: Optional[float],
    kd: Optional[float],
    device_type: str,
    config: Dict[str, Any]
) -> Tuple[bool, Optional[str], Dict[str, float]]:
    """Validate PID parameters against configurable limits.
    
    Args:
        kp: Proportional gain (optional, None means don't change)
        ki: Integral gain (optional, None means don't change)
        kd: Derivative gain (optional, None means don't change)
        device_type: Device type (e.g., 'heater', 'co2')
        config: ConfigLoader instance or config dict with pid_limits
    
    Returns:
        Tuple of (is_valid, error_message, validated_values)
        validated_values contains only the non-None parameters
    """
    # Get PID limits from config
    if hasattr(config, 'get'):
        # ConfigLoader instance
        pid_limits = config.get('control.pid_limits', {})
    else:
        # Dict
        pid_limits = config.get('control', {}).get('pid_limits', {})
    
    # Get limits for this device type
    device_limits = pid_limits.get(device_type, {})
    
    if not device_limits:
        # No limits defined for this device type - allow any values
        logger.warning(f"No PID limits defined for device_type '{device_type}', allowing all values")
        validated = {}
        if kp is not None:
            validated['kp'] = kp
        if ki is not None:
            validated['ki'] = ki
        if kd is not None:
            validated['kd'] = kd
        return True, None, validated
    
    # Validate each parameter
    validated = {}
    errors = []
    
    if kp is not None:
        kp_min = device_limits.get('kp_min', 0.0)
        kp_max = device_limits.get('kp_max', 100.0)
        if kp < kp_min or kp > kp_max:
            errors.append(f"Kp ({kp}) must be between {kp_min} and {kp_max}")
        else:
            validated['kp'] = kp
    
    if ki is not None:
        ki_min = device_limits.get('ki_min', 0.0)
        ki_max = device_limits.get('ki_max', 1.0)
        if ki < ki_min or ki > ki_max:
            errors.append(f"Ki ({ki}) must be between {ki_min} and {ki_max}")
        else:
            validated['ki'] = ki
    
    if kd is not None:
        kd_min = device_limits.get('kd_min', 0.0)
        kd_max = device_limits.get('kd_max', 10.0)
        if kd < kd_min or kd > kd_max:
            errors.append(f"Kd ({kd}) must be between {kd_min} and {kd_max}")
        else:
            validated['kd'] = kd
    
    if errors:
        return False, "; ".join(errors), validated
    
    return True, None, validated


def validate_setpoint(
    setpoint_type: str,
    value: float,
    config: Dict[str, Any]
) -> Tuple[bool, Optional[str]]:
    """Validate setpoint value against safety limits.
    
    Args:
        setpoint_type: Type of setpoint ('temperature', 'humidity', 'co2')
        value: Setpoint value to validate
        config: ConfigLoader instance or config dict with safety_limits
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Get safety limits from config
    if hasattr(config, 'get_safety_limits'):
        # ConfigLoader instance
        safety_limits = config.get_safety_limits()
    elif hasattr(config, 'get'):
        # ConfigLoader instance with get method
        safety_limits = config.get('control.safety_limits', {})
    else:
        # Dict
        safety_limits = config.get('control', {}).get('safety_limits', {})
    
    if not safety_limits:
        logger.warning("No safety limits defined, allowing all setpoint values")
        return True, None
    
    # Validate based on setpoint type
    if setpoint_type == 'temperature':
        min_val = safety_limits.get('min_temperature', 10.0)
        max_val = safety_limits.get('max_temperature', 35.0)
        if value < min_val or value > max_val:
            return False, f"Temperature setpoint ({value}°C) must be between {min_val}°C and {max_val}°C"
    
    elif setpoint_type == 'humidity':
        min_val = safety_limits.get('min_humidity', 30.0)
        max_val = safety_limits.get('max_humidity', 90.0)
        if value < min_val or value > max_val:
            return False, f"Humidity setpoint ({value}%) must be between {min_val}% and {max_val}%"
    
    elif setpoint_type == 'co2':
        min_val = safety_limits.get('min_co2', 400.0)
        max_val = safety_limits.get('max_co2', 2000.0)
        if value < min_val or value > max_val:
            return False, f"CO2 setpoint ({value}ppm) must be between {min_val}ppm and {max_val}ppm"
    
    elif setpoint_type == 'vpd':
        # VPD validation: typical range 0.0 - 5.0 kPa
        min_val = 0.0
        max_val = 5.0
        if value < min_val or value > max_val:
            return False, f"VPD setpoint ({value}kPa) must be between {min_val}kPa and {max_val}kPa"
    
    else:
        return False, f"Unknown setpoint type: {setpoint_type}"
    
    return True, None


def validate_device_mapping(
    channel: int,
    mcp_board_id: Optional[int],
    config: Dict[str, Any],
    existing_mappings: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None
) -> Tuple[bool, Optional[str]]:
    """Validate device mapping (channel, board ID).
    
    Args:
        channel: MCP23017 channel number (0-15)
        mcp_board_id: MCP23017 board ID (optional)
        config: ConfigLoader instance or config dict
        existing_mappings: Optional dict of existing mappings to check for duplicates
                          Format: {(location, cluster, device_name): {'channel': int, ...}}
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Validate channel range
    if channel < 0 or channel > 15:
        return False, f"Channel {channel} is out of range (0-15)"
    
    # Check for duplicate channel assignments if existing_mappings provided
    if existing_mappings:
        for (loc, clust, dev_name), mapping in existing_mappings.items():
            if mapping.get('channel') == channel:
                # Check if same board (if board_id specified)
                if mcp_board_id is not None and mapping.get('mcp_board_id') == mcp_board_id:
                    return False, f"Channel {channel} is already assigned to {loc}/{clust}/{dev_name}"
                elif mcp_board_id is None and mapping.get('mcp_board_id') is None:
                    return False, f"Channel {channel} is already assigned to {loc}/{clust}/{dev_name}"
    
    return True, None

