"""Helper functions to process Redis Stream entries into sensor data points.

CAN stream entries ship an assignment snapshot (registry id + topology
location) from ingestion; routing follows that snapshot, and the canonical
suffix derivation is owned by ``shared.cluster_topology`` (the same owner
the CAN processor consults).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.models import DataPoint
from shared import (
    calculate_rh,
    calculate_vpd,
    get_pressure_state,
    update_pressure_state,
)
from shared.cluster_topology import sensor_name_like_pattern
from shared.stream_assignment import all_entries_qualified, entry_qualified

__all__ = [
    "all_entries_qualified",
    "entry_qualified",
    "extract_sensor_values_from_decoded",
    "process_stream_entries_to_sensor_data",
]


def get_sensor_suffix(location: str, cluster: str) -> str:
    """Return the canonical measurement-name suffix via the topology owner."""
    pattern = sensor_name_like_pattern(location, cluster)
    return pattern[1:] if pattern else ""


def extract_sensor_values_from_decoded(
    decoded: dict[str, Any], location: str, cluster: str
) -> list[tuple[str, float, str]]:
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
    message_type = decoded.get("message_type", "")

    if message_type == "PT100":
        if "temp_dry_c" in decoded and decoded["temp_dry_c"] is not None:
            if location == "Lab":
                sensor_key = "lab_temp"
            elif suffix:
                sensor_key = f"dry_bulb{suffix}"
            else:
                sensor_key = "dry_bulb"
            sensors.append((sensor_key, float(decoded["temp_dry_c"]), "°C"))

        if "temp_wet_c" in decoded and decoded["temp_wet_c"] is not None:
            sensor_key = f"wet_bulb{suffix}" if suffix else "wet_bulb"
            sensors.append((sensor_key, float(decoded["temp_wet_c"]), "°C"))

        # Calculate RH and VPD
        temp_dry = decoded.get("temp_dry_c")
        temp_wet = decoded.get("temp_wet_c")
        if temp_dry is not None and temp_wet is not None:
            pressure = get_pressure_state(location, cluster)
            rh = round(calculate_rh(float(temp_dry), float(temp_wet), pressure), 3)
            vpd = round(calculate_vpd(float(temp_dry), float(temp_wet), pressure), 3)
            rh_key = f"rh{suffix}" if suffix else "rh"
            vpd_key = f"vpd{suffix}" if suffix else "vpd"
            sensors.append((rh_key, rh, "%"))
            sensors.append((vpd_key, vpd, "kPa"))

    elif message_type == "SCD30":
        if "co2_ppm" in decoded and decoded["co2_ppm"] is not None:
            sensor_key = f"co2{suffix}" if suffix else "co2"
            sensors.append((sensor_key, float(decoded["co2_ppm"]), "ppm"))

        if "temperature_c" in decoded and decoded["temperature_c"] is not None:
            if location == "Lab":
                sensor_key = "water_temp"
            elif suffix:
                sensor_key = f"secondary_temp{suffix}"
            else:
                sensor_key = "secondary_temp"
            sensors.append((sensor_key, float(decoded["temperature_c"]), "°C"))

        if "humidity_percent" in decoded and decoded["humidity_percent"] is not None:
            sensor_key = f"secondary_rh{suffix}" if suffix else "secondary_rh"
            sensors.append((sensor_key, float(decoded["humidity_percent"]), "%"))

    elif message_type == "BME280":
        if "pressure_hpa" in decoded and decoded["pressure_hpa"] is not None:
            pressure_value = float(decoded["pressure_hpa"])
            sensor_key = f"pressure{suffix}" if suffix else "pressure"
            sensors.append((sensor_key, pressure_value, "hPa"))
            update_pressure_state(location, cluster, pressure_value)

    elif message_type == "VL53" or message_type == "VL53L0X":
        if "distance_mm" in decoded and decoded["distance_mm"] is not None:
            sensor_key = f"water_level{suffix}" if suffix else "water_level"
            sensors.append((sensor_key, float(decoded["distance_mm"]), "mm"))

    return sensors


def process_stream_entries_to_sensor_data(
    stream_entries: list[dict[str, Any]], location: str, cluster: str
) -> dict[str, list[DataPoint]]:
    """Process Redis Stream entries into sensor data points.

    Every CAN entry carries an assignment snapshot from ingestion; entries
    are routed by that snapshot (never by a node-id map), and only entries
    whose qualified location matches the requested filter feed the result.
    """
    sensor_data: dict[str, list[DataPoint]] = {}

    for entry in stream_entries:
        # Only process CAN sensor entries
        if entry.get("type") != "can":
            continue

        decoded = entry.get("decoded")
        if not decoded:
            continue

        entry_location = entry.get("location")
        entry_cluster = entry.get("cluster")

        # Filter by requested location/cluster
        if entry_location != location or entry_cluster != cluster:
            continue

        # Extract sensor values
        sensors = extract_sensor_values_from_decoded(decoded, location, cluster)

        # Get timestamp
        ts_ms = entry.get("timestamp_ms")
        timestamp = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC) if ts_ms else datetime.now()

        # Add to sensor data
        for sensor_name, value, unit in sensors:
            if sensor_name not in sensor_data:
                sensor_data[sensor_name] = []

            sensor_data[sensor_name].append(DataPoint(timestamp=timestamp, value=value, unit=unit))

    # Sort each sensor's data points by timestamp
    for sensor_name in sensor_data:
        sensor_data[sensor_name].sort(key=lambda point: point.timestamp)

    return sensor_data
