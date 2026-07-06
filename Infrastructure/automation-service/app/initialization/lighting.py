"""Lighting initialization functions for automation service.

This module provides functions for initializing lighting hardware,
setting safety levels, and restoring light intensities on startup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.database import DatabaseManager
    from app.hardware.dfr0971 import DFR0971Manager
    from app.repositories.devices import DeviceRepository

logger = logging.getLogger(__name__)


async def set_safety_levels(
    device_repo: DeviceRepository, dfr0971_manager: DFR0971Manager | None
) -> None:
    """Set safety levels on DFR0971 dimming boards.

    Safety levels limit maximum output and are stored in EEPROM.
    Reads safety_level from device registry (0 = no limit/100%).

    Args:
        device_repo: DeviceRepository instance for DB-backed device queries
        dfr0971_manager: DFR0971Manager instance for hardware control
    """
    if not dfr0971_manager:
        logger.warning("DFR0971 manager not available, skipping safety level setup")
        return

    hierarchy = await device_repo.get_all_as_hierarchy()
    safety_levels: dict[tuple[int, int], float] = {}

    for room in hierarchy:
        lights = await device_repo.get_lights_by_room(room)
        for light in lights:
            if not light.dimming_enabled:
                continue
            if light.dimming_type != "dfr0971":
                continue

            board_id = light.board_id
            channel = light.dimming_channel

            if board_id is None or channel is None:
                continue

            # Read safety_level from registry (0 means no limit = 100%)
            if light.safety_level == 0:
                safety_level = 100.0
            else:
                safety_level = float(light.safety_level)

            logger.info("Safety level for %s: %.1f%%", light.display_name, safety_level)

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
                "Set safety level to %.1f%% for board %s channel %s",
                safety_level,
                board_id,
                channel,
            )
        else:
            logger.warning("Failed to set safety level for board %s channel %s", board_id, channel)


async def restore_light_intensities(
    database: DatabaseManager, device_repo: DeviceRepository, dfr0971_manager: DFR0971Manager | None
) -> None:
    """Restore light intensities on startup.

    Since DFR0971 cannot read EEPROM values, we restore from:
    1. Redis (fast, but may be lost on restart)
    2. Database (slower, but persistent - source of truth)

    The database is preferred since it persists across restarts and
    contains logged intensity values.

    Args:
        database: DatabaseManager instance
        device_repo: DeviceRepository instance for DB-backed device queries
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
    restored_count = 0

    hierarchy = await device_repo.get_all_as_hierarchy()
    for room in hierarchy:
        lights = await device_repo.get_lights_by_room(room)
        for light in lights:
            if not light.dimming_enabled:
                continue

            if light.dimming_type != "dfr0971":
                continue

            board_id = light.board_id
            channel = light.dimming_channel

            if board_id is None or channel is None:
                continue

            intensity = None
            source = None

            # Try Redis first (fast, but may not persist)
            if redis_client:
                light_data = redis_client.read_light_intensity(
                    light.location, light.cluster, light.device_name
                )
                if light_data:
                    intensity = light_data.get("intensity")
                    source = "Redis"

            # Fall back to database (slower, but persistent - source of truth)
            if intensity is None and database:
                intensity = await database.device_repo.get_latest_light_intensity(
                    light.location, light.cluster, light.device_name
                )
                if intensity is not None:
                    source = "Database"
                    # Also update Redis with value we found in database
                    if redis_client:
                        voltage = (intensity / 100.0) * 10.0
                        redis_client.write_light_intensity(
                            light.location,
                            light.cluster,
                            light.device_name,
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
                        "Restored %s/%s/%s to %.1f%% from %s (board %s, channel %s, not saved to EEPROM)",
                        light.location,
                        light.cluster,
                        light.device_name,
                        intensity,
                        source,
                        board_id,
                        channel,
                    )
                else:
                    logger.warning(
                        "Failed to restore intensity for %s/%s/%s",
                        light.location,
                        light.cluster,
                        light.device_name,
                    )
            elif intensity is not None and intensity == 0:
                logger.debug(
                    "Skipping restore of 0%% for %s/%s/%s (treat 0 as no value; control loop will set from schedule)",
                    light.location,
                    light.cluster,
                    light.device_name,
                )

    if restored_count > 0:
        logger.info("Restored %d light intensity values from database/Redis", restored_count)
    else:
        logger.warning("No light intensities restored - all at 0%% or first run")
