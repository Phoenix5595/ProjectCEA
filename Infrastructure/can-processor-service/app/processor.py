"""Process and validate decoded CAN frame data."""
import logging
import math
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# State tracker for pressure values per location/cluster
# Key: (location, cluster), Value: latest pressure (hPa)
_pressure_state: Dict[Tuple[str, str], float] = defaultdict(lambda: 1013.25)  # Default sea level pressure

# State tracker for CO2 readings per sensor
# Key: sensor_name, Value: list of (value, timestamp) tuples (max 3 entries)
_co2_history: Dict[str, List[Tuple[float, datetime]]] = defaultdict(list)

# CO2 filter configuration
CO2_MIN_VALID = 300.0  # Minimum valid CO2 reading (ppm)
CO2_MAX_DROP_RATE = 200.0  # Maximum drop rate to allow (ppm/sec)
CO2_HISTORY_SIZE = 3  # Number of recent readings to track
CO2_MAX_AGE_SECONDS = 30  # Maximum age of previous reading to consider (seconds)


def validate_co2_reading(sensor_name: str, value: float, timestamp: datetime) -> bool:
    """Validate a CO2 reading to detect false 0 ppm readings.
    
    This function checks if a 0 ppm reading is legitimate by:
    1. Checking if previous reading was valid (> 300 ppm)
    2. Calculating drop rate from previous reading
    3. Allowing fast drops (> 200 ppm/sec) as legitimate
    4. Rejecting slow drops to 0 as likely false readings
    
    Args:
        sensor_name: Name of the CO2 sensor (e.g., 'co2_f', 'co2_b')
        value: CO2 reading value (ppm)
        timestamp: Timestamp of the reading
    
    Returns:
        True if reading should be accepted, False if it should be filtered out
    """
    # If not a 0 reading, always accept (and update history)
    if value > 0:
        # Update history
        history = _co2_history[sensor_name]
        history.append((value, timestamp))
        # Keep only last CO2_HISTORY_SIZE readings
        if len(history) > CO2_HISTORY_SIZE:
            history.pop(0)
        return True
    
    # For 0 readings, check against previous readings
    history = _co2_history[sensor_name]
    
    # If no history, allow (first reading or after restart)
    if not history:
        history.append((value, timestamp))
        return True
    
    # Get most recent previous reading
    prev_value, prev_timestamp = history[-1]
    
    # If previous was also 0, allow (consecutive 0s might be legitimate sensor issue)
    if prev_value == 0:
        history.append((value, timestamp))
        if len(history) > CO2_HISTORY_SIZE:
            history.pop(0)
        return True
    
    # Check if previous reading is too old (> 30 seconds)
    time_delta = (timestamp - prev_timestamp).total_seconds()
    if time_delta > CO2_MAX_AGE_SECONDS:
        # Previous reading too old, treat as no previous reading
        history.append((value, timestamp))
        if len(history) > CO2_HISTORY_SIZE:
            history.pop(0)
        return True
    
    # Previous reading was valid (> 300 ppm)
    if prev_value >= CO2_MIN_VALID:
        # Calculate drop rate
        if time_delta > 0:
            drop_rate = (prev_value - value) / time_delta
        else:
            drop_rate = float('inf')  # Same timestamp, very fast drop
        
        # If drop rate exceeds threshold, allow (legitimate fast drop)
        if drop_rate > CO2_MAX_DROP_RATE:
            logger.debug(f"CO2 {sensor_name}: Allowing 0 reading with fast drop rate "
                        f"{drop_rate:.1f} ppm/sec (previous: {prev_value:.1f} ppm)")
            history.append((value, timestamp))
            if len(history) > CO2_HISTORY_SIZE:
                history.pop(0)
            return True
        else:
            # Slow drop to 0, likely false reading - reject
            logger.warning(f"CO2 {sensor_name}: Filtering false 0 reading "
                         f"(previous: {prev_value:.1f} ppm, drop rate: {drop_rate:.1f} ppm/sec)")
            # Don't add to history - we're rejecting this reading
            return False
    
    # Previous reading was between 0 and CO2_MIN_VALID
    # Allow the 0 reading (can't determine if false)
    history.append((value, timestamp))
    if len(history) > CO2_HISTORY_SIZE:
        history.pop(0)
    return True


def validate_decoded_data(decoded: Dict[str, Any]) -> bool:
    """Validate decoded CAN frame data.
    
    Args:
        decoded: Decoded data dictionary
    
    Returns:
        True if valid, False otherwise
    """
    # Check required fields
    if 'can_id' not in decoded or 'message_type' not in decoded:
        return False
    
    # Validate message type
    valid_types = ['PT100', 'BME280', 'SCD30', 'VL53', 'Heartbeat', 'Unknown']
    if decoded['message_type'] not in valid_types:
        return False
    
    # Validate node_id if present
    if 'node_id' in decoded and decoded['node_id'] is not None:
        if decoded['node_id'] not in [1, 2, 3]:
            logger.warning(f"Invalid node_id: {decoded['node_id']}")
    
    return True


def extract_sensor_values(decoded: Dict[str, Any], location: str, cluster: str) -> List[Tuple[str, float, str]]:
    """Extract sensor values from decoded data.
    
    Args:
        decoded: Decoded CAN frame data
        location: Location name (e.g., "Flower Room")
        cluster: Cluster name (e.g., "front", "back", "main")
    
    Returns:
        List of tuples: (sensor_name, value, unit)
    """
    sensors = []
    
    # Get sensor suffix based on location/cluster
    suffix = get_sensor_suffix(location, cluster)
    
    message_type = decoded.get('message_type', '')
    node_id = decoded.get('node_id')
    
    # Map node_id to location/cluster if not provided
    if not location or not cluster:
        location, cluster = get_location_from_node(node_id)
        suffix = get_sensor_suffix(location, cluster)
    
    if message_type == "PT100":
        # Dry bulb temperature
        if 'temp_dry_c' in decoded and decoded['temp_dry_c'] is not None:
            if location == "Lab":
                sensor_key = "lab_temp"
            elif suffix:
                sensor_key = f"dry_bulb_{suffix}"
            else:
                sensor_key = "dry_bulb"
            sensors.append((sensor_key, float(decoded['temp_dry_c']), "°C"))
        
        # Wet bulb temperature
        if 'temp_wet_c' in decoded and decoded['temp_wet_c'] is not None:
            sensor_key = f"wet_bulb_{suffix}" if suffix else "wet_bulb"
            sensors.append((sensor_key, float(decoded['temp_wet_c']), "°C"))
        
        # Calculate RH and VPD if both temperatures are available
        temp_dry = decoded.get('temp_dry_c')
        temp_wet = decoded.get('temp_wet_c')
        if temp_dry is not None and temp_wet is not None:
            # Get pressure for this location/cluster (default to sea level if not available)
            pressure = _pressure_state[(location, cluster)]
            
            # Calculate RH and VPD
            rh = calculate_rh(float(temp_dry), float(temp_wet), pressure)
            vpd = calculate_vpd(float(temp_dry), float(temp_wet), pressure)
            
            # Round to 3 decimal places
            rh = round(rh, 3)
            vpd = round(vpd, 3)
            
            # Add calculated values to sensors list
            rh_key = f"rh_{suffix}" if suffix else "rh"
            vpd_key = f"vpd_{suffix}" if suffix else "vpd"
            sensors.append((rh_key, rh, "%"))
            sensors.append((vpd_key, vpd, "kPa"))
            
            # Also add to decoded dict for database storage (normalized tables)
            decoded['rh_percent'] = rh
            decoded['vpd_kpa'] = vpd
            decoded['pressure_hpa'] = pressure  # Store pressure used for calculation
    
    elif message_type == "SCD30":
        # CO2
        if 'co2_ppm' in decoded and decoded['co2_ppm'] is not None:
            sensor_key = f"co2_{suffix}" if suffix else "co2"
            co2_value = float(decoded['co2_ppm'])
            
            # Validate CO2 reading (filters false 0 readings)
            # Use current time for timestamp since we're processing in real-time
            if validate_co2_reading(sensor_key, co2_value, datetime.now()):
                sensors.append((sensor_key, co2_value, "ppm"))
            # If validation fails, reading is filtered out (not added to sensors list)
        
        # Secondary temperature
        if 'temperature_c' in decoded and decoded['temperature_c'] is not None:
            if location == "Lab":
                sensor_key = "water_temp"
            elif suffix:
                sensor_key = f"secondary_temp_{suffix}"
            else:
                sensor_key = "secondary_temp"
            sensors.append((sensor_key, float(decoded['temperature_c']), "°C"))
        
        # Secondary RH
        if 'humidity_percent' in decoded and decoded['humidity_percent'] is not None:
            sensor_key = f"secondary_rh_{suffix}" if suffix else "secondary_rh"
            sensors.append((sensor_key, float(decoded['humidity_percent']), "%"))
    
    elif message_type == "BME280":
        # Pressure
        if 'pressure_hpa' in decoded and decoded['pressure_hpa'] is not None:
            pressure_value = float(decoded['pressure_hpa'])
            sensor_key = f"pressure_{suffix}" if suffix else "pressure"
            sensors.append((sensor_key, pressure_value, "hPa"))
            
            # Update pressure state for this location/cluster
            _pressure_state[(location, cluster)] = pressure_value
    
    elif message_type == "VL53" or message_type == "VL53L0X":
        # Water level (distance)
        if 'distance_mm' in decoded and decoded['distance_mm'] is not None:
            sensor_key = f"water_level_{suffix}" if suffix else "water_level"
            sensors.append((sensor_key, float(decoded['distance_mm']), "mm"))
    
    return sensors


def get_sensor_suffix(location: str, cluster: str) -> str:
    """Get sensor name suffix based on location and cluster."""
    if location == "Flower Room":
        return "f" if cluster == "front" else "b"
    elif location == "Veg Room":
        return "v"
    elif location == "Lab":
        return ""  # Lab sensors might not have suffix
    return ""


def get_location_from_node(node_id: Optional[int]) -> Tuple[str, str]:
    """Map node_id to location and cluster.
    
    Node IDs from v7 NodeMapping.cpp:
    - 1: Flower Room, back
    - 2: Flower Room, front
    - 3: Veg Room, main
    """
    mapping = {
        1: ("Flower Room", "back"),
        2: ("Flower Room", "front"),
        3: ("Veg Room", "main"),
        4: ("Lab", "main"),
        5: ("Outside", "main"),
    }
    return mapping.get(node_id, ("Flower Room", "back"))


def calculate_rh(temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
    """Calculate relative humidity from dry and wet bulb temperatures.
    
    Uses psychrometric formula matching backend implementation.
    
    Args:
        temp_dry: Dry bulb temperature (°C)
        temp_wet: Wet bulb temperature (°C)
        pressure: Atmospheric pressure (hPa), default 1013.25 (sea level)
    
    Returns:
        Relative humidity (%)
    """
    # Saturation vapor pressure at dry bulb temperature
    es_dry = 6.112 * math.exp((17.67 * temp_dry) / (temp_dry + 243.5))
    
    # Saturation vapor pressure at wet bulb temperature
    es_wet = 6.112 * math.exp((17.67 * temp_wet) / (temp_wet + 243.5))
    
    # Actual vapor pressure using psychrometric equation
    e = es_wet - 0.000662 * pressure * (temp_dry - temp_wet)
    
    # Relative humidity
    rh = (e / es_dry) * 100.0
    
    # Clamp to valid range
    return max(0.0, min(100.0, rh))


def calculate_vpd(temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
    """Calculate VPD (Vapor Pressure Deficit) from dry and wet bulb temperatures.
    
    Args:
        temp_dry: Dry bulb temperature (°C)
        temp_wet: Wet bulb temperature (°C)
        pressure: Atmospheric pressure (hPa), default 1013.25 (sea level)
    
    Returns:
        VPD (kPa)
    """
    # Saturation vapor pressure at dry bulb temperature
    es = 6.112 * math.exp((17.67 * temp_dry) / (temp_dry + 243.5))
    
    # Actual vapor pressure
    ea = 6.112 * math.exp((17.67 * temp_wet) / (temp_wet + 243.5)) - 0.000662 * pressure * (temp_dry - temp_wet)
    
    # VPD (convert from hPa to kPa)
    vpd = (es - ea) / 10.0
    
    return max(0.0, vpd)

