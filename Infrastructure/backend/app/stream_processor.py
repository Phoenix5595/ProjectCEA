"""Helper functions to process Redis Stream entries into sensor data points."""
import math
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from collections import defaultdict
from app.models import DataPoint

# State tracker for pressure values per location/cluster
_pressure_state: Dict[Tuple[str, str], float] = defaultdict(lambda: 1013.25)


def get_location_from_node(node_id: Optional[int]) -> Tuple[str, str]:
    """Map node_id to location and cluster."""
    mapping = {
        1: ("Flower Room", "back"),
        2: ("Flower Room", "front"),
        3: ("Veg Room", "main"),
        4: ("Lab", "main"),
        5: ("Outside", "main"),
    }
    return mapping.get(node_id, ("Flower Room", "back"))


def get_sensor_suffix(location: str, cluster: str) -> str:
    """Get sensor name suffix based on location and cluster."""
    if location == "Flower Room":
        return "f" if cluster == "front" else "b"
    elif location == "Veg Room":
        return "v"
    elif location == "Lab":
        return ""
    return ""


def calculate_rh(temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
    """Calculate relative humidity from dry and wet bulb temperatures."""
    es_dry = 6.112 * math.exp((17.67 * temp_dry) / (temp_dry + 243.5))
    es_wet = 6.112 * math.exp((17.67 * temp_wet) / (temp_wet + 243.5))
    e = es_wet - 0.000662 * pressure * (temp_dry - temp_wet)
    rh = (e / es_dry) * 100.0
    return max(0.0, min(100.0, rh))


def calculate_vpd(temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
    """Calculate VPD from dry and wet bulb temperatures."""
    es = 6.112 * math.exp((17.67 * temp_dry) / (temp_dry + 243.5))
    ea = 6.112 * math.exp((17.67 * temp_wet) / (temp_wet + 243.5)) - 0.000662 * pressure * (temp_dry - temp_wet)
    vpd = (es - ea) / 10.0
    return max(0.0, vpd)


def extract_sensor_values_from_decoded(decoded: Dict[str, Any], location: str, cluster: str) -> List[Tuple[str, float, str]]:
    """Extract sensor values from decoded CAN frame data.
    
    Args:
        decoded: Decoded CAN frame data
        location: Location name
        cluster: Cluster name
    
    Returns:
        List of (sensor_name, value, unit) tuples
    """
    sensors = []
    suffix = get_sensor_suffix(location, cluster)
    message_type = decoded.get('message_type', '')
    
    if message_type == "PT100":
        if 'temp_dry_c' in decoded and decoded['temp_dry_c'] is not None:
            if location == "Lab":
                sensor_key = "lab_temp"
            elif suffix:
                sensor_key = f"dry_bulb_{suffix}"
            else:
                sensor_key = "dry_bulb"
            sensors.append((sensor_key, float(decoded['temp_dry_c']), "°C"))
        
        if 'temp_wet_c' in decoded and decoded['temp_wet_c'] is not None:
            sensor_key = f"wet_bulb_{suffix}" if suffix else "wet_bulb"
            sensors.append((sensor_key, float(decoded['temp_wet_c']), "°C"))
        
        # Calculate RH and VPD
        temp_dry = decoded.get('temp_dry_c')
        temp_wet = decoded.get('temp_wet_c')
        if temp_dry is not None and temp_wet is not None:
            pressure = _pressure_state[(location, cluster)]
            rh = round(calculate_rh(float(temp_dry), float(temp_wet), pressure), 3)
            vpd = round(calculate_vpd(float(temp_dry), float(temp_wet), pressure), 3)
            rh_key = f"rh_{suffix}" if suffix else "rh"
            vpd_key = f"vpd_{suffix}" if suffix else "vpd"
            sensors.append((rh_key, rh, "%"))
            sensors.append((vpd_key, vpd, "kPa"))
    
    elif message_type == "SCD30":
        if 'co2_ppm' in decoded and decoded['co2_ppm'] is not None:
            sensor_key = f"co2_{suffix}" if suffix else "co2"
            sensors.append((sensor_key, float(decoded['co2_ppm']), "ppm"))
        
        if 'temperature_c' in decoded and decoded['temperature_c'] is not None:
            if location == "Lab":
                sensor_key = "water_temp"
            elif suffix:
                sensor_key = f"secondary_temp_{suffix}"
            else:
                sensor_key = "secondary_temp"
            sensors.append((sensor_key, float(decoded['temperature_c']), "°C"))
        
        if 'humidity_percent' in decoded and decoded['humidity_percent'] is not None:
            sensor_key = f"secondary_rh_{suffix}" if suffix else "secondary_rh"
            sensors.append((sensor_key, float(decoded['humidity_percent']), "%"))
    
    elif message_type == "BME280":
        if 'pressure_hpa' in decoded and decoded['pressure_hpa'] is not None:
            pressure_value = float(decoded['pressure_hpa'])
            sensor_key = f"pressure_{suffix}" if suffix else "pressure"
            sensors.append((sensor_key, pressure_value, "hPa"))
            _pressure_state[(location, cluster)] = pressure_value
    
    elif message_type == "VL53" or message_type == "VL53L0X":
        if 'distance_mm' in decoded and decoded['distance_mm'] is not None:
            sensor_key = f"water_level_{suffix}" if suffix else "water_level"
            sensors.append((sensor_key, float(decoded['distance_mm']), "mm"))
    
    return sensors


def process_stream_entries_to_sensor_data(
    stream_entries: List[Dict[str, Any]],
    location: str,
    cluster: str
) -> Dict[str, List[DataPoint]]:
    """Process Redis Stream entries into sensor data points.
    
    Args:
        stream_entries: List of decoded stream entries
        location: Location name
        cluster: Cluster name
    
    Returns:
        Dictionary mapping sensor_name -> List[DataPoint]
    """
    sensor_data: Dict[str, List[DataPoint]] = {}
    
    for entry in stream_entries:
        # Only process CAN sensor entries
        if entry.get('type') != 'can':
            continue
        
        decoded = entry.get('decoded')
        if not decoded:
            continue
        
        # Get node_id and map to location/cluster
        node_id = decoded.get('node_id')
        entry_location, entry_cluster = get_location_from_node(node_id)
        
        # Filter by requested location/cluster
        if entry_location != location or entry_cluster != cluster:
            continue
        
        # Extract sensor values
        sensors = extract_sensor_values_from_decoded(decoded, location, cluster)
        
        # Get timestamp
        ts_ms = entry.get('timestamp_ms')
        if ts_ms:
            timestamp = datetime.fromtimestamp(ts_ms / 1000.0)
        else:
            timestamp = datetime.now()
        
        # Add to sensor data
        for sensor_name, value, unit in sensors:
            if sensor_name not in sensor_data:
                sensor_data[sensor_name] = []
            
            sensor_data[sensor_name].append(DataPoint(
                timestamp=timestamp,
                value=value,
                unit=unit
            ))
    
    # Sort each sensor's data points by timestamp
    for sensor_name in sensor_data:
        sensor_data[sensor_name].sort(key=lambda x: x.timestamp)
    
    return sensor_data

