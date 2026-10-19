"""Confirmed outbound CalDAV writes; no inbound synchronization is performed."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any
from urllib.parse import quote
from uuid import uuid4
from xml.etree import ElementTree as ET

import httpx
from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

from app.database import DatabaseManager
from shared.infra_logging import get_logger
from shared.nextcloud_credentials import decrypt_secret
from shared.nextcloud_dav import (
    CALDAV,
    DAV,
    DavDiscovery,
    DavError,
    dav_request,
    discover_nextcloud,
    parse_dav_xml,
    require_dav_success,
    resolve_dav_href,
    successful_properties,
    validate_dav_url,
)

logger = get_logger(__name__)
BLOCKED_ERRORS = frozenset(
    {
        "calendar_auth_failed",
        "calendar_collection_unavailable",
        "calendar_remote_conflict",
        "calendar_remote_response_invalid",
        "calendar_credentials_unavailable",
        "calendar_url_invalid",
    }
)


def _room_prefix(location: str, title: str) -> str:
    short = {"Flower Room": "Flower", "Veg Room": "Veg", "Lab": "Lab"}.get(location, location)
    return title if title.startswith(f"[{short}]") else f"[{short}] {title}"


def serialize_calendar_event(row: dict[str, Any]) -> bytes:
    event = IEvent()
    event.add("uid", row["ical_uid"])
    event.add("dtstamp", row.get("updated_at") or row.get("created_at") or datetime.now(UTC))
    event.add("summary", _room_prefix(row["location"], row["title"]))
    event.add("dtstart", row["start_date"])
    event.add("dtend", (row["end_date"] or row["start_date"]) + timedelta(days=1))
    event.add("X-CEA-EVENT-ID", str(row["id"]))
    if row.get("notes") is not None:
        event.add("description", row["notes"])
    calendar = ICalendar()
    calendar.add("version", "2.0")
    calendar.add("prodid", "-//SJCEA//Growing//EN")
    calendar.add_component(event)
    return calendar.to_ical()


def _events(payload: bytes):
    try:
        return [
            component
            for component in ICalendar.from_ical(payload).walk()
            if component.name == "VEVENT"
        ]
    except Exception as error:
        raise DavError("calendar_remote_response_invalid") from error


def _content(event: IEvent) -> tuple[str, str, date, date, str, str]:
    start = event.decoded("DTSTART") if "DTSTART" in event else None
    end = event.decoded("DTEND") if "DTEND" in event else None
    if type(start) is not date or type(end) is not date:
        raise DavError("calendar_remote_conflict")
    return (
        str(event.get("UID", "")),
        str(event.get("SUMMARY", "")),
        start,
        end,
        str(event.get("DESCRIPTION", "")),
        str(event.get("X-CEA-EVENT-ID", "")),
    )


@dataclass(frozen=True, slots=True)
class CalendarSyncResult:
    ok: bool
    confirmed: tuple[int, ...]
    pending: tuple[int, ...]
    errors: tuple[dict[str, Any], ...]
    pushed: int = 0
    deleted: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class CalendarSyncWorker:
    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        if not hasattr(db, "_calendar_remote_slots"):
            db._calendar_remote_slots = asyncio.Semaphore(4)
        self._slots = db._calendar_remote_slots
        # HTTPX's request INFO logs contain instance-specific account/href URLs.
        # This transport logs only fixed codes and local event IDs below.
        logging.getLogger("httpx").setLevel(logging.WARNING)

    async def discover(
        self, caldav_base_url: str, username: str, app_password: str
    ) -> DavDiscovery:
        async with httpx.AsyncClient(
            auth=httpx.BasicAuth(username, app_password),
            trust_env=False,
            follow_redirects=False,
            timeout=5.0,
        ) as client:
            async with asyncio.timeout(15):
                return await discover_nextcloud(client, caldav_base_url)

    async def test_connection(
        self, caldav_base_url: str, username: str, app_password: str
    ) -> list[dict[str, str]]:
        discovered = await self.discover(caldav_base_url, username, app_password)
        return [
            {
                **calendar,
                "files_root_url": discovered.files_root_url,
                "principal_url": discovered.principal_url,
            }
            for calendar in discovered.calendars
        ]

    async def run_sync(self) -> dict[str, Any]:
        due = await self._db.calendar_repo.events_pending_sync()
        return (await self.sync_events([row["id"] for row in due])).as_dict()

    async def sync_events(
        self, event_ids: Sequence[int], *, deadline_seconds: float = 5.0
    ) -> CalendarSyncResult:
        """One wall-clock deadline covers this committed mutation's exact IDs."""
        ids = tuple(dict.fromkeys(event_ids))
        if not ids:
            return CalendarSyncResult(True, (), (), ())
        started = asyncio.get_running_loop().time()
        deadline = started + max(0.0, deadline_seconds)
        # Reserve final status persistence/reads; transport cancellation never
        # assumes that the remote server rejected a request already sent.
        work_deadline = max(started, deadline - min(0.4, deadline_seconds / 4))
        tasks = [asyncio.create_task(self._sync_one(event_id, work_deadline)) for event_id in ids]
        try:
            async with asyncio.timeout_at(work_deadline):
                await asyncio.gather(*tasks)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        confirmed, pending, errors = [], [], []
        pushed = deleted = 0
        try:
            remaining = max(0.001, deadline - asyncio.get_running_loop().time())
            async with asyncio.timeout(remaining):
                rows = await self._db.pool.fetch(
                    "SELECT e.id,e.sync_status,e.sync_error_code,e.deleted_at,EXISTS("
                    "SELECT 1 FROM calendar_sync_connection c WHERE c.id=e.sync_connection_id "
                    "AND c.generation=e.sync_generation AND c.enabled AND c.files_root_url IS NOT NULL) AS active_connection "
                    "FROM calendar_event e WHERE e.id=ANY($1::integer[])",
                    list(ids),
                )
            by_id = {row["id"]: row for row in rows}
            for event_id in ids:
                row = by_id.get(event_id)
                if row is not None and row["sync_status"] == "synced" and row["active_connection"]:
                    confirmed.append(event_id)
                    if row["deleted_at"] is not None:
                        deleted += 1
                    else:
                        pushed += 1
                else:
                    pending.append(event_id)
                    code = (
                        row["sync_error_code"] if row is not None else "calendar_event_unavailable"
                    )
                    if row is not None and not row["active_connection"]:
                        code = "calendar_connection_unavailable"
                    if code:
                        errors.append({"event_id": event_id, "code": code})
        except Exception as error:
            pending = list(ids)
            confirmed = []
            pushed = deleted = 0
            code = (
                "calendar_sync_timeout"
                if isinstance(error, TimeoutError)
                else "calendar_database_unavailable"
            )
            errors = [{"event_id": event_id, "code": code} for event_id in ids]
        return CalendarSyncResult(
            not pending, tuple(confirmed), tuple(pending), tuple(errors), pushed, deleted
        )

    async def _sync_one(self, event_id: int, deadline: float) -> None:
        owner = uuid4()
        row = None
        try:
            async with asyncio.timeout_at(deadline), self._slots:
                row = await self._db.calendar_repo.claim_sync_event(event_id, owner)
                if row is None:
                    # Foreground may meet an in-flight background claim. Wait
                    # only within this save's deadline, never steal the lease.
                    while asyncio.get_running_loop().time() < deadline:
                        state = await self._db.calendar_repo.get_event(
                            event_id, include_deleted=True
                        )
                        if state is None or state["sync_status"] in ("synced", "error", "conflict"):
                            return
                        if state["sync_lease_until"] is None:
                            return
                        await asyncio.sleep(0.05)
                    return
                try:
                    password = decrypt_secret(bytes(row["credentials_encrypted"]))
                except (ValueError, RuntimeError):
                    raise DavError("calendar_credentials_unavailable")
                target = (
                    validate_dav_url(
                        row["target_calendar_url"], origin=row["caldav_base_url"]
                    ).rstrip("/")
                    + "/"
                )
                async with httpx.AsyncClient(
                    auth=httpx.BasicAuth(row["account_email"], password),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=4.0,
                ) as client:
                    if row["sync_status"] == "pending_delete":
                        await self._delete_remote(client, target, row)
                        href = etag = None
                    else:
                        href, etag = await self._push_remote(client, target, row)
                await self._db.calendar_repo.complete_sync_event(row, owner, href, etag)
        except (asyncio.CancelledError, TimeoutError):
            if row is not None:
                await self._release_failure(row, owner, "calendar_sync_timeout")
        except (httpx.TransportError, OSError):
            if row is not None:
                await self._release_failure(row, owner, "calendar_transport_failed")
        except DavError as error:
            if row is not None:
                await self._release_failure(
                    row, owner, error.code, blocked=error.code in BLOCKED_ERRORS
                )
        except Exception:
            if row is not None:
                await self._release_failure(row, owner, "calendar_sync_failed", blocked=True)
            logger.error("event %s code calendar_sync_failed", event_id)

    async def _release_failure(self, row, owner, code: str, *, blocked: bool = False) -> None:
        try:
            async with asyncio.timeout(0.15):
                await self._db.calendar_repo.fail_sync_event(row, owner, code, blocked=blocked)
        except Exception:
            # The 15-second database lease remains the recovery fence.
            pass
        logger.warning("event %s code %s", row["id"], code)

    async def _lookup_uid(self, client, target, row):
        query = ET.Element(f"{{{CALDAV}}}calendar-query")
        prop = ET.SubElement(query, f"{{{DAV}}}prop")
        ET.SubElement(prop, f"{{{DAV}}}getetag")
        ET.SubElement(prop, f"{{{CALDAV}}}calendar-data")
        filters = ET.SubElement(query, f"{{{CALDAV}}}filter")
        calendar_filter = ET.SubElement(filters, f"{{{CALDAV}}}comp-filter", {"name": "VCALENDAR"})
        event_filter = ET.SubElement(
            calendar_filter, f"{{{CALDAV}}}comp-filter", {"name": "VEVENT"}
        )
        uid_filter = ET.SubElement(event_filter, f"{{{CALDAV}}}prop-filter", {"name": "UID"})
        match = ET.SubElement(
            uid_filter, f"{{{CALDAV}}}text-match", {"collation": "i;octet", "match-type": "equals"}
        )
        match.text = row["ical_uid"]
        reply = await dav_request(
            client,
            "REPORT",
            target,
            content=ET.tostring(query, encoding="utf-8"),
            headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        )
        require_dav_success(reply)
        matches = []
        for response in parse_dav_xml(reply.body).findall(f"{{{DAV}}}response"):
            properties = successful_properties(response)
            data = properties.get(f"{{{CALDAV}}}calendar-data")
            if data is None or data.text is None:
                raise DavError("calendar_remote_response_invalid")
            payload = data.text.encode("utf-8")
            for event in _events(payload):
                if str(event.get("UID", "")) != row["ical_uid"]:
                    continue
                if str(event.get("X-CEA-EVENT-ID", "")) != str(row["id"]):
                    raise DavError("calendar_remote_conflict")
                href = resolve_dav_href(
                    target, response.findtext(f"{{{DAV}}}href", ""), collection=target
                )
                etag = properties.get(f"{{{DAV}}}getetag")
                matches.append((href, etag.text if etag is not None else None, _content(event)))
        if len(matches) > 1:
            raise DavError("calendar_remote_conflict")
        return matches[0] if matches else None

    async def _confirm_href(self, client, target, row, href, expected):
        href = validate_dav_url(href, origin=target, collection=target)
        reply = await dav_request(client, "GET", href)
        require_dav_success(reply)
        events = _events(reply.body)
        if len(events) != 1 or _content(events[0]) != expected:
            raise DavError("calendar_remote_conflict")
        return href, reply.headers.get("etag")

    async def _push_remote(self, client, target, row):
        payload = serialize_calendar_event(row)
        expected = _content(_events(payload)[0])
        href = target + quote(row["ical_uid"], safe="") + ".ics"
        known_href, known_etag = row.get("external_event_id"), row.get("external_etag")
        if known_href and known_etag:
            href = validate_dav_url(known_href, origin=target, collection=target)
            reply = await dav_request(
                client,
                "PUT",
                href,
                content=payload,
                headers={"If-Match": known_etag, "Content-Type": "text/calendar; charset=utf-8"},
            )
            if 200 <= reply.status < 300:
                return await self._confirm_href(client, target, row, href, expected)
            if reply.status not in (404, 409, 412):
                require_dav_success(reply)
        # Exact-UID reconciliation handles uncertain creates and stale hrefs.
        found = await self._lookup_uid(client, target, row)
        if found is not None:
            href, etag, content = found
            if content == expected:
                return href, etag
            if not row["sync_mirror_all"] or not etag:
                raise DavError("calendar_remote_conflict")
            headers = {"If-Match": etag, "Content-Type": "text/calendar; charset=utf-8"}
        else:
            headers = {"If-None-Match": "*", "Content-Type": "text/calendar; charset=utf-8"}
        try:
            reply = await dav_request(client, "PUT", href, content=payload, headers=headers)
        except httpx.TransportError:
            found = await self._lookup_uid(client, target, row)
            if found is not None and found[2] == expected:
                return found[0], found[1]
            raise
        if reply.status in (404, 409, 412):
            found = await self._lookup_uid(client, target, row)
            if found is not None and found[2] == expected:
                return found[0], found[1]
            raise DavError("calendar_remote_conflict")
        require_dav_success(reply)
        return await self._confirm_href(client, target, row, href, expected)

    async def _delete_remote(self, client, target, row) -> None:
        known = row.get("external_event_id") or target + quote(row["ical_uid"], safe="") + ".ics"
        known = validate_dav_url(known, origin=target, collection=target)
        reply = await dav_request(client, "GET", known)
        if reply.status != 404:
            require_dav_success(reply)
            events = _events(reply.body)
            if (
                len(events) != 1
                or str(events[0].get("UID", "")) != row["ical_uid"]
                or str(events[0].get("X-CEA-EVENT-ID", "")) != str(row["id"])
            ):
                raise DavError("calendar_remote_conflict")
        found = await self._lookup_uid(client, target, row)
        if found is None:
            # Both href and UID must be absent, not merely an empty binding.
            if known and reply.status != 404:
                raise DavError("calendar_remote_conflict")
            return
        href, etag, _ = found
        if not etag:
            raise DavError("calendar_remote_conflict")
        try:
            reply = await dav_request(client, "DELETE", href, headers={"If-Match": etag})
        except httpx.TransportError:
            reply = None
        if reply is not None and reply.status not in (200, 202, 204, 404, 409, 412):
            require_dav_success(reply)
        absence = await dav_request(client, "GET", href)
        if absence.status != 404:
            require_dav_success(absence)
            raise DavError("calendar_remote_conflict")
        if await self._lookup_uid(client, target, row) is not None:
            raise DavError("calendar_remote_conflict")
        if known != href:
            old_absence = await dav_request(client, "GET", known)
            if old_absence.status != 404:
                require_dav_success(old_absence)
                raise DavError("calendar_remote_conflict")
