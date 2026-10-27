"""Helpers shared by the core and sync calendar endpoints: CalendarMutationError to HTTP mapping and mutation-event emission."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.events.mutation_context import (
    MutationOperation,
    MutationRequestContext,
    PersistedMutation,
    emit_persisted_mutation,
)
from app.events.mutation_diff import safe_allowlisted_diff
from app.events.operational_models import EntityContext, JsonValue
from app.events.operational_ports import OperationalEventSink
from app.repositories.calendar_errors import CalendarMutationError


def _raise_calendar_error(error: CalendarMutationError) -> None:
    detail: dict[str, Any] = {"code": error.code}
    if error.detail is not None:
        detail["detail"] = error.detail
    raise HTTPException(status_code=error.status_code, detail=detail)


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
