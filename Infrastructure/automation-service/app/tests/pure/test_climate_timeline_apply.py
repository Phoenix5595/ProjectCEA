"""Apply repository, service, route and invalidator behavior on isolated fakes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import time
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.events import ConfigChangeEvent, ConfigEventType
from app.repositories.climate_timeline_apply import (
    TimelineApplyCommit,
    TimelineApplyRepository,
    TimelineApplyStaleRevisionError,
)
from app.repositories.room_modes import InvalidProfileIdentityError, ProfileNotFoundError
from app.routes import climate_timeline as timeline_routes
from app.routes.climate_timeline import get_apply_service, router
from app.schemas.climate_timeline import TimelineApplyRequest, TimelineApplyResponse
from app.services import climate_timeline_apply as apply_module
from app.services.climate_timeline_apply import (
    ClimateTimelineApplyService,
    SavedTimelineConfigurationInvalidator,
)
from shared.auth import APIKeyAuthMiddleware
from shared.redis_keys import climate_period_cache_key

_LOCATION = "Veg Room"
_CLUSTER = "main"
_TIMELINE_LOCK = 7_281_992

_STORED_DAY = time(7, 0)
_STORED_NIGHT = time(19, 0)
_APPLIED_DAY = time(6, 0)
_APPLIED_NIGHT = time(18, 0)

# The exact schedule-cache clears an affected active save performs.
_SCHEDULE_KEYS = (
    f"schedules:loc:{_LOCATION}:cluster:{_CLUSTER}",
    f"schedules:loc:{_LOCATION}:cluster:{_CLUSTER}:climate",
    f"schedule:{_LOCATION}:{_CLUSTER}",
    "schedules:all",
)


def _payload(
    *,
    expected_config_revision: str = "0000007",
    mode_id: int = 1,
    submode_id: int | None = None,
    photoperiod: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "request_id": "apply-17",
        "expected_config_revision": expected_config_revision,
        "draft_revision": 4,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "periods": [
            {
                "id": "draft-day",
                "period_name": "Draft Day",
                "start_time": "00:00",
                "end_time": "00:00",
                "ramp_minutes": 0,
                "heating_setpoint": 24.0,
                "cooling_setpoint": None,
                "vpd_setpoint": None,
                "co2_setpoint": None,
                "details": "",
            }
        ],
        "photoperiod": photoperiod
        or {
            "day_start_time": "06:00",
            "night_start_time": "18:00",
            "ramp_up_minutes": 10,
            "ramp_down_minutes": 10,
        },
    }


def _unchanged_photoperiod() -> dict[str, object]:
    """The photoperiod that already matches the stored fixture row."""
    return {
        "day_start_time": _STORED_DAY.strftime("%H:%M"),
        "night_start_time": _STORED_NIGHT.strftime("%H:%M"),
        "ramp_up_minutes": 15,
        "ramp_down_minutes": 15,
    }


@dataclass
class _State:
    revision: int = 7
    active: tuple[int, int | None] | None = (1, None)
    periods: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "period_name": "Saved Day",
                "heating_setpoint": 20.0,
                "mode_id": 1,
                "submode_id": None,
            }
        ]
    )
    # One exact (mode_id, submode_id) parameter row per prepared profile with
    # the deprecated intensity columns the timeline aggregate must never
    # rewrite.
    mode_parameters: dict[tuple[int, int | None], dict[str, object]] = field(
        default_factory=lambda: {
            (1, None): {
                "day_start_time": _STORED_DAY,
                "night_start_time": _STORED_NIGHT,
                "light_ramp_up_minutes": 15,
                "light_ramp_down_minutes": 15,
                "main_light_intensity": 72.0,
                "supplemental_light_intensity": 0.0,
            }
        }
    )
    fail_period_insert: bool = False
    trace: list[str] = field(default_factory=list)
    issued: list[tuple[str, tuple[object, ...]]] = field(default_factory=list)


def _query_tag(query: str) -> str:
    if "pg_advisory_xact_lock" in query:
        return "lock"
    if "MAX(version_id)" in query:
        return "read_revision"
    if "FROM room_active_mode" in query:
        return "lock_active"
    if "SELECT * FROM mode_parameters" in query:
        return "read_prior_parameters"
    if "UPDATE mode_parameters" in query:
        return "update_parameter"
    if "INSERT INTO mode_parameters" in query:
        return "insert_parameter"
    if "DELETE FROM climate_periods" in query:
        return "delete_periods"
    if "INSERT INTO climate_periods" in query:
        return "insert_period"
    if "INSERT INTO config_versions" in query:
        return "insert_version"
    raise AssertionError(f"unexpected query: {query}")


class _Connection:
    def __init__(self, state: _State) -> None:
        self._state = state
        self._staged: _State | None = None

    @asynccontextmanager
    async def transaction(self):
        self._state.trace.append("transaction_enter")
        self._staged = deepcopy(self._state)
        try:
            yield
        except Exception:
            self._state.trace.append("rollback")
            raise
        else:
            self._state.active = self._staged.active
            self._state.revision = self._staged.revision
            self._state.periods = self._staged.periods
            self._state.mode_parameters = self._staged.mode_parameters
            self._state.trace.append("commit")

    def _issue(self, query: str, args: tuple[object, ...]) -> str:
        assert self._staged is not None
        tag = _query_tag(query)
        self._state.issued.append((tag, args))
        return tag

    async def execute(self, query: str, *args: object) -> str:
        tag = self._issue(query, args)
        if tag == "lock":
            assert args == (_TIMELINE_LOCK,)
            self._state.trace.append("lock")
            return "SELECT 1"
        if tag == "insert_parameter":
            self._state.trace.append("stage_parameter_insert")
            self._staged.mode_parameters[(args[2], args[3])] = {
                "day_start_time": args[4],
                "night_start_time": args[5],
                "light_ramp_up_minutes": args[6],
                "light_ramp_down_minutes": args[7],
                "main_light_intensity": 100,
                "supplemental_light_intensity": 0,
            }
            return "INSERT 0 1"
        if tag == "delete_periods":
            self._state.trace.append("delete_periods")
            mode_id, submode_id = args[2], args[3]
            self._staged.periods = [
                row
                for row in self._staged.periods
                if not (
                    row.get("mode_id") == mode_id and row.get("submode_id") == submode_id
                )
            ]
            return "DELETE 1"
        if tag == "insert_period":
            self._state.trace.append("stage_period")
            if self._state.fail_period_insert:
                raise RuntimeError("injected period insert failure")
            self._staged.periods.append(
                {
                    "period_name": args[4],
                    "heating_setpoint": args[8],
                    "mode_id": args[2],
                    "submode_id": args[3],
                }
            )
            return "INSERT 0 1"
        raise AssertionError(query)

    async def fetchval(self, query: str, *args: object) -> int | None:
        tag = self._issue(query, args)
        if tag == "read_revision":
            self._state.trace.append("read_revision")
            return self._staged.revision
        if tag == "update_parameter":
            row = self._staged.mode_parameters.get((args[6], args[7]))
            if row is None:
                return None
            self._state.trace.append("stage_photoperiod")
            row.update(
                {
                    "day_start_time": args[0],
                    "night_start_time": args[1],
                    "light_ramp_up_minutes": args[2],
                    "light_ramp_down_minutes": args[3],
                }
            )
            return 1
        raise AssertionError(query)

    async def fetchrow(self, query: str, *args: object) -> dict[str, object] | None:
        tag = self._issue(query, args)
        if tag == "insert_version":
            self._state.trace.append("increment_revision")
            self._staged.revision += 1
            return {"version_id": self._staged.revision}
        if tag == "lock_active":
            self._state.trace.append("lock_active")
            if self._state.active is None:
                return None
            return {"mode_id": self._state.active[0], "submode_id": self._state.active[1]}
        if tag == "read_prior_parameters":
            self._state.trace.append("read_prior_parameters")
            row = self._staged.mode_parameters.get((args[2], args[3]))
            return dict(row) if row is not None else None
        raise AssertionError(query)


class _Pool:
    def __init__(self, state: _State) -> None:
        self.connection = _Connection(state)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


class _IdentitySource:
    """The exact profile identity validator; raises the typed repo errors."""

    def __init__(self, state: _State, error: Exception | None = None) -> None:
        self._state = state
        self._error = error
        self.calls: list[tuple[str, int, int | None]] = []

    async def get_profile_identity_on_connection(
        self, _conn: object, location: str, mode_id: int, submode_id: int | None
    ) -> dict[str, object]:
        self.calls.append((location, mode_id, submode_id))
        if self._error is not None:
            raise self._error
        self._state.trace.append("valid_identity")
        return {"mode_id": mode_id, "submode_id": submode_id}

    async def get_active_mode(self, location: str, cluster: str) -> dict[str, object] | None:
        """Post-commit active authority support for the invalidator only."""
        assert (location, cluster) == (_LOCATION, _CLUSTER)
        if self._state.active is None:
            return None
        return {
            "location": location,
            "cluster": cluster,
            "mode_id": self._state.active[0],
            "submode_id": self._state.active[1],
        }


class _ScheduleService:
    """RoomScheduleService.sync_on_connection behavioral fake."""

    def __init__(self, state: _State, error: Exception | None = None) -> None:
        self._state = state
        self._error = error
        self.calls: list[dict[str, object]] = []

    async def sync_on_connection(
        self,
        connection: object,
        location: str,
        cluster: str,
        mode_id: int,
        submode_id: int | None,
        *,
        parameters: Mapping[str, object] | None = None,
    ) -> dict[str, int]:
        if self._error is not None:
            raise self._error
        assert parameters is not None
        self.calls.append(
            {
                "connection": connection,
                "location": location,
                "cluster": cluster,
                "mode_id": mode_id,
                "submode_id": submode_id,
                "parameters": dict(parameters),
            }
        )
        self._state.trace.append("sync_schedules")
        return {"schedules_created": 4, "devices_configured": 3}


class _Invalidator:
    def __init__(self, state: _State) -> None:
        self._state = state
        self.calls: list[tuple[str, str, str, int | None, int | None]] = []

    async def invalidate(
        self,
        location: str,
        cluster: str,
        revision: str,
        mode_id: int | None,
        submode_id: int | None,
    ) -> str | None:
        self._state.trace.append("invalidate")
        self.calls.append((location, cluster, revision, mode_id, submode_id))


def _repository(
    state: _State,
    identity: _IdentitySource | None = None,
    schedule: _ScheduleService | None = None,
) -> tuple[TimelineApplyRepository, _IdentitySource, _ScheduleService]:
    identity = identity or _IdentitySource(state)
    schedule = schedule or _ScheduleService(state)
    repository = TimelineApplyRepository(_Pool(state), identity, schedule)
    return repository, identity, schedule


def _service(
    state: _State,
    identity: _IdentitySource | None = None,
    schedule: _ScheduleService | None = None,
) -> tuple[ClimateTimelineApplyService, _Invalidator, _ScheduleService]:
    repository, identity, schedule = _repository(state, identity, schedule)
    invalidator = _Invalidator(state)
    return ClimateTimelineApplyService(repository, invalidator), invalidator, schedule


def _app(service: ClimateTimelineApplyService) -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_apply_service] = lambda: service
    return app


@pytest.mark.asyncio
async def test_apply_returns_409_with_draft_identity_when_revision_is_stale() -> None:
    # Given: an Apply draft based on revision seven while saved authority is revision eight.
    state = _State(revision=8)
    service, invalidator, schedule = _service(state)
    app = _app(service)

    # When: the reviewed draft reaches the authenticated Apply route.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/climate-timeline/Veg%20Room/main/apply", json=_payload())

    # Then: the draft identity remains available for the client to retain and re-review.
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "stale_timeline_revision",
        "request_id": "apply-17",
        "expected_config_revision": "0000007",
        "draft_revision": 4,
    }
    assert schedule.calls == []
    assert state.trace == ["transaction_enter", "lock", "read_revision", "rollback"]
    assert invalidator.calls == []


@pytest.mark.asyncio
async def test_apply_route_returns_the_committed_saved_baseline() -> None:
    # Given: a valid reviewed aggregate and its transaction-backed Apply service.
    state = _State()
    service, invalidator, schedule = _service(state)
    app = _app(service)

    # When: the authenticated client applies the reviewed timeline over HTTP.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/climate-timeline/Veg%20Room/main/apply", json=_payload())

    # Then: the route returns the new saved revision after commit and invalidation.
    assert response.status_code == 200
    body = TimelineApplyResponse.model_validate_json(response.content, strict=False)
    assert (body.request_id, body.config_revision) == ("apply-17", "0000008")
    assert body.parameters_configured is True
    assert body.notification_warning is None
    assert state.trace[-2:] == ["commit", "invalidate"]
    assert len(schedule.calls) == 1
    assert invalidator.calls == [(_LOCATION, _CLUSTER, "0000008", 1, None)]


@pytest.mark.asyncio
async def test_apply_rolls_back_timeline_aggregate_when_period_write_fails() -> None:
    # Given: a persisted aggregate and an injected failure after the replacement begins.
    state = _State(fail_period_insert=True)
    service, invalidator, schedule = _service(state)
    before = deepcopy((state.revision, state.periods, state.mode_parameters))
    request = TimelineApplyRequest.model_validate(_payload())

    # When: the period insert raises from inside the one transaction.
    with pytest.raises(RuntimeError, match="injected period insert failure"):
        await service.apply("Veg Room", "main", request)

    # Then: no timeline field, revision, parameter row, or invalidation escapes the rollback.
    assert (state.revision, state.periods, state.mode_parameters) == before
    assert schedule.calls == []
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "valid_identity",
        "lock_active",
        "read_prior_parameters",
        "stage_photoperiod",
        "delete_periods",
        "stage_period",
        "rollback",
    ]
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "read_prior_parameters",
        "update_parameter",
        "delete_periods",
        "insert_period",
    ]
    assert invalidator.calls == []


@pytest.mark.asyncio
async def test_apply_rejects_invalid_review_before_opening_a_transaction() -> None:
    # Given: a reviewed aggregate containing overlapping half-open periods.
    state = _State()
    service, invalidator, _ = _service(state)
    invalid = _payload()
    invalid["periods"] = [
        {
            "id": "draft-day",
            "period_name": "Draft Day",
            "start_time": "00:00",
            "end_time": "13:00",
            "ramp_minutes": 0,
            "heating_setpoint": 24.0,
            "cooling_setpoint": None,
            "vpd_setpoint": None,
            "co2_setpoint": None,
            "details": "",
        },
        {
            "id": "draft-night",
            "period_name": "Draft Night",
            "start_time": "12:00",
            "end_time": "00:00",
            "ramp_minutes": 0,
            "heating_setpoint": 18.0,
            "cooling_setpoint": None,
            "vpd_setpoint": None,
            "co2_setpoint": None,
            "details": "",
        },
    ]
    request = TimelineApplyRequest.model_validate(invalid)

    # When: Apply receives the invalid reviewed aggregate.
    with pytest.raises(ValueError, match="Overlap"):
        await service.apply("Veg Room", "main", request)

    # Then: validation rejects it before any transaction or post-commit effect begins.
    assert state.trace == []
    assert invalidator.calls == []


@pytest.mark.asyncio
async def test_apply_increments_once_and_invalidates_only_after_commit() -> None:
    # Given: one valid reviewed aggregate whose active profile photoperiod changes.
    state = _State()
    service, invalidator, schedule = _service(state)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: Apply persists the timeline-owned aggregate.
    response = await service.apply("Veg Room", "main", request)

    # Then: one committed revision is returned and publication follows, never precedes, commit.
    assert isinstance(response, TimelineApplyResponse)
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert state.revision == 8
    assert state.periods == [
        {
            "period_name": "Draft Day",
            "heating_setpoint": 24.0,
            "mode_id": 1,
            "submode_id": None,
        }
    ]
    assert state.mode_parameters[(1, None)] == {
        "day_start_time": _APPLIED_DAY,
        "night_start_time": _APPLIED_NIGHT,
        "light_ramp_up_minutes": 10,
        "light_ramp_down_minutes": 10,
        "main_light_intensity": 72.0,
        "supplemental_light_intensity": 0.0,
    }
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "valid_identity",
        "lock_active",
        "read_prior_parameters",
        "stage_photoperiod",
        "delete_periods",
        "stage_period",
        "increment_revision",
        "sync_schedules",
        "commit",
        "invalidate",
    ]
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "read_prior_parameters",
        "update_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    assert invalidator.calls == [(_LOCATION, _CLUSTER, "0000008", 1, None)]


@pytest.mark.asyncio
async def test_apply_regenerates_active_schedules_on_the_supplied_connection() -> None:
    # Given: the running profile's photoperiod is being re-saved with new clocks.
    state = _State()
    pool = _Pool(state)
    identity = _IdentitySource(state)
    schedule = _ScheduleService(state)
    repository = TimelineApplyRepository(pool, identity, schedule)
    invalidator = _Invalidator(state)
    service = ClimateTimelineApplyService(repository, invalidator)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: Apply commits the changed active photoperiod.
    response = await service.apply("Veg Room", "main", request)

    # Then: the schedule owner runs once on the same connection before commit,
    # reusing the exact committed parameter row.
    assert response.config_revision == "0000008"
    assert len(schedule.calls) == 1
    call = schedule.calls[0]
    assert call["connection"] is pool.connection
    assert (call["location"], call["cluster"]) == (_LOCATION, _CLUSTER)
    assert (call["mode_id"], call["submode_id"]) == (1, None)
    assert call["parameters"] == {
        "day_start_time": _APPLIED_DAY,
        "night_start_time": _APPLIED_NIGHT,
        "light_ramp_up_minutes": 10,
        "light_ramp_down_minutes": 10,
        "main_light_intensity": 72.0,
        "supplemental_light_intensity": 0.0,
    }
    assert state.trace.index("sync_schedules") < state.trace.index("commit")


@pytest.mark.asyncio
async def test_apply_preserves_schedules_when_active_photoperiod_is_unchanged() -> None:
    # Given: a re-save whose clocks/ramps already equal the stored active row.
    state = _State()
    service, invalidator, schedule = _service(state)
    request = TimelineApplyRequest.model_validate(_payload(photoperiod=_unchanged_photoperiod()))

    # When: Apply commits the identical active aggregate.
    response = await service.apply("Veg Room", "main", request)

    # Then: no schedule sync touches the room; the stored row values stand.
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert schedule.calls == []
    row = state.mode_parameters[(1, None)]
    assert (row["day_start_time"], row["night_start_time"]) == (_STORED_DAY, _STORED_NIGHT)
    assert (row["light_ramp_up_minutes"], row["light_ramp_down_minutes"]) == (15, 15)
    assert (row["main_light_intensity"], row["supplemental_light_intensity"]) == (72.0, 0.0)
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "valid_identity",
        "lock_active",
        "read_prior_parameters",
        "stage_photoperiod",
        "delete_periods",
        "stage_period",
        "increment_revision",
        "commit",
        "invalidate",
    ]
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "read_prior_parameters",
        "update_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    assert invalidator.calls == [(_LOCATION, _CLUSTER, "0000008", 1, None)]


@pytest.mark.asyncio
async def test_apply_replaces_only_the_null_base_scope_and_keeps_submode_siblings() -> None:
    # Given: a NULL-base sibling profile saved while a different submode runs.
    flower_base = {
        "day_start_time": _STORED_DAY,
        "night_start_time": _STORED_NIGHT,
        "light_ramp_up_minutes": 15,
        "light_ramp_down_minutes": 15,
        "main_light_intensity": 72.0,
        "supplemental_light_intensity": 0.0,
    }
    sibling_base = {
        "day_start_time": _STORED_DAY,
        "night_start_time": _STORED_NIGHT,
        "light_ramp_up_minutes": 30,
        "light_ramp_down_minutes": 30,
        "main_light_intensity": 55.0,
        "supplemental_light_intensity": 5.0,
    }
    sibling_bulk = {
        "day_start_time": _STORED_DAY,
        "night_start_time": _STORED_NIGHT,
        "light_ramp_up_minutes": 15,
        "light_ramp_down_minutes": 15,
        "main_light_intensity": 80.0,
        "supplemental_light_intensity": 0.0,
    }
    before_flower_base = deepcopy(flower_base)
    before_sibling_bulk = deepcopy(sibling_bulk)
    state = _State(
        active=(2, 3),
        periods=[
            {
                "period_name": "Saved Day",
                "heating_setpoint": 20.0,
                "mode_id": 1,
                "submode_id": None,
            },
            {
                "period_name": "Sibling Base",
                "heating_setpoint": 18.0,
                "mode_id": 2,
                "submode_id": None,
            },
            {
                "period_name": "Sibling Bulk",
                "heating_setpoint": 19.0,
                "mode_id": 2,
                "submode_id": 3,
            },
        ],
        mode_parameters={
            (1, None): flower_base,
            (2, None): sibling_base,
            (2, 3): sibling_bulk,
        },
    )
    service, invalidator, schedule = _service(state)
    request = TimelineApplyRequest.model_validate(_payload(mode_id=2, submode_id=None))

    # When: Apply commits the changed NULL-base preparation.
    response = await service.apply("Veg Room", "main", request)

    # Then: only the NULL-base slice is replaced and no active authority moves.
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert state.periods == [
        {
            "period_name": "Saved Day",
            "heating_setpoint": 20.0,
            "mode_id": 1,
            "submode_id": None,
        },
        {
            "period_name": "Sibling Bulk",
            "heating_setpoint": 19.0,
            "mode_id": 2,
            "submode_id": 3,
        },
        {
            "period_name": "Draft Day",
            "heating_setpoint": 24.0,
            "mode_id": 2,
            "submode_id": None,
        },
    ]
    assert state.mode_parameters[(2, None)] == {
        "day_start_time": _APPLIED_DAY,
        "night_start_time": _APPLIED_NIGHT,
        "light_ramp_up_minutes": 10,
        "light_ramp_down_minutes": 10,
        "main_light_intensity": 55.0,
        "supplemental_light_intensity": 5.0,
    }
    assert state.mode_parameters[(1, None)] == before_flower_base
    assert state.mode_parameters[(2, 3)] == before_sibling_bulk
    assert schedule.calls == []
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "valid_identity",
        "lock_active",
        "stage_photoperiod",
        "delete_periods",
        "stage_period",
        "increment_revision",
        "commit",
        "invalidate",
    ]
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "update_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    assert json.loads(state.issued[-1][1][3]) == {"mode_id": 2, "submode_id": None}
    assert invalidator.calls == [(_LOCATION, _CLUSTER, "0000008", 2, None)]


@pytest.mark.asyncio
async def test_apply_inserts_the_missing_parameter_row_in_one_transaction() -> None:
    # Given: the running profile whose mode_parameters row does not exist yet.
    state = _State(mode_parameters={})
    service, invalidator, schedule = _service(state)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: Apply persists the aggregate inside its single transaction.
    response = await service.apply("Veg Room", "main", request)

    # Then: the parameter row is created in that same transaction with the
    # 100/0 intensity defaults, the saved baseline stays configured, and the
    # newly prepared photoperiod regenerates the derived schedules.
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "read_prior_parameters",
        "update_parameter",
        "insert_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    assert state.mode_parameters == {
        (1, None): {
            "day_start_time": _APPLIED_DAY,
            "night_start_time": _APPLIED_NIGHT,
            "light_ramp_up_minutes": 10,
            "light_ramp_down_minutes": 10,
            "main_light_intensity": 100,
            "supplemental_light_intensity": 0,
        }
    }
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "valid_identity",
        "lock_active",
        "read_prior_parameters",
        "stage_parameter_insert",
        "delete_periods",
        "stage_period",
        "increment_revision",
        "sync_schedules",
        "commit",
        "invalidate",
    ]
    assert len(schedule.calls) == 1
    assert schedule.calls[0]["parameters"] == {
        "location": "Veg Room",
        "cluster": "main",
        "mode_id": 1,
        "submode_id": None,
        "day_start_time": _APPLIED_DAY,
        "night_start_time": _APPLIED_NIGHT,
        "light_ramp_up_minutes": 10,
        "light_ramp_down_minutes": 10,
        "main_light_intensity": 100,
        "supplemental_light_intensity": 0,
    }
    assert invalidator.calls == [(_LOCATION, _CLUSTER, "0000008", 1, None)]


@pytest.mark.asyncio
async def test_apply_inserts_missing_row_for_inactive_profile_without_schedule_regeneration(
) -> None:
    # Given: an inactive profile with no parameter row while another mode runs.
    state = _State(mode_parameters={})
    service, invalidator, schedule = _service(state)
    request = TimelineApplyRequest.model_validate(_payload(mode_id=2, submode_id=None))

    # When: Apply creates the missing inactive preparation.
    response = await service.apply("Veg Room", "main", request)

    # Then: the row is inserted with 100/0 defaults and no schedule owner runs.
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert state.mode_parameters == {
        (2, None): {
            "day_start_time": _APPLIED_DAY,
            "night_start_time": _APPLIED_NIGHT,
            "light_ramp_up_minutes": 10,
            "light_ramp_down_minutes": 10,
            "main_light_intensity": 100,
            "supplemental_light_intensity": 0,
        }
    }
    assert schedule.calls == []
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "update_parameter",
        "insert_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    assert state.trace[-2:] == ["commit", "invalidate"]
    assert invalidator.calls == [(_LOCATION, _CLUSTER, "0000008", 2, None)]


@pytest.mark.asyncio
async def test_apply_rolls_back_everything_when_the_single_schedule_sync_fails() -> None:
    # Given: the running profile's photoperiod change whose schedule owner fails.
    state = _State()
    service, invalidator, schedule = _service(
        state, schedule=_ScheduleService(state, RuntimeError("injected schedule sync failure"))
    )
    before = deepcopy((state.revision, state.periods, state.mode_parameters))
    request = TimelineApplyRequest.model_validate(_payload())

    # When: the derived schedule replacement raises inside the one transaction.
    with pytest.raises(RuntimeError, match="injected schedule sync failure"):
        await service.apply("Veg Room", "main", request)

    # Then: periods, parameters, revision and post-commit notices are all restored.
    assert (state.revision, state.periods, state.mode_parameters) == before
    assert schedule.calls == []
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "valid_identity",
        "lock_active",
        "read_prior_parameters",
        "stage_photoperiod",
        "delete_periods",
        "stage_period",
        "increment_revision",
        "rollback",
    ]
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "read_prior_parameters",
        "update_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    assert invalidator.calls == []


@pytest.mark.asyncio
async def test_repository_raises_typed_stale_revision_inside_transaction() -> None:
    # Given: a repository with a newer committed revision than the reviewed request.
    state = _State(revision=8)
    repository, _, schedule = _repository(state)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: the repository compares the revision while its transaction is active.
    with pytest.raises(TimelineApplyStaleRevisionError):
        await repository.apply("Veg Room", "main", request)

    # Then: no mutation begins before the stale comparison rejects it.
    assert schedule.calls == []
    assert state.trace == ["transaction_enter", "lock", "read_revision", "rollback"]


@pytest.mark.asyncio
async def test_apply_keeps_stored_intensity_values_when_parameters_already_exist() -> None:
    # Given: an existing parameter row carrying non-default intensity values.
    state = _State()
    repository, identity, schedule = _repository(state)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: the repository updates only the timeline-owned clocks and ramps.
    commit = await repository.apply("Veg Room", "main", request)

    # Then: no parameter INSERT is issued, the stored intensities survive, and
    # the changed photoperiod delegates once to the schedule owner.
    assert commit == TimelineApplyCommit("0000008", True)
    assert identity.calls == [("Veg Room", 1, None)]
    assert [tag for tag, _ in state.issued] == [
        "lock",
        "read_revision",
        "lock_active",
        "read_prior_parameters",
        "update_parameter",
        "delete_periods",
        "insert_period",
        "insert_version",
    ]
    row = state.mode_parameters[(1, None)]
    assert (row["main_light_intensity"], row["supplemental_light_intensity"]) == (72.0, 0.0)
    assert (row["day_start_time"], row["night_start_time"]) == (_APPLIED_DAY, _APPLIED_NIGHT)
    assert len(schedule.calls) == 1
    assert schedule.calls[0]["parameters"] == {
        "day_start_time": _APPLIED_DAY,
        "night_start_time": _APPLIED_NIGHT,
        "light_ramp_up_minutes": 10,
        "light_ramp_down_minutes": 10,
        "main_light_intensity": 72.0,
        "supplemental_light_intensity": 0.0,
    }


@pytest.mark.asyncio
async def test_apply_identity_rejection_precedes_every_write() -> None:
    # Given: an identity validator that rejects the requested profile.
    state = _State()
    identity = _IdentitySource(state, ProfileNotFoundError("mode 1 does not exist"))
    service, invalidator, schedule = _service(state, identity)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: Apply validates the exact identity inside the open transaction.
    with pytest.raises(ProfileNotFoundError):
        await service.apply("Veg Room", "main", request)

    # Then: the transaction rolls back before any period or parameter write,
    # and the invalidator never hears about the aborted attempt.
    assert identity.calls == [("Veg Room", 1, None)]
    assert [tag for tag, _ in state.issued] == ["lock", "read_revision"]
    assert state.trace == ["transaction_enter", "lock", "read_revision", "rollback"]
    assert schedule.calls == []
    assert invalidator.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identity_error", "status", "code"),
    [
        (ProfileNotFoundError("mode 1 does not exist"), 404, "profile_not_found"),
        (
            InvalidProfileIdentityError("flower submode requires the flower mode"),
            422,
            "invalid_profile_identity",
        ),
    ],
)
async def test_apply_route_translates_profile_identity_failures(
    identity_error: Exception, status: int, code: str
) -> None:
    # Given: an identity validator raising one of the typed repository errors.
    state = _State()
    service, _, schedule = _service(state, _IdentitySource(state, identity_error))
    app = _app(service)

    # When: the authenticated client applies against the unknown profile.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/climate-timeline/Veg%20Room/main/apply", json=_payload())

    # Then: the route maps the typed error without exposing a partial commit.
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code
    assert schedule.calls == []
    assert state.trace[-1] == "rollback"


@pytest.mark.asyncio
async def test_apply_threads_a_post_commit_warning_into_the_response() -> None:
    # Given: a repository commit followed by an invalidator that reports failure.
    state = _State()
    repository, identity, schedule = _repository(state)
    invalidator_calls: list[tuple[str, str, str, int | None, int | None]] = []

    class _WarningInvalidator:
        async def invalidate(
            self,
            location: str,
            cluster: str,
            revision: str,
            mode_id: int | None,
            submode_id: int | None,
        ) -> str | None:
            invalidator_calls.append((location, cluster, revision, mode_id, submode_id))
            return "configuration_notification_failed"

    service = ClimateTimelineApplyService(repository, _WarningInvalidator())

    # When: Apply commits the aggregate and the notification boundary warns.
    request = TimelineApplyRequest.model_validate(_payload())
    response = await service.apply("Veg Room", "main", request)

    # Then: the committed baseline stands and the warning is reported truthfully.
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert response.notification_warning == "configuration_notification_failed"
    assert identity.calls == [("Veg Room", 1, None)]
    assert len(schedule.calls) == 1
    assert invalidator_calls == [(_LOCATION, _CLUSTER, "0000008", 1, None)]


@pytest.mark.asyncio
async def test_route_dependency_wires_the_database_schedule_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an isolated database identity/pool and the schedule owner provider.
    state = _State()
    pool = _Pool(state)
    identity = _IdentitySource(state)
    schedule = _ScheduleService(state)
    database = SimpleNamespace(pool=pool, room_mode_repo=identity)
    monkeypatch.setattr(timeline_routes, "get_room_schedule_service", lambda: schedule)
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the route dependency assembles the real wiring and commits a draft.
    service = get_apply_service(database)  # type: ignore[arg-type]
    response = await service.apply(
        "Veg Room", "main", TimelineApplyRequest.model_validate(_payload())
    )

    # Then: the schedule owner runs through that wiring and the active profile
    # notification reaches the boundary with a clean warning.
    assert response.config_revision == "0000008"
    assert response.parameters_configured is True
    assert response.notification_warning is None
    assert len(schedule.calls) == 1
    assert state.periods == [
        {
            "period_name": "Draft Day",
            "heating_setpoint": 24.0,
            "mode_id": 1,
            "submode_id": None,
        }
    ]
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000008", "mode_id": 1, "submode_id": None},
        )
    ]


class _RecordingStateManager:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.deletes: list[str] = []

    async def delete(self, key: str, skip_redis: bool = False) -> bool:
        del skip_redis
        if self._error is not None:
            raise self._error
        self.deletes.append(key)
        return True


class _RecordingEventBus:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.events: list[ConfigChangeEvent] = []

    async def publish(self, event: ConfigChangeEvent) -> bool:
        if self._error is not None:
            raise self._error
        self.events.append(event)
        return True


class _FakeDatabase:
    def __init__(
        self,
        active: dict[str, object] | None,
        prior_rows: list[dict[str, object]] | None = None,
        active_error: Exception | None = None,
    ) -> None:
        self.room_mode_repo = AsyncMock()
        if active_error is not None:
            self.room_mode_repo.get_active_mode = AsyncMock(side_effect=active_error)
        else:
            self.room_mode_repo.get_active_mode = AsyncMock(return_value=active)
        self.climate_periods_repo = AsyncMock()
        self.climate_periods_repo.get_periods = AsyncMock(return_value=prior_rows or [])


def _invalidator(
    active: dict[str, object] | None,
    prior: list[dict[str, object]] | None = None,
    affected: Sequence[tuple[int, int | None]] = (),
    active_error: Exception | None = None,
) -> SavedTimelineConfigurationInvalidator:
    database = _FakeDatabase(active, prior, active_error=active_error)
    return SavedTimelineConfigurationInvalidator(database, affected_identities=affected)


@pytest.mark.asyncio
async def test_invalidator_clears_schedules_and_publishes_for_the_active_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a committed change to the identity that is currently active.
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator runs for the active profile after commit.
    warning = await _invalidator({"mode_id": 1, "submode_id": None}).invalidate(
        _LOCATION, _CLUSTER, "0000008", 1, None
    )

    # Then: the exact climate key, the schedule keys and one scoped event follow.
    assert warning is None
    assert state_manager.deletes == [
        climate_period_cache_key(_LOCATION, _CLUSTER, 1, None),
        *_SCHEDULE_KEYS,
    ]
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000008", "mode_id": 1, "submode_id": None},
        )
    ]
    assert (event_bus.events[0].location, event_bus.events[0].cluster) == (_LOCATION, _CLUSTER)
    assert event_bus.events[0].config_type == "climate_timeline"


@pytest.mark.asyncio
async def test_invalidator_leaves_schedules_and_events_untouched_for_inactive_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a saved Drying-style profile that is not the running Flower profile.
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator runs for the inactive profile after commit.
    warning = await _invalidator({"mode_id": 1, "submode_id": None}).invalidate(
        _LOCATION, _CLUSTER, "0000008", 2, None
    )

    # Then: only the exact selected climate key is dropped; the running
    # profile's key, schedule caches and broadcast stay untouched.
    assert warning is None
    assert state_manager.deletes == [climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)]
    assert climate_period_cache_key(_LOCATION, _CLUSTER, 1, None) not in state_manager.deletes
    assert not [key for key in state_manager.deletes if key.startswith("schedules")]
    assert event_bus.events == []


@pytest.mark.asyncio
async def test_invalidator_skips_active_notification_when_no_active_identity_is_known(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a scoped save while the room has no active mode row.
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator runs without any active authority to compare.
    warning = await _invalidator(None).invalidate(_LOCATION, _CLUSTER, "0000008", 2, None)

    # Then: the selected key is cleared and no guessed active identity notifies.
    assert warning is None
    assert state_manager.deletes == [climate_period_cache_key(_LOCATION, _CLUSTER, 2, None)]
    assert event_bus.events == []


@pytest.mark.asyncio
async def test_invalidator_still_warns_and_clears_when_the_active_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a scoped inactive save whose active-authority read bounces.
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator cannot read the active identity after commit.
    warning = await _invalidator(
        {"mode_id": 1, "submode_id": None}, active_error=ConnectionError("db busy")
    ).invalidate(_LOCATION, _CLUSTER, "0000008", 2, None)

    # Then: the committed save is not rolled back by a read failure; the saved
    # key is still cleared and the warning reports the notification failure.
    assert warning == "configuration_notification_failed"
    assert climate_period_cache_key(_LOCATION, _CLUSTER, 2, None) in state_manager.deletes
    assert event_bus.events == []


@pytest.mark.asyncio
async def test_invalidator_broad_replacement_clears_the_supplied_identity_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an unscoped legacy replacement whose caller supplied the
    # prior/replacement identity union while another identity is active.
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)
    invalidator = SavedTimelineConfigurationInvalidator(
        _FakeDatabase({"mode_id": 1, "submode_id": None}, []),
        affected_identities=((2, None), (2, 3)),
    )

    # When: the invalidator runs without a scoped mode identity.
    warning = await invalidator.invalidate(_LOCATION, _CLUSTER, "0000009", None, None)

    # Then: one named key per supplied identity plus the active profile key is
    # deleted without a scan, and the valid active profile still notifies.
    assert warning is None
    assert set(state_manager.deletes) == {
        climate_period_cache_key(_LOCATION, _CLUSTER, 2, None),
        climate_period_cache_key(_LOCATION, _CLUSTER, 2, 3),
        climate_period_cache_key(_LOCATION, _CLUSTER, 1, None),
        *_SCHEDULE_KEYS,
    }
    assert [(event.event_type, event.data) for event in event_bus.events] == [
        (
            ConfigEventType.SCHEDULE_CHANGED,
            {"config_revision": "0000009", "mode_id": None, "submode_id": None},
        )
    ]


@pytest.mark.asyncio
async def test_invalidator_reports_a_warning_when_cache_deletion_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a state manager whose deletes raise after the commit.
    state_manager = _RecordingStateManager(ConnectionError("redis down"))
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator cannot delete the climate key for an inactive save.
    warning = await _invalidator({"mode_id": 1, "submode_id": None}).invalidate(
        _LOCATION, _CLUSTER, "0000008", 2, None
    )

    # Then: the persisted change stands and only the warning reports the failure.
    assert warning == "configuration_notification_failed"
    assert state_manager.deletes == []
    assert event_bus.events == []


@pytest.mark.asyncio
async def test_invalidator_retains_a_delivered_event_when_a_cache_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an active save whose cache deletes bounce but whose event delivers.
    state_manager = _RecordingStateManager(ConnectionError("redis down"))
    event_bus = _RecordingEventBus()
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator clears and publishes for the active profile.
    warning = await _invalidator({"mode_id": 1, "submode_id": None}).invalidate(
        _LOCATION, _CLUSTER, "0000008", 1, None
    )

    # Then: the warning is returned, and the event remains a real delivery
    # without any automatic cache retry.
    assert warning == "configuration_notification_failed"
    assert len(event_bus.events) == 1


@pytest.mark.asyncio
async def test_invalidator_reports_a_warning_when_the_schedule_event_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: cache keys that delete fine and an event bus that cannot publish.
    state_manager = _RecordingStateManager()
    event_bus = _RecordingEventBus(ConnectionError("queue closed"))
    monkeypatch.setattr(apply_module, "get_state_manager", lambda: state_manager)
    monkeypatch.setattr(apply_module, "get_event_bus", lambda: event_bus)

    # When: the invalidator publishes the schedule change for the active profile.
    warning = await _invalidator({"mode_id": 1, "submode_id": None}).invalidate(
        _LOCATION, _CLUSTER, "0000008", 1, None
    )

    # Then: the deletes remain real and the publish failure is reported, not raised.
    assert warning == "configuration_notification_failed"
    assert state_manager.deletes[:1] == [climate_period_cache_key(_LOCATION, _CLUSTER, 1, None)]
    assert event_bus.events == []
