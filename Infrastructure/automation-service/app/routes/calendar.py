"""Grow calendar API."""

from __future__ import annotations

from datetime import date
import json
import os
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.calendar.credentials import encrypt_secret
from app.calendar.flower_grow_plan import FlowerGrowPlanInput
from app.calendar.sync_worker import CalendarSyncWorker
from app.database import DatabaseManager
from app.schemas.calendar import (
    CalendarEventCreate,
    CalendarEventUpdate,
    FlowerGrowPlanRequest,
    SyncConnectionCreate,
    SyncConnectionTest,
)
from app.services.calendar_mode_scheduler import CalendarModeScheduler
from shared.infra_logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


def get_database() -> DatabaseManager:
    raise RuntimeError("Dependency not injected")


def _serialize_event(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
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
async def create_event(
    body: CalendarEventCreate,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    data = body.model_dump()
    data["metadata"] = data.get("metadata") or {}
    row = await database.calendar_repo.create_event(data)
    return _serialize_event(row)


@router.patch("/events/{event_id}")
async def update_event(
    event_id: int,
    body: CalendarEventUpdate,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    row = await database.calendar_repo.update_event(event_id, body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return _serialize_event(row)


@router.delete("/events/{event_id}")
async def delete_event(
    event_id: int,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, str]:
    ok = await database.calendar_repo.soft_delete_event(event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "deleted"}


@router.post("/grow-plans/flower")
async def create_flower_grow_plan(
    body: FlowerGrowPlanRequest,
    database: DatabaseManager = Depends(get_database),
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
    result, err = await database.calendar_repo.create_flower_grow_plan(inp, body.idempotency_key)
    if err:
        if err.startswith("Overlaps"):
            raise HTTPException(status_code=409, detail=err)
        raise HTTPException(status_code=400, detail=err)
    assert result is not None
    return {
        "grow_plan_id": result["grow_plan_id"],
        "crop_batch_id": result["crop_batch_id"],
        "events": [_serialize_event(e) for e in result["events"]],
    }


@router.delete("/grow-plans/{grow_plan_id}")
async def delete_grow_plan(
    grow_plan_id: uuid.UUID,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    count = await database.calendar_repo.delete_grow_plan(grow_plan_id)
    return {"deleted": count, "grow_plan_id": str(grow_plan_id)}


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
async def create_sync_connection(
    body: SyncConnectionCreate,
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    enc = encrypt_secret(body.app_password)
    row = await database.calendar_repo.upsert_sync_connection(
        {
            "display_name": body.display_name or body.username,
            "account_email": body.username,
            "caldav_base_url": body.caldav_base_url.rstrip("/"),
            "credentials_encrypted": enc,
            "target_calendar_url": body.target_calendar_url,
        }
    )
    return row


@router.delete("/sync/connections")
async def remove_sync_connection(
    database: DatabaseManager = Depends(get_database),
) -> dict[str, str]:
    await database.calendar_repo.delete_sync_connection()
    return {"status": "disconnected"}


@router.post("/sync/connections/test")
async def test_sync_connection(
    body: SyncConnectionTest,
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, str]]:
    # Pydantic validator already rejects http:// URLs unless override is set
    if (
        body.caldav_base_url.startswith("http://")
        and os.getenv("CALDAV_ALLOW_HTTP_TEST", "").lower() == "true"
    ):
        logger.warning("CALDAV connection test using HTTP (dev override active)")
    worker = CalendarSyncWorker(database)
    try:
        return await worker.test_connection(
            body.caldav_base_url.rstrip("/"), body.username, body.app_password
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/sync/run")
async def run_sync(
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    worker = CalendarSyncWorker(database)
    return await worker.run_sync()
