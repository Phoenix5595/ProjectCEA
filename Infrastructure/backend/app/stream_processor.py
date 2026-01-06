"""Helper functions to process Redis Stream entries into sensor data points."""
from shared.logging import get_logger
import math
import sys
import os
from typing import Dict, Any, List, Tuple, Optional
from datetime import datetime
from collections import defaultdict
from app.models import DataPoint

# Import shared sensor processing functions
try:
    shared_path = os.path.join(os.path.dirname(__file__), '..', '..', '..')
    sys.path.insert(0, shared_path)
    from shared import (
        get_sensor_suffix as shared_get_sensor_suffix,
        calculate_rh as shared_calculate_rh,
        calculate_vpd as shared_calculate_vpd,
        update_pressure_state,
        get_pressure_state
    )
except ImportError:
    # Fallback implementations
    def shared_get_sensor_suffix(location: str, cluster: str) -> str:
        suffix_map = {
            ('Veg Room', 'clusterA'): '_f',
            ('Veg Room', 'clusterB'): '_b',
            ('Flower Room', 'clusterA'): '_f',
            ('Flower Room', 'clusterB'): '_b',
            ('Mother Room', 'clusterA'): '_f',
            ('Mother Room', 'clusterB'): '_b',
        }
        return suffix_map.get((location, cluster), '_f')

    def shared_calculate_rh(temp_dry, temp_wet, pressure=1013.25):
        if temp_dry <= temp_wet:
            return 100.0
        a, b = 17.27, 237.3
        es_dry = 6.1078 * math.exp((a * temp_dry) / (b + temp_dry))
        es_wet = 6.1078 * math.exp((a * temp_wet) / (b + temp_wet))
        e = es_wet - ((pressure / 1000) * (temp_dry - temp_wet) * 0.00066 * (1 + 0.00115 * temp_wet))
        return max(0.0, min(100.0, (e / es_dry) * 100.0))

    def shared_calculate_vpd(temp_dry, temp_wet, pressure=1013.25):
        if temp_dry <= temp_wet:
            return 0.0
        a, b = 17.27, 237.3
        es_dry = 0.6108 * math.exp((a * temp_dry) / (b + temp_dry))
        es_wet = 0.6108 * math.exp((a * temp_wet) / (b + temp_wet))
        e = es_wet - ((pressure / 1000) * (temp_dry - temp_wet) * 0.00066 * (1 + 0.00115 * temp_wet))
        return max(0.0, es_dry - e)

    def update_pressure_state(location, cluster, pressure):
        pass

    def get_pressure_state(location, cluster):
        return 1013.25

# State tracker for pressure values per location/cluster (local to stream processor)
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
    """Get the sensor suffix for a location/cluster combination.

    Note: Stream processor uses different cluster naming than CAN processor.
    This function adapts the shared suffix mapping to stream processor conventions.
    """
    # Adapt cluster names for stream processor
    adapted_cluster = cluster
    if cluster == "back":
        adapted_cluster = "clusterB"
    elif cluster == "front":
        adapted_cluster = "clusterA"
    elif cluster == "main":
        adapted_cluster = "clusterA"  # Default to clusterA for main

    return shared_get_sensor_suffix(location, adapted_cluster)


def calculate_rh(temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
    """Calculate relative humidity using shared library."""
    return shared_calculate_rh(temp_dry, temp_wet, pressure)


def calculate_vpd(temp_dry: float, temp_wet: float, pressure: float = 1013.25) -> float:
    """Calculate vapor pressure deficit using shared library."""
    return shared_calculate_vpd(temp_dry, temp_wet, pressure)


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

