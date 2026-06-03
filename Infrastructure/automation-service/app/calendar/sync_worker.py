"""Nextcloud CalDAV two-way sync for calendar_event rows."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from icalendar import Calendar as ICalendar
from icalendar import Event as IEvent

from app.calendar.credentials import decrypt_secret
from app.database import DatabaseManager
from shared.infra_logging import get_logger

logger = get_logger(__name__)


def _room_prefix(location: str, title: str) -> str:
    short = {"Flower Room": "Flower", "Veg Room": "Veg", "Lab": "Lab"}.get(location, location)
    if title.startswith(f"[{short}]"):
        return title
    return f"[{short}] {title}"


class CalendarSyncWorker:
    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    async def test_connection(
        self, caldav_base_url: str, username: str, app_password: str
    ) -> list[dict[str, str]]:
        import asyncio

        def _probe() -> list[dict[str, str]]:
            import caldav

            client = caldav.DAVClient(url=caldav_base_url, username=username, password=app_password)
            principal = client.principal()
            return [{"name": str(cal.name), "url": str(cal.url)} for cal in principal.calendars()]

        return await asyncio.to_thread(_probe)

    async def run_sync(self) -> dict[str, Any]:
        conn = await self._db.calendar_repo.get_sync_connection()
        if not conn:
            return {"ok": False, "error": "No sync connection configured"}

        cred_row = await self._db.pool.fetchrow(
            """
            SELECT credentials_encrypted, account_email, caldav_base_url, target_calendar_url
            FROM calendar_sync_connection WHERE id = $1
            """,
            conn["id"],
        )
        if not cred_row:
            return {"ok": False, "error": "Connection row missing"}

        password = decrypt_secret(bytes(cred_row["credentials_encrypted"]))
        username = cred_row["account_email"] or ""
        base_url = cred_row["caldav_base_url"]
        target_url = cred_row["target_calendar_url"]

        pushed = 0
        deleted = 0
        errors: list[str] = []
        pending = await self._db.calendar_repo.events_pending_sync()

        for row in pending:
            try:
                if row["sync_status"] == "pending_delete":
                    await self._delete_remote(base_url, username, password, target_url, row)
                    await self._db.pool.execute(
                        """
                        UPDATE calendar_event
                        SET sync_status = 'synced', last_synced_at = NOW()
                        WHERE id = $1
                        """,
                        row["id"],
                    )
                    deleted += 1
                else:
                    href, etag = await self._push_remote(
                        base_url, username, password, target_url, row
                    )
                    await self._db.calendar_repo.mark_event_synced(
                        row["id"], href, etag, target_url
                    )
                    pushed += 1
            except Exception as e:
                errors.append(f"event {row['id']}: {e}")
                logger.error("Calendar sync event %s failed: %s", row["id"], e)

        await self._db.calendar_repo.update_sync_state(
            conn["id"],
            last_error="; ".join(errors[:3]) if errors else None,
        )
        return {"ok": len(errors) == 0, "pushed": pushed, "deleted": deleted, "errors": errors}

    async def _push_remote(
        self,
        base_url: str,
        username: str,
        password: str,
        target_url: str,
        row: dict[str, Any],
    ) -> tuple[str, str | None]:
        import asyncio

        def _do() -> tuple[str, str | None]:
            import caldav

            client = caldav.DAVClient(url=base_url, username=username, password=password)
            cal = client.calendar(url=target_url)
            uid = row["ical_uid"]
            title = _room_prefix(row["location"], row["title"])
            start: date = row["start_date"]
            end: date = row["end_date"] or row["start_date"]
            # CalDAV all-day: DTEND is exclusive
            dtend = end + timedelta(days=1)

            cal_event = IEvent()
            cal_event.add("summary", title)
            cal_event.add("uid", uid)
            cal_event.add("dtstart", start)
            cal_event.add("dtend", dtend)
            cal_event.add("X-CEA-EVENT-ID", str(row["id"]))
            ical = ICalendar()
            ical.add_component(cal_event)
            ical_bytes = ical.to_ical()

            if row.get("external_event_id"):
                ev = cal.event_by_url(row["external_event_id"])
                ev.data = ical_bytes
                ev.save()
                return str(ev.url), str(getattr(ev, "etag", "") or "")
            ev = cal.add_event(ical_bytes)
            return str(ev.url), str(getattr(ev, "etag", "") or "")

        return await asyncio.to_thread(_do)

    async def _delete_remote(
        self,
        base_url: str,
        username: str,
        password: str,
        target_url: str,
        row: dict[str, Any],
    ) -> None:
        if not row.get("external_event_id"):
            return
        import asyncio

        def _do() -> None:
            import caldav

            client = caldav.DAVClient(url=base_url, username=username, password=password)
            cal = client.calendar(url=target_url)
            ev = cal.event_by_url(row["external_event_id"])
            ev.delete()

        await asyncio.to_thread(_do)
