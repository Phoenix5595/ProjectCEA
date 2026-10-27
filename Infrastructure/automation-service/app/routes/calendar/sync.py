"""Grow calendar Nextcloud sync endpoints: connections, mirror preview, reconnect, test and run."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
import httpx

from app.calendar.sync_worker import CalendarSyncWorker
from app.database import DatabaseManager
from app.events.mutation_context import (
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext, JsonValue
from app.events.operational_ports import OperationalEventSink
from app.repositories.calendar_errors import CalendarMutationError
from app.routes.calendar.dependencies import get_database
from app.routes.calendar.support import _emit_calendar_mutation, _raise_calendar_error
from app.schemas.calendar import SyncConnectionCreate, SyncConnectionTest, SyncReconnectRequest
from shared.nextcloud_credentials import decrypt_secret, encrypt_secret
from shared.nextcloud_dav import DavError, validate_dav_url

router = APIRouter()
# Display name (case-insensitive) of the only Nextcloud calendar the Pi writes to.
TARGET_CALENDAR_NAME = "Culture"


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
            calendar["url"] == target
            and calendar["name"].casefold() == TARGET_CALENDAR_NAME.casefold()
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
            and calendar["name"].casefold() == TARGET_CALENDAR_NAME.casefold()
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
                "url": urlsplit(calendar["url"])
                ._replace(netloc=urlsplit(calendar["url"]).hostname or "")
                .geturl(),
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
