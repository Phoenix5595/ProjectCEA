from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import time

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.repositories.climate_timeline_apply import (
    TimelineApplyRepository,
    TimelineApplyStaleRevisionError,
)
from app.routes.climate_timeline import get_apply_service, router
from app.schemas.climate_timeline import TimelineApplyRequest, TimelineApplyResponse
from app.services.climate_timeline_apply import ClimateTimelineApplyService
from shared.auth import APIKeyAuthMiddleware


def _payload(*, expected_config_revision: str = "0000007") -> dict[str, object]:
    return {
        "request_id": "apply-17",
        "expected_config_revision": expected_config_revision,
        "draft_revision": 4,
        "mode_id": 1,
        "submode_id": None,
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
        "photoperiod": {
            "day_start_time": "06:00",
            "night_start_time": "18:00",
            "ramp_up_minutes": 10,
            "ramp_down_minutes": 10,
        },
    }


@dataclass
class _State:
    revision: int = 7
    periods: list[dict[str, object]] = field(
        default_factory=lambda: [{"period_name": "Saved Day", "heating_setpoint": 20.0}]
    )
    photoperiod: dict[str, object] = field(
        default_factory=lambda: {
            "day_start_time": "07:00",
            "night_start_time": "19:00",
            "light_ramp_up_minutes": 15,
            "light_ramp_down_minutes": 15,
        }
    )
    light_intensities: dict[tuple[int, int], float] = field(default_factory=lambda: {(9, 1): 72.0})
    fail_period_insert: bool = False
    trace: list[str] = field(default_factory=list)


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
        except RuntimeError:
            self._state.trace.append("rollback")
            raise
        else:
            self._state.revision = self._staged.revision
            self._state.periods = self._staged.periods
            self._state.photoperiod = self._staged.photoperiod
            self._state.trace.append("commit")

    async def fetchval(self, query: str, *args: object) -> int:
        assert self._staged is not None
        if "MAX(version_id)" in query:
            self._state.trace.append("read_revision")
            return self._staged.revision
        if "UPDATE mode_parameters" in query:
            self._state.trace.append("stage_photoperiod")
            self._staged.photoperiod = {
                "day_start_time": args[0],
                "night_start_time": args[1],
                "light_ramp_up_minutes": args[2],
                "light_ramp_down_minutes": args[3],
            }
            return 1
        raise AssertionError(query)

    async def execute(self, query: str, *args: object) -> str:
        assert self._staged is not None
        if "pg_advisory_xact_lock" in query:
            self._state.trace.append("lock")
            return "SELECT 1"
        if "DELETE FROM climate_periods" in query:
            self._state.trace.append("delete_periods")
            self._staged.periods = []
            return "DELETE 1"
        if "INSERT INTO climate_periods" in query:
            self._state.trace.append("stage_period")
            if self._state.fail_period_insert:
                raise RuntimeError("injected period insert failure")
            self._staged.periods.append({"period_name": args[4], "heating_setpoint": args[8]})
            return "INSERT 0 1"
        raise AssertionError(query)

    async def fetchrow(self, query: str, *_args: object) -> dict[str, int]:
        assert self._staged is not None
        if "INSERT INTO config_versions" in query:
            self._state.trace.append("increment_revision")
            self._staged.revision += 1
            return {"version_id": self._staged.revision}
        raise AssertionError(query)


class _Pool:
    def __init__(self, state: _State) -> None:
        self._connection = _Connection(state)

    @asynccontextmanager
    async def acquire(self):
        yield self._connection


class _Invalidator:
    def __init__(self, state: _State) -> None:
        self._state = state
        self.calls: list[tuple[str, str, str]] = []

    async def invalidate(self, location: str, cluster: str, revision: str) -> None:
        self._state.trace.append("invalidate")
        self.calls.append((location, cluster, revision))


def _service(state: _State) -> tuple[ClimateTimelineApplyService, _Invalidator]:
    invalidator = _Invalidator(state)
    return ClimateTimelineApplyService(
        TimelineApplyRepository(_Pool(state)), invalidator
    ), invalidator


@pytest.mark.asyncio
async def test_apply_returns_409_with_draft_identity_when_revision_is_stale() -> None:
    # Given: an Apply draft based on revision seven while saved authority is revision eight.
    state = _State(revision=8)
    service, invalidator = _service(state)
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_apply_service] = lambda: service

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
    assert state.trace == ["transaction_enter", "lock", "read_revision", "rollback"]
    assert invalidator.calls == []


@pytest.mark.asyncio
async def test_apply_route_returns_the_committed_saved_baseline() -> None:
    # Given: a valid reviewed aggregate and its transaction-backed Apply service.
    state = _State()
    service, invalidator = _service(state)
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_apply_service] = lambda: service

    # When: the authenticated client applies the reviewed timeline over HTTP.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/climate-timeline/Veg%20Room/main/apply", json=_payload())

    # Then: the route returns the new saved revision after commit and invalidation.
    assert response.status_code == 200
    body = TimelineApplyResponse.model_validate_json(response.content, strict=False)
    assert (body.request_id, body.config_revision) == ("apply-17", "0000008")
    assert state.trace[-2:] == ["commit", "invalidate"]
    assert invalidator.calls == [("Veg Room", "main", "0000008")]


@pytest.mark.asyncio
async def test_apply_rolls_back_timeline_aggregate_when_period_write_fails() -> None:
    # Given: a persisted aggregate and an injected failure after the replacement begins.
    state = _State(fail_period_insert=True)
    service, invalidator = _service(state)
    before = deepcopy((state.revision, state.periods, state.photoperiod, state.light_intensities))
    request = TimelineApplyRequest.model_validate(_payload())

    # When: the period insert raises from inside the one transaction.
    with pytest.raises(RuntimeError, match="injected period insert failure"):
        await service.apply("Veg Room", "main", request)

    # Then: no timeline field, revision, light target, or invalidation escapes the rollback.
    assert (state.revision, state.periods, state.photoperiod, state.light_intensities) == before
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "delete_periods",
        "stage_period",
        "rollback",
    ]
    assert invalidator.calls == []


@pytest.mark.asyncio
async def test_apply_rejects_invalid_review_before_opening_a_transaction() -> None:
    # Given: a reviewed aggregate containing overlapping half-open periods.
    state = _State()
    service, invalidator = _service(state)
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
    # Given: one valid reviewed aggregate and an unrelated persisted light intensity sentinel.
    state = _State()
    service, invalidator = _service(state)
    request = TimelineApplyRequest.model_validate(_payload())

    # When: Apply persists the timeline-owned aggregate.
    response = await service.apply("Veg Room", "main", request)

    # Then: one committed revision is returned and publication follows, never precedes, commit.
    assert isinstance(response, TimelineApplyResponse)
    assert response.config_revision == "0000008"
    assert state.revision == 8
    assert state.periods == [{"period_name": "Draft Day", "heating_setpoint": 24.0}]
    assert state.photoperiod == {
        "day_start_time": time(6, 0),
        "night_start_time": time(18, 0),
        "light_ramp_up_minutes": 10,
        "light_ramp_down_minutes": 10,
    }
    assert state.light_intensities == {(9, 1): 72.0}
    assert state.trace == [
        "transaction_enter",
        "lock",
        "read_revision",
        "delete_periods",
        "stage_period",
        "stage_photoperiod",
        "increment_revision",
        "commit",
        "invalidate",
    ]
    assert invalidator.calls == [("Veg Room", "main", "0000008")]


@pytest.mark.asyncio
async def test_repository_raises_typed_stale_revision_inside_transaction() -> None:
    # Given: a repository with a newer committed revision than the reviewed request.
    state = _State(revision=8)
    repository = TimelineApplyRepository(_Pool(state))
    request = TimelineApplyRequest.model_validate(_payload())

    # When: the repository compares the revision while its transaction is active.
    with pytest.raises(TimelineApplyStaleRevisionError):
        await repository.apply("Veg Room", "main", request)

    # Then: no mutation begins before the stale comparison rejects it.
    assert state.trace == ["transaction_enter", "lock", "read_revision", "rollback"]
