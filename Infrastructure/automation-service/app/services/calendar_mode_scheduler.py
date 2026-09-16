"""Apply Flower Room mode/submode from active calendar phases."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, NotRequired, TypedDict
from zoneinfo import ZoneInfo

from app.database import DatabaseManager
from app.events.operational_models import (
    ActorContext,
    ActorType,
    EntityContext,
    EventCategory,
    EventDetail,
    EventSeverity,
    EventSource,
    OperationalEvent,
    SystemPayload,
)
from app.events.operational_ports import OperationalEventSink
from app.services.mode_transition_service import ModeTransitionService
from shared.infra_logging import get_logger

from ..repositories.calendar import CalendarRepository

logger = get_logger(__name__)
LOCAL_TZ = ZoneInfo("America/Toronto")


class CalendarTransition(TypedDict):
    event_id: int | None
    event_type: str | None
    title: str | None
    auto_mode_transition: bool
    calendar_mode_transitions_enabled: bool
    target_mode_name: str | None
    target_submode_name: str | None
    target_mode_id: NotRequired[int]
    target_submode_id: NotRequired[int | None]
    resolution_reason: NotRequired[str]


class CalendarModeScheduler:
    def __init__(self, db: DatabaseManager, event_sink: OperationalEventSink | None = None) -> None:
        self._db = db
        self._transition = ModeTransitionService(db)
        self._event_sink = event_sink
        self._skipped_transitions: set[tuple[int, date]] = set()

    def _today(self) -> date:
        from datetime import datetime

        return datetime.now(tz=LOCAL_TZ).date()

    async def run_catchup(self) -> None:
        await self._apply_for_date(self._today(), triggered_by="calendar_scheduler_catchup")

    async def run_tick(self) -> None:
        await self._apply_for_date(self._today(), triggered_by="calendar_scheduler")

    async def _apply_for_date(self, on_date: date, triggered_by: str) -> None:
        repo = self._db.calendar_repo
        if not await repo.flower_calendar_mode_transitions_enabled():
            return
        event = await repo.get_active_flower_phase_event(on_date)
        if event:
            if await repo.mode_application_exists(event["id"], on_date):
                return
            meta = self._db.calendar_repo._parse_metadata(event.get("metadata"))
            if meta.get("auto_mode_transition") is False:
                return
            mode_name = meta.get("target_mode_name")
            if not mode_name:
                self._emit_transition_skipped(
                    event["id"], on_date, "missing", None, "missing_target_mode"
                )
                return
            submode_name = meta.get("target_submode_name")
            if submode_name is not None and not isinstance(submode_name, str):
                self._emit_transition_skipped(
                    event["id"], on_date, mode_name, None, "unknown_submode"
                )
                return
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
            self._emit_transition_skipped(
                event_id, on_date, mode_name, submode_name, "unknown_mode"
            )
            return
        mode_id = mode_row["id"]
        submode_id = None
        if submode_name:
            submodes = await self._db.room_mode_repo.get_flower_submodes()
            sm = next((s for s in submodes if s["name"] == submode_name), None)
            if sm is None:
                logger.warning("Calendar scheduler: unknown submode %s", submode_name)
                self._emit_transition_skipped(
                    event_id, on_date, mode_name, submode_name, "unknown_submode"
                )
                return
            submode_id = sm["id"]

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

    async def get_expected_transition(
        self, location: str, cluster: str, on_date: date
    ) -> CalendarTransition | None:
        del cluster
        if location != "Flower Room":
            return None
        if not await self._db.calendar_repo.flower_calendar_mode_transitions_enabled():
            return None
        event = await self._db.calendar_repo.get_active_flower_phase_event(on_date)
        if not event:
            return None
        calendar_repo: CalendarRepository = self._db.calendar_repo
        meta = calendar_repo.parse_metadata(event.get("metadata"))
        if meta.get("auto_mode_transition") is False:
            return None
        mode_name, submode_name = meta.get("target_mode_name"), meta.get("target_submode_name")
        transition: CalendarTransition = {
            "event_id": event.get("id"),
            "event_type": event.get("event_type"),
            "title": event.get("title"),
            "auto_mode_transition": True,
            "calendar_mode_transitions_enabled": True,
            "target_mode_name": mode_name,
            "target_submode_name": submode_name,
        }
        if not isinstance(mode_name, str) or not mode_name:
            return {**transition, "resolution_reason": "missing_target_mode"}
        mode_row = await self._db.room_mode_repo.get_room_mode_by_name(mode_name)
        if mode_row is None or not isinstance(mode_row.get("id"), int):
            return {**transition, "resolution_reason": "unknown_mode"}
        if submode_name is None:
            return {**transition, "target_mode_id": mode_row["id"], "target_submode_id": None}
        if not isinstance(submode_name, str) or not submode_name:
            return {**transition, "resolution_reason": "unknown_submode"}
        submodes = await self._db.room_mode_repo.get_flower_submodes()
        submode = next((row for row in submodes if row["name"] == submode_name), None)
        if submode is None or not isinstance(submode.get("id"), int):
            return {**transition, "resolution_reason": "unknown_submode"}
        return {
            **transition,
            "target_mode_id": mode_row["id"],
            "target_submode_id": submode["id"],
        }

    def _emit_transition_skipped(
        self,
        event_id: int | None,
        on_date: date,
        mode_name: str,
        submode_name: str | None,
        reason: str,
    ) -> None:
        if event_id is None or self._event_sink is None:
            return
        key = (event_id, on_date)
        if key in self._skipped_transitions:
            return
        self._skipped_transitions.add(key)
        try:
            self._event_sink.emit_nowait(
                OperationalEvent(
                    occurred_at=datetime.now(UTC),
                    source=EventSource.AUTOMATION,
                    category=EventCategory.SYSTEM,
                    severity=EventSeverity.WARNING,
                    event_type="calendar.transition_skipped",
                    entity=EntityContext(
                        entity_type="calendar_event",
                        entity_id=str(event_id),
                        location="Flower Room",
                        cluster="main",
                    ),
                    actor=ActorContext(
                        actor_type=ActorType.SERVICE, actor_id="calendar_mode_scheduler"
                    ),
                    reason_code=reason,
                    reason_text=f"Calendar destination {mode_name}/{submode_name or 'default'} was skipped",
                    payload=SystemPayload(
                        component="calendar_mode_scheduler",
                        state="transition_skipped",
                        detail=reason,
                        details=(
                            EventDetail(key="phase", value=str(event_id)),
                            EventDetail(
                                key="destination",
                                value=f"{mode_name}/{submode_name or 'default'}",
                            ),
                            EventDetail(key="resolution_reason", value=reason),
                        ),
                    ),
                )
            )
        except Exception:  # noqa: BLE001
            logger.warning("Calendar scheduler: unable to emit skipped transition event")

    async def get_expected_mode(self, location: str, cluster: str, on_date: date) -> dict[str, Any]:
        if location != "Flower Room":
            return {"mode_name": None, "submode_name": None}
        if not await self._db.calendar_repo.flower_calendar_mode_transitions_enabled():
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
