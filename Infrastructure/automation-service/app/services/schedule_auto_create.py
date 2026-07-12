"""Helpers for auto-creating schedule-related rows when devices are created."""

from __future__ import annotations

from app.database import DatabaseManager
from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus
from shared.infra_logging import get_logger

logger = get_logger(__name__)


async def create_default_intensity_for_light(
    database: DatabaseManager,
    device_id: int,
    location: str,
    cluster: str,
) -> None:
    """Create default light target intensity rows (10%) for all modes.

    This is called automatically after a light device is created so that
    every mode has a fallback intensity anchor.  Failures are logged but
    do not bubble up — light creation must always succeed.
    """
    try:
        modes = await database.room_mode_repo.get_room_modes()
        if not modes:
            logger.warning(f"No room modes found for {location}/{cluster}")
            return

        for mode in modes:
            mode_id = mode.get("id")
            if mode_id is None:
                continue
            await database.light_target_intensity_repo.set_intensity(device_id, mode_id, 10.0)

        event_bus = get_event_bus()
        await event_bus.publish(
            ConfigChangeEvent(
                event_type=ConfigEventType.SCHEDULE_CHANGED,
                location=location,
                cluster=cluster,
                config_type="light_target_intensity",
                data={"device_id": device_id, "action": "created_defaults"},
            )
        )
        logger.info(f"Created default intensities for light {device_id} in {location}/{cluster}")
    except Exception as e:
        logger.error(
            f"Failed to create default intensities for light {device_id} in {location}/{cluster}: {e}",
            exc_info=True,
        )
