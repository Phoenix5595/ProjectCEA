"""Process and validate decoded CAN frame data."""

from __future__ import annotations

from datetime import datetime
import os
import sys
from typing import Any

from shared.logging import get_logger

# Add the shared library to the path
current_dir = os.path.dirname(os.path.abspath(__file__))
shared_path = os.path.join(current_dir, "..", "..", "..")
sys.path.insert(0, shared_path)

# Import shared sensor processing functions
try:
    from shared import (
        calculate_rh,
        calculate_vpd,
        get_location_from_node,
        get_pressure_state,
        get_sensor_suffix,
        update_pressure_state,
        validate_co2_reading,
    )

    logger.info("Successfully imported shared sensor processing library")
except ImportError as e:
    # Use print since logger might not be available if shared import failed
    print(f"Warning: Failed to import shared sensor processing library: {e}", file=sys.stderr)
    try:
        logger.warning(f"Failed to import shared sensor processing library: {e}")
    except Exception:
        pass  # Logger not available

    # Define minimal fallback implementations
    def get_sensor_suffix(location: str, cluster: str) -> str:
        suffix_map = {
            ("Veg Room", "clusterA"): "_f",
            ("Veg Room", "clusterB"): "_b",
            ("Flower Room", "clusterA"): "_f",
            ("Flower Room", "clusterB"): "_b",
            ("Mother Room", "clusterA"): "_f",
            ("Mother Room", "clusterB"): "_b",
        }
        return suffix_map.get((location, cluster), "_f")

    def get_location_from_node(node_id):
        if node_id is None:
            return ("Unknown", "Unknown")
        node_map = {
            1: ("Veg Room", "clusterB"),  # Node 1 has _b sensors in DB
            2: ("Veg Room", "clusterA"),  # Node 2 has _f sensors in DB
            3: ("Flower Room", "clusterA"),
            4: ("Flower Room", "clusterB"),
            5: ("Mother Room", "clusterA"),
            6: ("Mother Room", "clusterB"),
        }
        return node_map.get(node_id, ("Unknown", f"node_{node_id}"))

    def calculate_rh(temp_dry, temp_wet, pressure=1013.25):
        if temp_dry <= temp_wet:
            return 100.0
        import math

        a, b = 17.27, 237.3
        es_dry = 6.1078 * math.exp((a * temp_dry) / (b + temp_dry))
        es_wet = 6.1078 * math.exp((a * temp_wet) / (b + temp_wet))
        e = es_wet - (
            (pressure / 1000) * (temp_dry - temp_wet) * 0.00066 * (1 + 0.00115 * temp_wet)
        )
        return max(0.0, min(100.0, (e / es_dry) * 100.0))

    def calculate_vpd(temp_dry, temp_wet, pressure=1013.25):
        if temp_dry <= temp_wet:
            return 0.0
        import math

        a, b = 17.27, 237.3
        es_dry = 0.6108 * math.exp((a * temp_dry) / (b + temp_dry))
        es_wet = 0.6108 * math.exp((a * temp_wet) / (b + temp_wet))
        e = es_wet - (
            (pressure / 1000) * (temp_dry - temp_wet) * 0.00066 * (1 + 0.00115 * temp_wet)
        )
        return max(0.0, es_dry - e)

    def validate_co2_reading(sensor_name, value, timestamp):
        return value >= 0

    def update_pressure_state(location, cluster, pressure):
        pass

    def get_pressure_state(location, cluster):
        return 1013.25


logger = get_logger(__name__)


def validate_decoded_data(decoded: dict[str, Any]) -> bool:
    """Validate decoded CAN frame data.

    Args:
        decoded: Decoded data dictionary

    Returns:
        True if valid, False otherwise
    """
    # Check required fields
    if "can_id" not in decoded or "message_type" not in decoded:
        return False

    # Validate message type
    valid_types = ["PT100", "BME280", "SCD30", "VL53", "Heartbeat", "Unknown"]
    if decoded["message_type"] not in valid_types:
        return False

    # Validate node_id if present
    if "node_id" in decoded and decoded["node_id"] is not None:
        if decoded["node_id"] not in [1, 2, 3]:
            logger.warning(f"Invalid node_id: {decoded['node_id']}")

    return True


def extract_sensor_values(
    decoded: dict[str, Any], location: str, cluster: str
) -> list[tuple[str, float, str]]:
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

    message_type = decoded.get("message_type", "")
    node_id = decoded.get("node_id")

    # Map node_id to location/cluster if not provided
    if not location or not cluster:
        location, cluster = get_location_from_node(node_id)
        suffix = get_sensor_suffix(location, cluster)

    if message_type == "PT100":
        # Dry bulb temperature
        if "temp_dry_c" in decoded and decoded["temp_dry_c"] is not None:
            if location == "Lab":
                sensor_key = "lab_temp"
            elif suffix:
                sensor_key = f"dry_bulb{suffix}"
            else:
                sensor_key = "dry_bulb"
            sensors.append((sensor_key, float(decoded["temp_dry_c"]), "°C"))

        # Wet bulb temperature
        if "temp_wet_c" in decoded and decoded["temp_wet_c"] is not None:
            sensor_key = f"wet_bulb{suffix}" if suffix else "wet_bulb"
            sensors.append((sensor_key, float(decoded["temp_wet_c"]), "°C"))

        # Calculate RH and VPD if both temperatures are available
        temp_dry = decoded.get("temp_dry_c")
        temp_wet = decoded.get("temp_wet_c")
        if temp_dry is not None and temp_wet is not None:
            # Get pressure for this location/cluster (default to sea level if not available)
            pressure = get_pressure_state(location, cluster)

            # Calculate RH and VPD
            rh = calculate_rh(float(temp_dry), float(temp_wet), pressure)
            vpd = calculate_vpd(float(temp_dry), float(temp_wet), pressure)

            # Round to 3 decimal places
            rh = round(rh, 3)
            vpd = round(vpd, 3)

            # Add calculated values to sensors list
            rh_key = f"rh{suffix}" if suffix else "rh"
            vpd_key = f"vpd{suffix}" if suffix else "vpd"
            sensors.append((rh_key, rh, "%"))
            sensors.append((vpd_key, vpd, "kPa"))

            # Also add to decoded dict for database storage (normalized tables)
            decoded["rh_percent"] = rh
            decoded["vpd_kpa"] = vpd
            decoded["pressure_hpa"] = pressure  # Store pressure used for calculation

    elif message_type == "SCD30":
        # CO2
        if "co2_ppm" in decoded and decoded["co2_ppm"] is not None:
            sensor_key = f"co2{suffix}" if suffix else "co2"
            co2_value = float(decoded["co2_ppm"])

            # Validate CO2 reading (filters false 0 readings)
            # Use current time for timestamp since we're processing in real-time
            if validate_co2_reading(sensor_key, co2_value, datetime.now()):
                sensors.append((sensor_key, co2_value, "ppm"))
            # If validation fails, reading is filtered out (not added to sensors list)

        # Secondary temperature
        if "temperature_c" in decoded and decoded["temperature_c"] is not None:
            if location == "Lab":
                sensor_key = "water_temp"
            elif suffix:
                sensor_key = f"secondary_temp{suffix}"
            else:
                sensor_key = "secondary_temp"
            sensors.append((sensor_key, float(decoded["temperature_c"]), "°C"))

        # Secondary RH
        if "humidity_percent" in decoded and decoded["humidity_percent"] is not None:
            sensor_key = f"secondary_rh{suffix}" if suffix else "secondary_rh"
            sensors.append((sensor_key, float(decoded["humidity_percent"]), "%"))

    elif message_type == "BME280":
        # Pressure
        if "pressure_hpa" in decoded and decoded["pressure_hpa"] is not None:
            pressure_value = float(decoded["pressure_hpa"])
            sensor_key = f"pressure{suffix}" if suffix else "pressure"
            sensors.append((sensor_key, pressure_value, "hPa"))

            # Update pressure state for this location/cluster
            update_pressure_state(location, cluster, pressure_value)

    elif message_type == "VL53" or message_type == "VL53L0X":
        # Water level (distance)
        if "distance_mm" in decoded and decoded["distance_mm"] is not None:
            sensor_key = f"water_level{suffix}" if suffix else "water_level"
            sensors.append((sensor_key, float(decoded["distance_mm"]), "mm"))

    return sensors
