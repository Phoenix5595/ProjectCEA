"""Climate periods API (ZoneConfig table + persistence)."""

from __future__ import annotations

from datetime import time as datetime_time
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

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
from app.repositories.climate_timeline_apply import _TIMELINE_APPLY_LOCK
from app.schemas.climate_periods import PeriodsSaveRequest
from app.services.climate_timeline_apply import SavedTimelineConfigurationInvalidator

router = APIRouter(prefix="/api/climate-periods", tags=["climate-periods"])

_PERIOD_EVENT_FIELDS = (
    "period_name",
    "start_time",
    "end_time",
    "ramp_minutes",
    "heating_setpoint",
    "cooling_setpoint",
    "vpd_setpoint",
    "co2_setpoint",
)


def _canonical_digest_value(value: Any) -> Any:
    """JSON-safe digest value; TIME columns read back as datetime.time."""
    if isinstance(value, datetime_time):
        return value.strftime("%H:%M")
    return value


def _periods_digest(periods: list[dict[str, Any]]) -> str:
    canonical_periods = [
        {field: _canonical_digest_value(period.get(field)) for field in _PERIOD_EVENT_FIELDS}
        for period in periods
    ]
    canonical = json.dumps(canonical_periods, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _affected_period_identities(
    previous: list[dict[str, Any]], request: PeriodsSaveRequest
) -> list[tuple[int, int | None]]:
    """Prior row identities plus the replacement identity for cache invalidation.

    Prior identities are captured before the scoped delete, so an identity
    whose rows all disappear is still invalidated even when no period remains.
    NULL-mode rows have no exact-profile cache key and are skipped.
    """
    identities: set[tuple[int, int | None]] = {
        (row["mode_id"], row.get("submode_id"))
        for row in previous
        if isinstance(row.get("mode_id"), int)
    }
    if request.mode_id is not None:
        identities.add((request.mode_id, request.submode_id))
    return sorted(identities, key=lambda pair: (pair[0], pair[1] if pair[1] is not None else -1))


def get_database() -> DatabaseManager:
    """Dependency to get database manager."""
    raise RuntimeError("Dependency not injected")


@router.get("/{location}/{cluster}")
async def get_climate_periods(
    location: str,
    cluster: str,
    mode_id: int | None = Query(
        None,
        description="When set, return only periods for this room mode (and submode if provided).",
    ),
    submode_id: int | None = Query(
        None,
        description="Flower submode id; omit for modes with no submode (matches NULL in DB).",
    ),
    database: DatabaseManager = Depends(get_database),
) -> list[dict[str, Any]]:
    """Get climate periods for a location/cluster.

    If ``mode_id`` is provided, rows are filtered to that mode and submode
    (``submode_id IS NOT DISTINCT FROM`` the query param, so NULL matches veg).
    If omitted, all rows for the room are returned (admin / legacy).
    """
    if mode_id is not None:
        periods = await database.climate_periods_repo.get_periods_for_room_mode(
            location, cluster, mode_id, submode_id
        )
    else:
        periods = await database.climate_periods_repo.get_periods(location, cluster)
    return [dict(p) for p in periods]


@router.post("/{location}/{cluster}")
@emits_operational_mutation
async def save_climate_periods(
    location: str,
    cluster: str,
    request: PeriodsSaveRequest,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Save climate periods for a location/cluster.

    The previous read, scoped delete, replacement inserts and the one
    configuration revision share one transaction under the timeline advisory
    lock. A missing or false delete/insert/version result aborts without
    leaving a partial replacement.
    """
    valid, errors = database.climate_periods_repo.validate_24h_coverage(
        [p.model_dump() for p in request.periods]
    )

    if not valid:
        raise HTTPException(status_code=400, detail={"errors": errors})

    try:
        pool = await database._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            _ = await connection.execute("SELECT pg_advisory_xact_lock($1)", _TIMELINE_APPLY_LOCK)
            previous = await database.climate_periods_repo.get_periods(
                location, cluster, request.mode_id, request.submode_id, conn=connection
            )

            deleted = await database.climate_periods_repo.delete_periods(
                location, cluster, request.mode_id, request.submode_id, conn=connection
            )
            if not deleted:
                raise RuntimeError("climate periods delete reported no persisted change")

            saved = []
            for p in request.periods:
                result = await database.climate_periods_repo.save_period(
                    location=location,
                    cluster=cluster,
                    period_name=p.period_name,
                    start_time=p.start_time,
                    end_time=p.end_time,
                    ramp_minutes=p.ramp_minutes,
                    heating_setpoint=p.heating_setpoint,
                    cooling_setpoint=p.cooling_setpoint,
                    vpd_setpoint=p.vpd_setpoint,
                    co2_setpoint=p.co2_setpoint,
                    details=p.details,
                    mode_id=request.mode_id,
                    submode_id=request.submode_id,
                    conn=connection,
                )
                if result:
                    saved.append(result)
                else:
                    raise RuntimeError("Failed to save climate period")

            version_id = await database.config_repo.log_config_version(
                config_type="climate_timeline",
                location=location,
                cluster=cluster,
                changes={
                    "periods_digest": _periods_digest([p.model_dump() for p in request.periods])
                },
                conn=connection,
            )
            if version_id is None:
                raise RuntimeError("failed to record a climate period revision")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database transaction failed: {str(e)}") from e

    affected_identities = _affected_period_identities(previous, request)
    warning = await SavedTimelineConfigurationInvalidator(
        database, affected_identities=affected_identities
    ).invalidate(location, cluster, f"{version_id:07x}", request.mode_id, request.submode_id)

    emit_persisted_mutation(
        sink,
        PersistedMutation(
            operation="update",
            entity=EntityContext(
                entity_type="climate_periods",
                entity_id=f"{location}:{cluster}:{request.mode_id}:{request.submode_id}",
                location=location,
                cluster=cluster,
            ),
            changes=safe_allowlisted_diff(
                before={"periods_digest": _periods_digest(previous)},
                after={
                    "periods_digest": _periods_digest([p.model_dump() for p in request.periods])
                },
                allowed_fields=frozenset({"periods_digest"}),
            ),
        ),
        context,
    )

    return {"saved": len(saved), "periods": saved, "notification_warning": warning}


@router.get("/{location}/{cluster}/validate")
async def validate_climate_periods(
    location: str,
    cluster: str,
    mode_id: int | None = Query(None),
    submode_id: int | None = Query(None),
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any]:
    """Validate 24h coverage for climate periods."""
    if mode_id is not None:
        periods = await database.climate_periods_repo.get_periods_for_room_mode(
            location, cluster, mode_id, submode_id
        )
    else:
        periods = await database.climate_periods_repo.get_periods(location, cluster)
    valid, errors = database.climate_periods_repo.validate_24h_coverage([dict(p) for p in periods])
    return {"valid": valid, "errors": errors, "period_count": len(periods)}


@router.get("/{location}/{cluster}/active")
async def get_active_period(
    location: str,
    cluster: str,
    time: str = "00:00",
    database: DatabaseManager = Depends(get_database),
) -> dict[str, Any] | None:
    """Get the active climate period at a given time."""
    period = await database.climate_periods_repo.get_active_period(location, cluster, time)
    return dict(period) if period else None


@router.delete("/{location}/{cluster}")
@emits_operational_mutation
async def delete_climate_periods(
    location: str,
    cluster: str,
    database: DatabaseManager = Depends(get_database),
    context: MutationRequestContext = Depends(get_mutation_request_context),
    sink: OperationalEventSink = Depends(get_mutation_event_sink),
) -> dict[str, Any]:
    """Delete all climate periods for a location/cluster."""
    previous = await database.climate_periods_repo.get_periods(location, cluster)
    success = await database.climate_periods_repo.delete_periods(location, cluster)
    if success:
        emit_persisted_mutation(
            sink,
            PersistedMutation(
                operation="delete",
                entity=EntityContext(
                    entity_type="climate_periods",
                    entity_id=f"{location}:{cluster}",
                    location=location,
                    cluster=cluster,
                ),
                changes=safe_allowlisted_diff(
                    before={"period_count": len(previous)},
                    after={"period_count": 0},
                    allowed_fields=frozenset({"period_count"}),
                ),
            ),
            context,
        )
    return {"deleted": success}
