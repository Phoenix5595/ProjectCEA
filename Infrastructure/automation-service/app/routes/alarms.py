"""Alarm management endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from app.alarm_manager import AlarmManager
from app.database import DatabaseManager
from app.events.mutation_context import MutationRequestContext
from app.events.mutation_coverage import emits_operational_mutation
from app.events.mutation_dependencies import get_mutation_event_sink, get_mutation_request_context
from app.events.operational_models import (
    AlarmPayload,
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
)
from app.events.operational_ports import OperationalEventSink
from app.schemas.alarms import (
    ActiveAlarmResponse,
    AlarmAcknowledgeResponse,
    AlarmListResponse,
)

router = APIRouter()
_SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}


def _utc_datetime(value: object, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            return fallback
    return fallback


def _alarm_response(alarm: dict[str, Any], now: datetime) -> ActiveAlarmResponse:
    return ActiveAlarmResponse(
        location=str(alarm.get("location", "")),
        cluster=str(alarm.get("cluster", "")),
        alarm_name=str(alarm.get("alarm_name", "")),
        severity=str(alarm.get("severity", "info")).lower(),
        message=str(alarm.get("message", "")),
        active=bool(alarm.get("active", False)),
        acknowledged=bool(alarm.get("acknowledged", False)),
        opened_at=_utc_datetime(alarm.get("since", alarm.get("opened_at")), now),
        acknowledged_at=(
            _utc_datetime(alarm["acknowledged_at"], now)
            if alarm.get("acknowledged_at") is not None
            else None
        ),
        acknowledged_by=str(alarm["acknowledged_by"]) if alarm.get("acknowledged_by") else None,
    )


def _sort_alarms(alarms: list[ActiveAlarmResponse]) -> list[ActiveAlarmResponse]:
    return sorted(
        alarms,
        key=lambda alarm: (
            _SEVERITY_RANK.get(alarm.severity, 3),
            alarm.acknowledged,
            alarm.opened_at,
        ),
    )


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    from app.main import container

    return container.get_database()


def get_alarm_manager() -> AlarmManager | None:
    """Get alarm manager."""
    from app.main import container

    return container.get_alarm_manager()


@router.get("/api/alarms/{location}/{cluster}", response_model=AlarmListResponse)
async def get_alarms(
    location: str, cluster: str, alarm_manager: AlarmManager | None = Depends(get_alarm_manager)
) -> AlarmListResponse:
    """Return active alarms for one location/cluster in operator order."""
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")

    now = datetime.now(UTC)
    records = alarm_manager.get_alarms(location, cluster).values()
    alarms = _sort_alarms([_alarm_response(record, now) for record in records])
    return AlarmListResponse(generated_at=now, alarms=tuple(alarms))


@router.get("/api/alarms", response_model=AlarmListResponse)
async def get_all_alarms(
    alarm_manager: AlarmManager | None = Depends(get_alarm_manager),
) -> AlarmListResponse:
    """Return all active durable alarms in operator order."""
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")

    now = datetime.now(UTC)
    records = alarm_manager.get_alarms().values()
    alarms = _sort_alarms([_alarm_response(record, now) for record in records])
    return AlarmListResponse(generated_at=now, alarms=tuple(alarms))


@router.post(
    "/api/alarms/{location}/{cluster}/{alarm_name}/acknowledge",
    response_model=AlarmAcknowledgeResponse,
)
@emits_operational_mutation
async def acknowledge_alarm(
    location: str,
    cluster: str,
    alarm_name: str,
    context: Annotated[MutationRequestContext, Depends(get_mutation_request_context)],
    sink: Annotated[OperationalEventSink, Depends(get_mutation_event_sink)],
    alarm_manager: AlarmManager | None = Depends(get_alarm_manager),
) -> AlarmAcknowledgeResponse:
    """Acknowledge an alarm.

    Args:
        location: Location name
        cluster: Cluster name
        alarm_name: Alarm identifier

    Returns:
        Success status
    """
    if not alarm_manager:
        raise HTTPException(status_code=503, detail="Alarm manager not available")

    success = alarm_manager.acknowledge_alarm(location, cluster, alarm_name)

    if not success:
        raise HTTPException(status_code=404, detail="Alarm not found")

    sink.emit_nowait(
        OperationalEvent(
            occurred_at=datetime.now(UTC),
            source=EventSource.API,
            category=EventCategory.ALARM,
            severity=EventSeverity.INFO,
            event_type="alarm.acknowledged",
            correlation_id=context.correlation_id,
            entity=EntityContext(
                entity_type="alarm",
                entity_id=alarm_name,
                location=location,
                cluster=cluster,
            ),
            actor=context.actor,
            payload=AlarmPayload(alarm_code=alarm_name, state="acknowledged"),
        )
    )

    return AlarmAcknowledgeResponse(
        location=location,
        cluster=cluster,
        alarm_name=alarm_name,
        acknowledged=True,
        success=True,
    )
