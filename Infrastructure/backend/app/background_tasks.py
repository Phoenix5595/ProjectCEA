"""Background tasks for periodic data updates."""

from __future__ import annotations

import asyncio
from datetime import datetime

from app.redis_client import get_all_sensor_timestamps, get_all_sensor_values
from app.websocket import websocket_manager
from shared.infra_logging import get_logger

logger = get_logger(__name__)

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY = 1.0  # seconds
ERROR_DELAY = 5.0  # seconds after error


async def broadcast_latest_sensor_data():
    """Periodically fetch latest sensor data from Redis and broadcast via WebSocket.

    This task reads live sensor values from Redis state keys and broadcasts them
    via WebSocket for real-time updates.
    """
    consecutive_errors = 0
    max_consecutive_errors = 10

    # Map sensor names to location/cluster
    # This maps sensor suffixes to location/cluster
    sensor_location_map = {
        # Flower Room, back
        "dry_bulb_b": ("Flower Room", "back"),
        "wet_bulb_b": ("Flower Room", "back"),
        "co2_b": ("Flower Room", "back"),
        "rh_b": ("Flower Room", "back"),
        "vpd_b": ("Flower Room", "back"),
        "pressure_b": ("Flower Room", "back"),
        "secondary_temp_b": ("Flower Room", "back"),
        "secondary_rh_b": ("Flower Room", "back"),
        # Flower Room, front
        "dry_bulb_f": ("Flower Room", "front"),
        "wet_bulb_f": ("Flower Room", "front"),
        "co2_f": ("Flower Room", "front"),
        "rh_f": ("Flower Room", "front"),
        "vpd_f": ("Flower Room", "front"),
        "pressure_f": ("Flower Room", "front"),
        "secondary_temp_f": ("Flower Room", "front"),
        "secondary_rh_f": ("Flower Room", "front"),
        # Veg Room, main
        "dry_bulb_v": ("Veg Room", "main"),
        "wet_bulb_v": ("Veg Room", "main"),
        "co2_v": ("Veg Room", "main"),
        "rh_v": ("Veg Room", "main"),
        "vpd_v": ("Veg Room", "main"),
        "pressure_v": ("Veg Room", "main"),
        "secondary_temp_v": ("Veg Room", "main"),
        "secondary_rh_v": ("Veg Room", "main"),
        # Lab (1-Wire: lab_temp, water_temperature)
        "lab_temp": ("Lab", "main"),
        "water_temp": ("Lab", "main"),
        "water_temperature": ("Lab", "main"),
    }

    # Unit mapping
    unit_map = {
        "dry_bulb": "°C",
        "wet_bulb": "°C",
        "secondary_temp": "°C",
        "lab_temp": "°C",
        "water_temp": "°C",
        "water_temperature": "°C",
        "co2": "ppm",
        "rh": "%",
        "secondary_rh": "%",
        "vpd": "kPa",
        "pressure": "hPa",
        "water_level": "mm",
    }

    logger.info("Background broadcast task starting (reading from Redis)...")

    while True:
        try:
            # Get all sensor values from Redis
            sensor_values = await get_all_sensor_values()

            if not sensor_values:
                # No data in Redis, might be starting up
                consecutive_errors += 1
                if consecutive_errors < 5:
                    logger.debug("No sensor data in Redis yet")
                await asyncio.sleep(1.0)
                continue

            consecutive_errors = 0  # Reset on success
            timestamps_ms = await get_all_sensor_timestamps(list(sensor_values.keys()))

            # Broadcast each sensor value
            for sensor_name, value in sensor_values.items():
                try:
                    # Get location/cluster from sensor name
                    location, cluster = sensor_location_map.get(sensor_name, (None, None))
                    if not location or not cluster:
                        continue  # Skip unknown sensors

                    # Get timestamp
                    ts_ms = timestamps_ms.get(sensor_name)
                    if ts_ms:
                        timestamp = datetime.fromtimestamp(ts_ms / 1000.0)
                    else:
                        timestamp = datetime.now()

                    # Determine unit
                    unit = ""
                    for key, u in unit_map.items():
                        if key in sensor_name:
                            unit = u
                            break

                    # Broadcast via WebSocket
                    await websocket_manager.broadcast_sensor_update(
                        location=location,
                        cluster=cluster,
                        sensor_type=sensor_name,
                        timestamp=timestamp,
                        value=value,
                        unit=unit,
                    )

                except Exception as e:
                    logger.warning(f"Error broadcasting {sensor_name}: {e}")
                    # Continue with other sensors even if one fails

            # Wait 0.5 seconds before next broadcast for faster updates
            await asyncio.sleep(0.5)

        except asyncio.CancelledError:
            logger.info("Background broadcast task cancelled")
            raise
        except Exception as e:
            consecutive_errors += 1
            logger.error(
                f"Unexpected error in background broadcast task (error #{consecutive_errors}): {e}",
                exc_info=True,
            )

            # If we've had too many errors, wait longer before retrying
            wait_time = ERROR_DELAY * min(consecutive_errors, 5)  # Cap at 5x delay
            await asyncio.sleep(wait_time)

            if consecutive_errors >= max_consecutive_errors:
                logger.warning("Too many consecutive errors, waiting longer before retry")
                consecutive_errors = 0
