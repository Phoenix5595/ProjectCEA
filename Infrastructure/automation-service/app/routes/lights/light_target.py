"""Light target intensity endpoints (DB-backed, scheduler-sync)."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException

from app.config import ConfigLoader
from app.database import DatabaseManager
from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext
from app.events.operational_ports import OperationalEventSink
from app.repositories.light_target_intensity import validate_normal_target_intensity
from app.routes.lights import get_config, get_database, get_scheduler, router
from app.schemas.lights import LightIntensityUpdate, TargetIntensityControl
from shared.infra_logging import get_logger

logger = get_logger(__name__)


async def _sync_scheduler_light_intensities(
    database: DatabaseManager,
    scheduler: Any | None,
) -> None:
    """Synchronously install the complete snapshot after a light-target commit."""
    try:
        from app.main import container

        registry = container.get_control_engine().runtime_device_registry
        if registry is None:
            raise RuntimeError("Runtime device registry is not configured")
        snapshot = await registry.reload_after_commit()
        logger.info(
            "Installed runtime snapshot version=%s after light-target update", snapshot.version
        )
    except Exception as e:
        logger.error(f"Failed to update scheduler light intensities: {e}", exc_info=True)


async def _publish_schedule_changed(
    location: str,
    cluster: str,
    data: dict[str, Any],
) -> None:
    """Publish a SCHEDULE_CHANGED event."""
    try:
        from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus

        event_bus = get_event_bus()
        event = ConfigChangeEvent(
            event_type=ConfigEventType.SCHEDULE_CHANGED,
            location=location,
            cluster=cluster,
            config_type="schedules",
            data=data,
        )
        await event_bus.publish(event)
        logger.info(f"Published SCHEDULE_CHANGED event for {location}/{cluster}")
    except Exception as e:
        logger.warning(f"Failed to publish SCHEDULE_CHANGED event: {e}")


@router.post("/api/lights/{location}/{cluster}/{device_name}/target")
@emits_operational_mutation
async def set_target_intensity(
    location: str,
    cluster: str,
    device_name: str,
    control: TargetIntensityControl,
    config: ConfigLoader = Depends(get_config),
    database: DatabaseManager = Depends(get_database),
    scheduler: Any = Depends(get_scheduler),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    try:
        target_intensity = validate_normal_target_intensity(control.target_intensity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    devices = await config.get_devices()
    device_info = devices.get(location, {}).get(cluster, {}).get(device_name)

    if not device_info:
        raise HTTPException(
            status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}"
        )

    if device_info.get("device_type") != "light":
        raise HTTPException(status_code=400, detail=f"Device {device_name} is not a light")

    # Look up device_id from device_registry
    device_id = await database.device_repo.get_device_id(location, cluster, device_name)
    if device_id is None:
        raise HTTPException(status_code=404, detail=f"Device {device_name} not found in registry")

    # Get active mode (fallback veg)
    active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
    mode_name = str(active_mode.get("mode_name", "veg")) if active_mode else "veg"

    mode_info = await database.room_mode_repo.get_mode_by_name(mode_name)
    if not mode_info:
        raise HTTPException(status_code=404, detail=f"Mode '{mode_name}' not found")
    mode_id = mode_info["id"]
    prior_target = await database.light_target_intensity_repo.get_intensity(device_id, mode_id)

    # Write to light_target_intensity
    ok = await database.light_target_intensity_repo.set_intensity(
        device_id, mode_id, target_intensity
    )
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set light target intensity for {device_name}",
        )

    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="update",
            entity=EntityContext(
                entity_type="light_target",
                entity_id=f"{device_id}:{mode_id}",
                location=location,
                cluster=cluster,
            ),
            changes=safe_allowlisted_diff(
                before={"target_intensity": prior_target},
                after={"target_intensity": target_intensity},
                allowed_fields=frozenset({"target_intensity"}),
            ),
        ),
        context,
    )

    # Synchronous scheduler cache update
    await _sync_scheduler_light_intensities(database, scheduler)

    # Publish SCHEDULE_CHANGED event
    await _publish_schedule_changed(
        location,
        cluster,
        {
            "action": "light_target_intensity_updated",
            "device_name": device_name,
            "device_id": device_id,
            "target_intensity": target_intensity,
            "mode_name": mode_name,
        },
    )

    return {
        "success": True,
        "location": location,
        "cluster": cluster,
        "device": device_name,
        "device_id": device_id,
        "target_intensity": target_intensity,
        "mode_name": mode_name,
    }


@router.put("/api/lights/{device_id}/intensity")
@emits_operational_mutation
async def update_light_intensity(
    device_id: int,
    control: LightIntensityUpdate,
    database: DatabaseManager = Depends(get_database),
    scheduler: Any = Depends(get_scheduler),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    try:
        target_intensity = validate_normal_target_intensity(control.target_intensity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Look up device from registry
    device_type = await database.device_repo.get_device_type_by_id(device_id)
    if device_type is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")
    if device_type != "light":
        raise HTTPException(status_code=400, detail=f"Device {device_id} is not a light")

    # Get device location/cluster for event publishing
    light = await database.device_repo.get_light_by_id(device_id)
    if light is None:
        raise HTTPException(status_code=404, detail=f"Light {device_id} not found")

    location = light.location
    cluster = light.cluster
    device_name = light.device_name

    # Get active mode (fallback veg)
    active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
    mode_name = str(active_mode.get("mode_name", "veg")) if active_mode else "veg"

    mode_info = await database.room_mode_repo.get_mode_by_name(mode_name)
    if not mode_info:
        raise HTTPException(status_code=404, detail=f"Mode '{mode_name}' not found")
    mode_id = mode_info["id"]
    prior_target = await database.light_target_intensity_repo.get_intensity(device_id, mode_id)

    # Write to light_target_intensity
    ok = await database.light_target_intensity_repo.set_intensity(
        device_id, mode_id, target_intensity
    )
    if not ok:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to set light target intensity for {device_name}",
        )

    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="update",
            entity=EntityContext(
                entity_type="light_target",
                entity_id=f"{device_id}:{mode_id}",
                location=location,
                cluster=cluster,
            ),
            changes=safe_allowlisted_diff(
                before={"target_intensity": prior_target},
                after={"target_intensity": target_intensity},
                allowed_fields=frozenset({"target_intensity"}),
            ),
        ),
        context,
    )

    # Synchronous scheduler cache update
    await _sync_scheduler_light_intensities(database, scheduler)

    # Publish SCHEDULE_CHANGED event
    await _publish_schedule_changed(
        location,
        cluster,
        {
            "action": "light_target_intensity_updated",
            "device_name": device_name,
            "device_id": device_id,
            "target_intensity": target_intensity,
            "mode_name": mode_name,
        },
    )

    return {
        "success": True,
        "device_id": device_id,
        "device": device_name,
        "location": location,
        "cluster": cluster,
        "target_intensity": target_intensity,
        "mode_name": mode_name,
    }
