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
    """Create 10% light target rows for modes configured in one room and cluster."""
    mode_ids = await database.room_mode_repo.get_mode_ids_for_room_cluster(location, cluster)
    if not mode_ids:
        raise RuntimeError(f"No mode parameters exist for {location}/{cluster}")

    for mode_id in mode_ids:
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
