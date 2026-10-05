"""Room schedule endpoints."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

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
from app.schemas.schedules import RoomScheduleCreate
from app.services.room_schedule_service import (
    ActiveModeMissingError,
    ProfileNotConfiguredError,
    RoomScheduleService,
    validate_room_schedule_times,
)
from shared.infra_logging import get_logger

from .base import (
    get_database,
)

logger = get_logger(__name__)

router = APIRouter()


def _to_hhmm(value: Any) -> str:
    if value is None:
        return "06:00"
    if hasattr(value, "hour"):
        return f"{value.hour:02d}:{value.minute:02d}"
    s = str(value).strip()
    return s[:5] if len(s) >= 5 else "06:00"


def get_room_schedule_service() -> RoomScheduleService:
    """Provide the transaction-owning room schedule service."""
    from app.main import container

    return container.get_room_schedule_service()


async def _commit_room_schedule_boundary(
    location: str,
    cluster: str,
    schedule: RoomScheduleCreate,
    result: dict[str, Any],
    context: MutationRequestContext,
    sink: OperationalEventSink,
) -> dict[str, Any]:
    """Emit the committed mutation, broadcast, and shape the HTTP response.

    The service owns the transaction and the config-event/cache boundary; this
    boundary owns only HTTP-facing notices and returns a truthful warning.
    """
    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="update",
            entity=EntityContext(
                entity_type="room_schedule",
                entity_id=f"{location}:{cluster}",
                location=location,
                cluster=cluster,
            ),
            changes=safe_allowlisted_diff(
                _room_schedule_values(result.get("prior_parameters")),
                {
                    "day_start_time": schedule.day_start_time,
                    "night_start_time": schedule.night_start_time,
                    "light_ramp_up_minutes": schedule.ramp_up_duration or 30,
                    "light_ramp_down_minutes": schedule.ramp_down_duration or 15,
                },
                frozenset(
                    {
                        "day_start_time",
                        "night_start_time",
                        "light_ramp_up_minutes",
                        "light_ramp_down_minutes",
                    }
                ),
            ),
        ),
        context,
    )
    try:
        from app.routes.websocket import broadcast_room_schedule_update

        await broadcast_room_schedule_update(
            location,
            cluster,
            {
                "day_start_time": schedule.day_start_time,
                "day_end_time": schedule.day_end_time,
                "night_start_time": schedule.night_start_time,
                "night_end_time": schedule.night_end_time,
                "ramp_up_duration": schedule.ramp_up_duration,
                "ramp_down_duration": schedule.ramp_down_duration,
            },
        )
    except Exception as e:  # noqa: BLE001 - broadcast is best-effort
        logger.warning(f"Failed to broadcast room schedule update: {e}")
    response: dict[str, Any] = {
        "success": True,
        "location": location,
        "cluster": cluster,
        "schedules_created": result.get("schedules_created", 0),
        "devices_configured": result.get("devices_configured", 0),
    }
    warning = result.get("warning")
    if warning:
        response["warning"] = warning
    return response


@router.post("/api/room-schedule/sync-all-from-mode-parameters")
@emits_operational_mutation
async def sync_all_room_schedules_from_mode_parameters(
    service: RoomScheduleService = Depends(get_room_schedule_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Synchronize every configured room through the shared service."""
    outcomes = await service.sync_all()
    results: list[dict[str, Any]] = []
    for outcome in outcomes:
        if outcome.get("success"):
            response = await _commit_room_schedule_boundary(
                str(outcome["location"]),
                str(outcome["cluster"]),
                cast("RoomScheduleCreate", outcome["schedule"]),
                outcome,
                context,
                sink,
            )
            entry: dict[str, Any] = {
                "location": outcome["location"],
                "cluster": outcome["cluster"],
                "success": True,
                "schedules_created": response["schedules_created"],
                "devices_configured": response["devices_configured"],
            }
            if outcome.get("warning"):
                entry["warning"] = outcome["warning"]
            results.append(entry)
        else:
            results.append(
                {
                    "location": outcome["location"],
                    "cluster": outcome["cluster"],
                    "success": False,
                    "error": outcome.get("error"),
                }
            )
    return {"synced": results}


@router.get("/api/room-schedule/{location}/{cluster}")
async def get_room_schedule(
    location: str, cluster: str, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    """Return photoperiod and ramp times from mode_parameters for the active mode."""
    try:
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if not active_mode:
            return {
                "day_start_time": "06:00",
                "day_end_time": "20:00",
                "night_start_time": "20:00",
                "night_end_time": "06:00",
                "ramp_up_duration": 30,
                "ramp_down_duration": 15,
            }

        mode_name = active_mode.get("mode_name", "veg")
        submode_name = active_mode.get("submode_name")
        params = await database.room_mode_repo.get_mode_parameters(
            location, cluster, mode_name, submode_name
        )

        if not params:
            return {
                "day_start_time": "06:00",
                "day_end_time": "20:00",
                "night_start_time": "20:00",
                "night_end_time": "06:00",
                "ramp_up_duration": 30,
                "ramp_down_duration": 15,
            }

        day_start = _to_hhmm(params.get("day_start_time"))
        night_start = _to_hhmm(params.get("night_start_time"))

        return {
            "day_start_time": day_start,
            "day_end_time": night_start,
            "night_start_time": night_start,
            "night_end_time": day_start,
            "ramp_up_duration": params.get("light_ramp_up_minutes", 30) or 30,
            "ramp_down_duration": params.get("light_ramp_down_minutes", 15) or 15,
        }
    except Exception as e:
        logger.warning(f"Error retrieving room schedule from mode_parameters: {e}")
        return {
            "day_start_time": "06:00",
            "day_end_time": "20:00",
            "night_start_time": "20:00",
            "night_end_time": "06:00",
            "ramp_up_duration": 30,
            "ramp_down_duration": 15,
        }


@router.post("/api/room-schedule/{location}/{cluster}")
@emits_operational_mutation
async def save_room_schedule(
    location: str,
    cluster: str,
    schedule: RoomScheduleCreate,
    service: RoomScheduleService = Depends(get_room_schedule_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Replace the room's non-light schedules from the requested bounds."""
    try:
        validate_room_schedule_times(schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    room_devices = await service.get_room_devices(location, cluster)
    if not room_devices:
        raise HTTPException(status_code=404, detail=f"No devices found for {location}/{cluster}")

    try:
        result = await service.save(location, cluster, schedule)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error saving room schedule for {location}/{cluster}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}") from e
    return await _commit_room_schedule_boundary(location, cluster, schedule, result, context, sink)


async def sync_room_schedule_from_mode_parameters(
    location: str,
    cluster: str,
    service: RoomScheduleService,
    context: MutationRequestContext,
    sink: OperationalEventSink,
) -> dict[str, Any]:
    """Derive the active profile's bounds in the service and commit them.

    Derivation and persistence are owned by ``RoomScheduleService``; this
    boundary translates errors to HTTP and emits the committed notices.
    """
    try:
        result = await service.sync_one(location, cluster)
    except (ActiveModeMissingError, ProfileNotConfiguredError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error syncing room schedule for {location}/{cluster}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}") from e
    return await _commit_room_schedule_boundary(
        location, cluster, cast("RoomScheduleCreate", result["schedule"]), result, context, sink
    )


@router.post("/api/room-schedule/{location}/{cluster}/sync-from-mode-parameters")
@emits_operational_mutation
async def sync_room_schedule(
    location: str,
    cluster: str,
    service: RoomScheduleService = Depends(get_room_schedule_service),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    return await sync_room_schedule_from_mode_parameters(location, cluster, service, context, sink)


def _room_schedule_values(parameters: dict[str, Any] | None) -> dict[str, str | int | None]:
    if parameters is None:
        return {}
    day_start_time = parameters.get("day_start_time")
    night_start_time = parameters.get("night_start_time")
    return {
        "day_start_time": _to_hhmm(day_start_time) if day_start_time is not None else None,
        "night_start_time": _to_hhmm(night_start_time) if night_start_time is not None else None,
        "light_ramp_up_minutes": parameters.get("light_ramp_up_minutes"),
        "light_ramp_down_minutes": parameters.get("light_ramp_down_minutes"),
    }
