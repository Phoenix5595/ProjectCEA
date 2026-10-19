"""Grow calendar API."""

from __future__ import annotations

import asyncio
from datetime import date
import json
from typing import Any
from urllib.parse import urlsplit
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx

from app.calendar.flower_grow_plan import FlowerGrowPlanInput
from app.calendar.sync_worker import CalendarSyncWorker
from app.database import DatabaseManager
from app.events.mutation_context import (
    MutationOperation,
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext, JsonValue
from app.events.operational_ports import OperationalEventSink
from app.repositories.calendar import CalendarMutationError, GrowPlanCurrentRunAmbiguousError
from app.schemas.calendar import (
    CalendarCurrentRunResponse,
    CalendarEventCreate,
    CalendarEventUpdate,
    FlowerCalendarModeTransitionUpdate,
    FlowerGrowPlanRequest,
    GrowAssociationRequest,
    GrowClosePreviewResponse,
    GrowLifecycleResponse,
    GrowRevisionRequest,
    SyncConnectionCreate,
    SyncConnectionTest,
    SyncReconnectRequest,
)
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from shared.infra_logging import get_logger
from shared.nextcloud_credentials import decrypt_secret, encrypt_secret
from shared.nextcloud_dav import DavError, validate_dav_url

logger = get_logger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def get_database() -> DatabaseManager:
    raise RuntimeError("Dependency not injected")


def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for private in ("sync_lease_owner", "sync_lease_until", "sync_mirror_all"):
        out.pop(private, None)
    for key in ("start_date", "end_date"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    for key in ("created_at", "updated_at", "last_synced_at", "deleted_at"):
        if out.get(key) is not None and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    if out.get("grow_plan_id") is not None:
        out["grow_plan_id"] = str(out["grow_plan_id"])
    meta = out.get("metadata")
    if isinstance(meta, str):
        out["metadata"] = json.loads(meta)
    out["source"] = "manual"
    out["editable"] = out.get("deleted_at") is None
    out["colorKey"] = out.get("event_type", "planned_task")
    out["eventType"] = out.get("event_type", "planned_task")
    out["start"] = out["start_date"]
    out["end"] = out.get("end_date")
    eid = out["id"]
    out["id"] = f"manual:{eid}"
    out["numericId"] = eid
    return out


async def _sync_saved_events(
    database: DatabaseManager, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Remote failure never turns a committed local write into a failed save."""
    if not rows:
        return rows
    result = None
    try:
        async with asyncio.timeout(4.85):
            result = await CalendarSyncWorker(database).sync_events(
                [row["id"] for row in rows], deadline_seconds=4.6
            )
            current = await database.pool.fetch(
                "SELECT * FROM calendar_event WHERE id=ANY($1::integer[])",
                [row["id"] for row in rows],
            )
            by_id = {row["id"]: dict(row) for row in current}
            return [by_id.get(row["id"], row) for row in rows]
    except Exception:
        errors = (
            {error["event_id"]: error["code"] for error in result.errors}
            if result is not None
            else {}
        )
        return [
            {**row, "sync_error_code": errors.get(row["id"], "calendar_sync_unavailable")}
            for row in rows
        ]


def _raise_calendar_error(error: CalendarMutationError) -> None:
    detail: dict[str, Any] = {"code": error.code}
    if error.detail is not None:
        detail["detail"] = error.detail
    raise HTTPException(status_code=error.status_code, detail=detail)


def _lifecycle_payload(lifecycle: Any) -> dict[str, Any]:
    return {
        "grow_plan_id": lifecycle.grow_plan_id,
        "crop_batch_id": lifecycle.crop_batch_id,
        "state": lifecycle.state,
        "calendar_revision": lifecycle.calendar_revision,
        "original_drying_end": lifecycle.original_drying_end,
        "first_closed_at": lifecycle.first_closed_at,
        "closed_at": lifecycle.closed_at,
        "anniversary_at": lifecycle.anniversary_at,
        "dataset_id": lifecycle.dataset_id,
        "data_revision": lifecycle.data_revision,
        "published_revision": lifecycle.published_revision,
    }


def _close_preview_payload(preview: Any) -> dict[str, Any]:
    return {
        "lifecycle": _lifecycle_payload(preview.lifecycle),
        "revision": preview.revision,
        "phase_windows": list(preview.phase_windows),
        "drying_end": preview.drying_end,
        "affected_counts": preview.affected_counts,
        "gaps": list(preview.gaps),
        "scope_unresolved": list(preview.scope_unresolved),
        "conflicts": list(preview.conflicts),
        "newly_included": preview.newly_included,
        "released": preview.released,
        "unavailable": list(preview.unavailable),
    }


def _lifecycle_values(lifecycle: Any) -> dict[str, JsonValue]:
    return {
        "grow_plan_id": str(lifecycle.grow_plan_id),
        "crop_batch_id": lifecycle.crop_batch_id,
        "state": lifecycle.state,
        "calendar_revision": lifecycle.calendar_revision,
        "original_drying_end": _date_value(lifecycle.original_drying_end),
        "anniversary_at": (
            lifecycle.anniversary_at.isoformat() if lifecycle.anniversary_at else None
        ),
        "data_revision": lifecycle.data_revision,
        "published_revision": lifecycle.published_revision,
    }


def _emit_lifecycle_mutation(
    sink: OperationalEventSink,
    context: MutationRequestContext,
    operation: MutationOperation,
    before: Any,
    after: Any,
) -> None:
    values = _lifecycle_values(after)
    prior = _lifecycle_values(before) if before is not None else {}
    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation=operation,
            entity=EntityContext(
                entity_type="calendar_grow_lifecycle",
                entity_id=str(after.grow_plan_id),
            ),
            changes=safe_allowlisted_diff(prior, values, frozenset(values.keys())),
        ),
        context,
    )


def _mode_event_dto(row: dict[str, Any]) -> dict[str, Any]:
    ts = row["triggered_at"]
    d = ts.date() if hasattr(ts, "date") else ts
    title = f"Mode: {row.get('new_mode_name', '')}"
    if row.get("new_submode_name"):
        title += f" / {row['new_submode_name']}"
    return {
        "id": f"mode:{row.get('event_id', row.get('id', ''))}",
        "source": "mode_transition",
        "eventType": "mode_transition",
        "title": title,
        "start": d.isoformat() if hasattr(d, "isoformat") else str(d),
        "end": d.isoformat() if hasattr(d, "isoformat") else str(d),
        "location": row["location"],
        "cluster": row.get("cluster", "main"),
        "editable": False,
        "colorKey": "mode_transition",
    }


@router.get("/rooms")
async def list_rooms(database: DatabaseManager = Depends(get_database)) -> list[dict[str, Any]]:
    return await database.calendar_repo.list_room_profiles()


@router.get("/current-run", response_model=CalendarCurrentRunResponse | None)
async def get_current_run(
    location: str,
    database: DatabaseManager = Depends(get_database),
) -> CalendarCurrentRunResponse | None:
    """Read the room's current calendar span without consulting sensor history."""
    try:
        run = await database.calendar_repo.get_current_run(location)
    except GrowPlanCurrentRunAmbiguousError as error:
        raise HTTPException(
            status_code=409,
            detail={"code": "current_run_ambiguous", "detail": str(error)},
        ) from error
    if run is None:
        return None
    return CalendarCurrentRunResponse(
        location=run.location,
        grow_plan_id=run.grow_plan_id,
        start_date=run.start_date,
        end_date=run.end_date,
    )


@router.get("/flower-mode-transitions")
async def get_flower_mode_transitions(
    database: DatabaseManager = Depends(get_database),
) -> dict[str, bool]:
    return {"enabled": await database.calendar_repo.flower_calendar_mode_transitions_enabled()}


@router.put("/flower-mode-transitions")
@emits_operational_mutation
async def update_flower_mode_transitions(
    body: FlowerCalendarModeTransitionUpdate,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, bool]:
    previous = await database.calendar_repo.flower_calendar_mode_transitions_enabled()
    row = await database.calendar_repo.set_flower_calendar_mode_transitions_enabled(body.enabled)
    if row is None:
        raise HTTPException(
            status_code=500, detail="Failed to save Flower calendar mode transition setting"
        )
    enabled = row["calendar_mode_transitions_enabled"]
    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="update",
            entity=EntityContext(
                entity_type="flower_calendar_mode_transition", entity_id="Flower Room"
            ),
            changes=safe_allowlisted_diff(
                {"enabled": previous}, {"enabled": enabled}, frozenset({"enabled"})
            ),
        ),
        context,
    )
    return {"enabled": enabled}


@router.get("/events")
async def list_events(
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    location: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    cursor: str | None = None,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    events, next_cursor = await database.calendar_repo.list_events(
        from_date, to_date, location, limit, cursor
    )
    items = [_serialize_event(e) for e in events]

    modes = await database.calendar_repo.list_mode_transitions(from_date, to_date, location)
    for m in modes:
        loc = m.get("location")
        # Band-aid for data quality: NULL location in mode_transition_history
        # should be investigated and fixed at the source. Defaulting here
        # masks the underlying issue but keeps the calendar view working.
        if not loc:
            logger.warning(
                "Mode transition %s has NULL location — defaulting to Flower Room. "
                "This masks a data quality issue in mode_transition_history.",
                m.get("id", "unknown"),
            )
            m["location"] = "Flower Room"
        items.append(_mode_event_dto(m))

    items.sort(key=lambda x: x.get("start", ""))
    return {"items": items, "next_cursor": next_cursor}


@router.get("/events/{event_id}")
async def get_event(
    event_id: int,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    row = await database.calendar_repo.get_event(event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return _serialize_event(row)


@router.post("/events")
@emits_operational_mutation
async def create_event(
    body: CalendarEventCreate,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    data = body.model_dump()
    data["metadata"] = data.get("metadata") or {}
    try:
        row = await database.calendar_repo.create_event(data)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    _emit_calendar_mutation(sink, context, "create", row, {}, _calendar_event_values(row))
    return _serialize_event((await _sync_saved_events(database, [row]))[0])


@router.patch("/events/{event_id}")
@emits_operational_mutation
async def update_event(
    event_id: int,
    body: CalendarEventUpdate,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    previous = await database.calendar_repo.get_event(event_id)
    try:
        row = await database.calendar_repo.update_event(
            event_id, body.model_dump(exclude_unset=True)
        )
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    if previous:
        _emit_calendar_mutation(
            sink,
            context,
            "update",
            row,
            _calendar_event_values(previous),
            _calendar_event_values(row),
        )
    return _serialize_event((await _sync_saved_events(database, [row]))[0])


@router.delete("/events/{event_id}")
@emits_operational_mutation
async def delete_event(
    event_id: int,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    previous = await database.calendar_repo.get_event(event_id)
    try:
        ok = await database.calendar_repo.soft_delete_event(event_id)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    if previous:
        _emit_calendar_mutation(
            sink,
            context,
            "delete",
            previous,
            {"id": _calendar_event_values(previous)["id"]},
            {},
            frozenset({"id"}),
        )
    deleted = await database.calendar_repo.get_event(event_id, include_deleted=True)
    saved = (await _sync_saved_events(database, [deleted]))[0] if deleted else previous
    return {"status": "deleted", "event": _serialize_event(saved) if saved else None}


@router.post("/events/{event_id}/grow-association")
@emits_operational_mutation
async def associate_grow_phase(
    event_id: int,
    body: GrowAssociationRequest,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    previous = await database.calendar_repo.get_event(event_id)
    try:
        row = await database.calendar_repo.associate_grow_phase(
            event_id, body.grow_plan_id, body.expected_revision
        )
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    if previous:
        _emit_calendar_mutation(
            sink,
            context,
            "update",
            row,
            _calendar_event_values(previous),
            _calendar_event_values(row),
            frozenset(
                {
                    "id",
                    "location",
                    "cluster",
                    "event_type",
                    "title",
                    "start_date",
                    "end_date",
                    "all_day",
                    "grow_plan_id",
                    "crop_batch_id",
                }
            ),
        )
    return _serialize_event((await _sync_saved_events(database, [row]))[0])


@router.post("/grow-plans/flower")
@emits_operational_mutation
async def create_flower_grow_plan(
    body: FlowerGrowPlanRequest,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    inp = FlowerGrowPlanInput(
        crop_name=body.crop_name,
        environment=body.environment,
        flower_end=body.flower_end,
        flower_weeks=body.flower_weeks,
        include_pot_phases=body.include_pot_phases,
        clone_weeks=body.clone_weeks,
        pot_weeks=body.pot_weeks,
        bed_weeks=body.bed_weeks,
        stretch_days=body.stretch_days,
        ripen_days=body.ripen_days,
        drying_days=body.drying_days,
        auto_mode_transition=body.auto_mode_transition,
    )
    try:
        result, err = await database.calendar_repo.create_flower_grow_plan(
            inp, body.idempotency_key
        )
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    if err:
        raise HTTPException(status_code=400, detail=err)
    assert result is not None
    if "crop_batch_id" in result:
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="create",
                entity=EntityContext(
                    entity_type="calendar_grow_plan",
                    entity_id=result["grow_plan_id"],
                ),
                changes=safe_allowlisted_diff(
                    {},
                    {
                        "crop_batch_id": result["crop_batch_id"],
                        "event_count": len(result["events"]),
                    },
                    frozenset({"crop_batch_id", "event_count"}),
                ),
            ),
            context,
        )
    return {
        "grow_plan_id": result["grow_plan_id"],
        "crop_batch_id": result["crop_batch_id"],
        "events": [
            _serialize_event(event)
            for event in await _sync_saved_events(database, result["events"])
        ],
    }


@router.get("/grow-plans/{grow_plan_id}/lifecycle", response_model=GrowLifecycleResponse)
async def get_grow_lifecycle(
    grow_plan_id: uuid.UUID,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    try:
        lifecycle = await database.calendar_repo.get_grow_lifecycle(grow_plan_id)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    return _lifecycle_payload(lifecycle)


@router.get(
    "/grow-plans/{grow_plan_id}/close-preview",
    response_model=GrowClosePreviewResponse,
)
async def preview_grow_close(
    grow_plan_id: uuid.UUID,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    try:
        preview = await database.calendar_repo.preview_grow_close(grow_plan_id)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    return _close_preview_payload(preview)


@router.post("/grow-plans/{grow_plan_id}/close", response_model=GrowLifecycleResponse)
@emits_operational_mutation
async def close_grow(
    grow_plan_id: uuid.UUID,
    body: GrowRevisionRequest,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    try:
        before = await database.calendar_repo.get_grow_lifecycle(grow_plan_id)
        after = await database.calendar_repo.close_grow(grow_plan_id, body.expected_revision)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    _emit_lifecycle_mutation(sink, context, "update", before, after)
    return _lifecycle_payload(after)


@router.post("/grow-plans/{grow_plan_id}/reopen", response_model=GrowLifecycleResponse)
@emits_operational_mutation
async def reopen_grow(
    grow_plan_id: uuid.UUID,
    body: GrowRevisionRequest,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    try:
        before = await database.calendar_repo.get_grow_lifecycle(grow_plan_id)
        after = await database.calendar_repo.reopen_grow(grow_plan_id, body.expected_revision)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    _emit_lifecycle_mutation(sink, context, "update", before, after)
    return _lifecycle_payload(after)


@router.delete("/grow-plans/{grow_plan_id}")
@emits_operational_mutation
async def delete_grow_plan(
    grow_plan_id: uuid.UUID,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    try:
        changed = await database.calendar_repo.delete_grow_plan(grow_plan_id)
        count = len(changed)
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    if count:
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="delete",
                entity=EntityContext(entity_type="calendar_grow_plan", entity_id=str(grow_plan_id)),
                changes=safe_allowlisted_diff(
                    {"event_count": count}, {}, frozenset({"event_count"})
                ),
            ),
            context,
        )
    return {
        "deleted": count,
        "grow_plan_id": str(grow_plan_id),
        "events": [
            _serialize_event(event) for event in await _sync_saved_events(database, changed)
        ],
    }


@router.get("/mode-schedule/{location}/{cluster}")
async def mode_schedule(
    location: str,
    cluster: str,
    on_date: date | None = Query(None, alias="date"),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    scheduler = CalendarModeScheduler(database)
    d = on_date or scheduler._today()
    expected = await scheduler.get_expected_mode(location, cluster, d)
    active = await database.room_mode_repo.get_active_mode(location, cluster)
    return {
        "date": d.isoformat(),
        "expected": expected,
        "active": {
            "mode_name": active.get("mode_name") if active else None,
            "submode_name": active.get("submode_name") if active else None,
        },
    }


@router.get("/sync/connections")
async def get_sync_connection(
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any] | None:
    return await database.calendar_repo.get_sync_connection()


@router.post("/sync/connections")
@emits_operational_mutation
async def create_sync_connection(
    body: SyncConnectionCreate,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    previous = await database.calendar_repo.get_sync_connection()
    try:
        enc = encrypt_secret(body.app_password)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=503, detail={"code": "calendar_credentials_unavailable"}
        ) from error
    try:
        discovery = await CalendarSyncWorker(database).discover(
            body.caldav_base_url, body.username, body.app_password
        )
        target = (
            validate_dav_url(body.target_calendar_url, origin=body.caldav_base_url).rstrip("/")
            + "/"
        )
        if not any(
            calendar["url"] == target and calendar["name"].casefold() == "growing"
            for calendar in discovery.calendars
        ):
            raise DavError("calendar_collection_unavailable")
    except DavError as error:
        raise HTTPException(status_code=400, detail={"code": error.code}) from error
    except (httpx.TransportError, OSError, TimeoutError) as error:
        raise HTTPException(
            status_code=503, detail={"code": "calendar_transport_failed"}
        ) from error
    row = await database.calendar_repo.upsert_sync_connection(
        {
            "display_name": body.display_name or body.username,
            "account_email": body.username,
            "caldav_base_url": body.caldav_base_url.rstrip("/"),
            "credentials_encrypted": enc,
            "target_calendar_url": target,
            "files_root_url": discovery.files_root_url,
            "instance_id": discovery.instance_id,
        }
    )
    _emit_calendar_mutation(
        sink,
        context,
        "create" if previous is None else "update",
        row,
        _calendar_connection_values(previous),
        _calendar_connection_values(row),
        frozenset({"id", "display_name", "target_calendar_host"}),
        "calendar_sync_connection",
    )
    return row


@router.get("/sync/connections/{connection_id}/mirror-preview")
async def mirror_preview(
    connection_id: int, database: DatabaseManager = Depends(get_database)
) -> dict[str, Any]:
    try:
        return await database.calendar_repo.mirror_preview(connection_id)
    except CalendarMutationError as error:
        _raise_calendar_error(error)


@router.post("/sync/connections/{connection_id}/reconnect")
@emits_operational_mutation
async def reconnect_and_mirror(
    connection_id: int,
    body: SyncReconnectRequest,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    if not body.backup_confirmed:
        raise HTTPException(status_code=400, detail={"code": "calendar_backup_required"})
    connection = await database.calendar_repo.get_sync_credentials(connection_id)
    if connection is None:
        raise HTTPException(status_code=404, detail={"code": "calendar_connection_not_found"})
    try:
        password = decrypt_secret(bytes(connection["credentials_encrypted"]))
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=503, detail={"code": "calendar_credentials_unavailable"}
        ) from error
    try:
        worker = CalendarSyncWorker(database)
        discovery = await worker.discover(
            connection["caldav_base_url"], connection["account_email"], password
        )
        if (
            discovery.instance_id != connection["instance_id"]
            or discovery.files_root_url != connection["files_root_url"]
        ):
            raise DavError("calendar_instance_changed")
        if not any(
            calendar["url"] == connection["target_calendar_url"]
            and calendar["name"].casefold() == "growing"
            for calendar in discovery.calendars
        ):
            raise DavError("calendar_collection_unavailable")
    except DavError as error:
        raise HTTPException(status_code=400, detail={"code": error.code}) from error
    except (httpx.TransportError, OSError, TimeoutError) as error:
        raise HTTPException(
            status_code=503, detail={"code": "calendar_transport_failed"}
        ) from error
    try:
        result = await database.calendar_repo.reconnect_and_mirror(
            connection_id, body.expected_generation
        )
    except CalendarMutationError as error:
        _raise_calendar_error(error)
    events = result.pop("event_ids")
    sync = await worker.sync_events(events)
    result["sync"] = sync.as_dict()
    _emit_calendar_sync_action(sink, context, sync.as_dict())
    return result


@router.delete("/sync/connections")
@emits_operational_mutation
async def remove_sync_connection(
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, str]:
    previous = await database.calendar_repo.get_sync_connection()
    deleted = await database.calendar_repo.delete_sync_connection()
    if deleted and previous:
        _emit_calendar_mutation(
            sink,
            context,
            "delete",
            previous,
            _calendar_connection_values(previous),
            {},
            frozenset({"id", "display_name", "target_calendar_host"}),
            "calendar_sync_connection",
        )
    return {"status": "disconnected"}


@router.post("/sync/connections/test")
async def test_sync_connection(
    body: SyncConnectionTest,
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, str]]:
    try:
        calendars = await CalendarSyncWorker(database).test_connection(
            body.caldav_base_url.rstrip("/"), body.username, body.app_password
        )
        return [
            {
                "name": calendar["name"],
                "url": urlsplit(calendar["url"])._replace(
                    netloc=urlsplit(calendar["url"]).hostname or ""
                ).geturl(),
            }
            for calendar in calendars
        ]
    except DavError as error:
        raise HTTPException(status_code=400, detail={"code": error.code}) from error
    except (httpx.TransportError, OSError, TimeoutError) as error:
        raise HTTPException(
            status_code=503, detail={"code": "calendar_transport_failed"}
        ) from error
    except Exception as error:
        # Remote/client exceptions can include submitted credentials or URLs.
        raise HTTPException(
            status_code=400, detail={"code": "calendar_connection_test_failed"}
        ) from error


@router.post("/sync/run")
@emits_operational_mutation
async def run_sync(
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    worker = CalendarSyncWorker(database)
    result = await worker.run_sync()
    _emit_calendar_sync_action(sink, context, result)
    return result


def _emit_calendar_mutation(
    sink: OperationalEventSink,
    context: MutationRequestContext,
    operation: MutationOperation,
    row: dict[str, Any],
    before: dict[str, JsonValue],
    after: dict[str, JsonValue],
    allowed_fields: frozenset[str] | None = None,
    entity_type: str = "calendar_event",
) -> None:
    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation=operation,
            entity=EntityContext(
                entity_type=entity_type,
                entity_id=str(row["id"]),
                location=row.get("location"),
                cluster=row.get("cluster"),
            ),
            changes=safe_allowlisted_diff(
                before,
                after,
                allowed_fields
                or frozenset(
                    {
                        "id",
                        "location",
                        "cluster",
                        "event_type",
                        "title",
                        "start_date",
                        "end_date",
                        "all_day",
                        "grow_plan_id",
                        "crop_batch_id",
                    }
                ),
            ),
        ),
        context,
    )


def _calendar_event_values(row: dict[str, Any]) -> dict[str, JsonValue]:
    return {
        "id": row.get("id"),
        "location": row.get("location"),
        "cluster": row.get("cluster"),
        "event_type": row.get("event_type"),
        "title": row.get("title"),
        "start_date": _date_value(row.get("start_date")),
        "end_date": _date_value(row.get("end_date")),
        "all_day": row.get("all_day"),
        "grow_plan_id": (
            str(row.get("grow_plan_id")) if row.get("grow_plan_id") is not None else None
        ),
        "crop_batch_id": row.get("crop_batch_id"),
    }


def _calendar_connection_values(row: dict[str, Any] | None) -> dict[str, JsonValue]:
    if row is None:
        return {}
    target_calendar_url = row.get("target_calendar_url")
    return {
        "id": row.get("id"),
        "display_name": row.get("display_name"),
        "target_calendar_host": urlsplit(target_calendar_url).hostname
        if isinstance(target_calendar_url, str)
        else None,
    }


def _emit_calendar_sync_action(
    sink: OperationalEventSink,
    context: MutationRequestContext,
    result: dict[str, Any],
) -> None:
    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="action",
            entity=EntityContext(entity_type="calendar_sync", entity_id="active"),
            changes=safe_allowlisted_diff(
                {"pushed_events": 0, "deleted_events": 0},
                {
                    "pushed_events": result.get("pushed", 0),
                    "deleted_events": result.get("deleted", 0),
                },
                frozenset({"pushed_events", "deleted_events"}),
            ),
        ),
        context,
    )


def _date_value(value: object) -> JsonValue:
    return (
        value.isoformat() if isinstance(value, date) else value if isinstance(value, str) else None
    )
