"""Apply Flower Room mode/submode from active calendar phases."""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

from app.database import DatabaseManager
from app.services.mode_transition_service import ModeTransitionService
from shared.infra_logging import get_logger

logger = get_logger(__name__)
LOCAL_TZ = ZoneInfo("America/Toronto")


class CalendarModeScheduler:
    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._transition = ModeTransitionService(db)

    def _today(self) -> date:
        from datetime import datetime

        return datetime.now(tz=LOCAL_TZ).date()

    async def run_catchup(self) -> None:
        await self._apply_for_date(self._today(), triggered_by="calendar_scheduler_catchup")

    async def run_tick(self) -> None:
        await self._apply_for_date(self._today(), triggered_by="calendar_scheduler")

    async def _apply_for_date(self, on_date: date, triggered_by: str) -> None:
        repo = self._db.calendar_repo
        event = await repo.get_active_flower_phase_event(on_date)
        if event:
            if await repo.mode_application_exists(event["id"], on_date):
                return
            meta = self._db.calendar_repo._parse_metadata(event.get("metadata"))
            if meta.get("auto_mode_transition") is False:
                return
            mode_name = meta.get("target_mode_name")
            if not mode_name:
                return
            submode_name = meta.get("target_submode_name")
            await self._set_mode(
                mode_name,
                submode_name,
                event["id"],
                on_date,
                triggered_by,
            )
            return

        last_end = await repo.get_last_ended_plan_end("Flower Room")
        if last_end and on_date > last_end:
            active = await self._db.room_mode_repo.get_active_mode("Flower Room", "main")
            if active and active.get("mode_name") == "drying":
                await self._set_mode("veg", None, None, on_date, "calendar_scheduler_idle")

    async def _set_mode(
        self,
        mode_name: str,
        submode_name: str | None,
        event_id: int | None,
        on_date: date,
        triggered_by: str,
    ) -> None:
        mode_row = await self._db.room_mode_repo.get_room_mode_by_name(mode_name)
        if not mode_row:
            logger.warning("Calendar scheduler: unknown mode %s", mode_name)
            return
        mode_id = mode_row["id"]
        submode_id = None
        if submode_name:
            submodes = await self._db.room_mode_repo.get_flower_submodes()
            sm = next((s for s in submodes if s["name"] == submode_name), None)
            submode_id = sm["id"] if sm else None

        active = await self._db.room_mode_repo.get_active_mode("Flower Room", "main")
        if active and active.get("mode_id") == mode_id and active.get("submode_id") == submode_id:
            if event_id:
                await self._db.calendar_repo.record_mode_application(
                    event_id, on_date, mode_id, submode_id, triggered_by
                )
            return

        try:
            # mode_transition_history.triggered_by CHECK allows api|schedule|system only
            await self._transition.execute_mode_transition(
                "Flower Room",
                "main",
                mode_id,
                submode_id,
                "system",
            )
            if event_id:
                await self._db.calendar_repo.record_mode_application(
                    event_id, on_date, mode_id, submode_id, triggered_by
                )
            logger.info(
                "Calendar mode applied: Flower Room -> %s/%s (%s)",
                mode_name,
                submode_name,
                triggered_by,
            )
        except Exception as e:
            logger.error(
                "Calendar mode transition failed: %s",
                e,
                extra={"mode_name": mode_name, "submode_name": submode_name},
            )

    async def get_expected_mode(self, location: str, cluster: str, on_date: date) -> dict[str, Any]:
        if location != "Flower Room":
            return {"mode_name": None, "submode_name": None}
        event = await self._db.calendar_repo.get_active_flower_phase_event(on_date)
        if not event:
            return {"mode_name": None, "submode_name": None}
        meta = event.get("metadata") or {}
        if isinstance(meta, str):
            import json

            meta = json.loads(meta)
        return {
            "mode_name": meta.get("target_mode_name"),
            "submode_name": meta.get("target_submode_name"),
            "event_type": event.get("event_type"),
            "title": event.get("title"),
        }
