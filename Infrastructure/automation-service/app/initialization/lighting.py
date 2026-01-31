"""Lighting initialization functions for automation service.

This module provides functions for initializing lighting hardware,
setting safety levels, and restoring light intensities on startup.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def set_safety_levels(config, dfr0971_manager) -> None:
    """Set safety levels on DFR0971 dimming boards.

    Safety levels limit maximum output and are stored in EEPROM.
    Reads safety_level from device config (0 = no limit/100%).

    Args:
        config: ConfigLoader instance with device configuration
        dfr0971_manager: DFR0971Manager instance for hardware control
    """
    if not dfr0971_manager:
        logger.warning("DFR0971 manager not available, skipping safety level setup")
        return

    devices = config.get_devices()
    safety_levels = {}

    for _location, clusters in devices.items():
        for _cluster, cluster_devices in clusters.items():
            for device_name, device_info in cluster_devices.items():
                # Only set safety for dimmable DFR0971 lights
                if not device_info.get("dimming_enabled", False):
                    continue
                if device_info.get("dimming_type") != "dfr0971":
                    continue

                board_id = device_info.get("dimming_board_id")
                channel = device_info.get("dimming_channel")

                if board_id is None or channel is None:
                    continue

                # Read safety_level from config (0 means no limit = 100%)
                config_safety = device_info.get("safety_level", 0)
                if config_safety == 0:
                    safety_level = 100.0
                else:
                    safety_level = float(config_safety)

                display_name = device_info.get("display_name", device_name)
                logger.info(f"Safety level for {display_name}: {safety_level}%")

                key = (board_id, channel)
                # Use lowest safety level if multiple devices share same channel (more restrictive)
                if key in safety_levels and safety_levels[key] < safety_level:
                    continue
                safety_levels[key] = safety_level

    # Apply safety levels
    for (board_id, channel), safety_level in safety_levels.items():
        success = dfr0971_manager.set_safety_level(board_id, channel, safety_level)
        if success:
            logger.info(
                f"Set safety level to {safety_level:.1f}% for board {board_id} channel {channel}"
            )
        else:
            logger.warning(f"Failed to set safety level for board {board_id} channel {channel}")


async def restore_light_intensities(database, config, dfr0971_manager) -> None:
    """Restore light intensities on startup.

    Since DFR0971 cannot read EEPROM values, we restore from:
    1. Redis (fast, but may be lost on restart)
    2. Database (slower, but persistent - source of truth)

    The database is preferred since it persists across restarts and
    contains logged intensity values.

    Args:
        database: DatabaseManager instance
        config: ConfigLoader instance with device configuration
        dfr0971_manager: DFR0971Manager instance for hardware control
    """
    if not dfr0971_manager:
        logger.warning("DFR0971 manager not available, skipping light intensity restoration")
        return

    logger.info("Restoring light intensities from database/Redis...")
    redis_client = (
        database._automation_redis
        if database._automation_redis and database._automation_redis.redis_enabled
        else None
    )
    devices = config.get_devices()
    restored_count = 0

    for location, clusters in devices.items():
        for cluster, cluster_devices in clusters.items():
            for device_name, device_info in cluster_devices.items():
                # Check if this is a dimmable light
                if not device_info.get("dimming_enabled", False):
                    continue

                if device_info.get("dimming_type") != "dfr0971":
                    continue

                board_id = device_info.get("dimming_board_id")
                channel = device_info.get("dimming_channel")

                if board_id is None or channel is None:
                    continue

                intensity = None
                source = None

                # Try Redis first (fast, but may not persist)
                if redis_client:
                    light_data = redis_client.read_light_intensity(location, cluster, device_name)
                    if light_data:
                        intensity = light_data.get("intensity")
                        source = "Redis"

                # Fall back to database (slower, but persistent - source of truth)
                if intensity is None and database:
                    intensity = await database.get_latest_light_intensity(
                        location, cluster, device_name
                    )
                    if intensity is not None:
                        source = "Database"
                        # Also update Redis with value we found in database
                        if redis_client:
                            voltage = (intensity / 100.0) * 10.0
                            redis_client.write_light_intensity(
                                location,
                                cluster,
                                device_name,
                                intensity,
                                voltage,
                                board_id,
                                channel,
                            )

                # Skip restoring 0 to hardware: treat 0 as "no value" so a bad 0 in DB/Redis
                # does not force lights off; the control loop will set intensity from the schedule.
                if intensity is not None and intensity > 0:
                    # Restore intensity to hardware (but don't save to EEPROM - safety levels stay in EEPROM)
                    success = dfr0971_manager.set_intensity(
                        board_id, channel, intensity, store_to_eeprom=False
                    )
                    if success:
                        restored_count += 1
                        logger.info(
                            f"Restored {location}/{cluster}/{device_name} to {intensity:.1f}% "
                            f"from {source} (board {board_id}, channel {channel}, not saved to EEPROM)"
                        )
                    else:
                        logger.warning(
                            f"Failed to restore intensity for {location}/{cluster}/{device_name}"
                        )
                elif intensity is not None and intensity == 0:
                    logger.debug(
                        f"Skipping restore of 0% for {location}/{cluster}/{device_name} "
                        "(treat 0 as no value; control loop will set from schedule)"
                    )

    if restored_count > 0:
        logger.info(f"Restored {restored_count} light intensity values from database/Redis")
    else:
        logger.warning("No light intensities restored - all at 0% or first run")
