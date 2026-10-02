from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import date, timedelta
import json
from typing import final

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.repositories.climate_timeline_snapshot import (
    ClimateScheduleConfiguration,
    ClimateScheduleSnapshot,
    ClimateScheduleSnapshotBuilder,
)
from app.routes.climate_periods import get_database
from app.routes.climate_timeline import get_preview_service, router
from app.schemas.climate_timeline import (
    RichTrajectoryEnvelope,
    StepTrajectorySegment,
    TimelinePreviewRequest,
    TimelinePreviewResponse,
)
from app.services import climate_timeline_preview
from app.services.climate_timeline_preview import ClimateTimelinePreviewService
from shared.auth import APIKeyAuthMiddleware


@final
class ReadOnlySchedules:
    def __init__(self, configuration: ClimateScheduleConfiguration) -> None:
        self.configuration: ClimateScheduleConfiguration = configuration
        self.write_calls: int = 0

    async def read_active_mode(self, location: str, cluster: str) -> Mapping[str, object]:
        del location, cluster
        return {"mode_id": 1, "submode_id": None}

    async def read_calendar_transition(self, location: str, cluster: str, on_date: date) -> None:
        del location, cluster, on_date
        return None

    async def read_schedule_configuration(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> ClimateScheduleConfiguration:
        del location, cluster, mode_id, submode_id
        return self.configuration

    async def write_schedule(self) -> None:
        self.write_calls += 1
        raise AssertionError("preview must never write")


@final
class SavedRevisionDatabase:
    class Config:
        async def get_latest_config_version(self) -> int:
            return 9

    config_repo = Config()


def _payload(*, periods: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "request_id": "preview-17",
        "expected_config_revision": "cfg-9",
        "draft_revision": 4,
        "mode_id": 1,
        "submode_id": None,
        "window": {
            "start": "2026-03-08T00:00:00Z",
            "end": "2026-03-09T00:00:00Z",
            "timezone": "America/Toronto",
        },
        "periods": periods
        or [
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


def _service() -> tuple[ClimateTimelinePreviewService, ReadOnlySchedules]:
    configuration = ClimateScheduleConfiguration.from_rows(
        {
            "day_start_time": "06:00",
            "night_start_time": "18:00",
            "light_ramp_up_minutes": 10,
            "light_ramp_down_minutes": 10,
        },
        (
            {
                "id": "saved-day",
                "period_name": "Saved Day",
                "start_time": "00:00",
                "end_time": "00:00",
                "ramp_minutes": 0,
                "heating_setpoint": 20.0,
                "cooling_setpoint": None,
                "vpd_setpoint": None,
                "co2_setpoint": None,
                "details": "",
            },
        ),
    )
    source = ReadOnlySchedules(configuration)
    return ClimateTimelinePreviewService(ClimateScheduleSnapshotBuilder(source)), source


def _app(service: ClimateTimelinePreviewService) -> FastAPI:
    app = FastAPI()
    app.add_middleware(APIKeyAuthMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_preview_service] = lambda: service
    return app


@pytest.mark.asyncio
async def test_saved_route_returns_saved_revision_periods_and_trajectory() -> None:
    # Given: a read-only saved schedule and a current configuration revision.
    service, _ = _service()
    app = _app(service)
    app.dependency_overrides[get_database] = SavedRevisionDatabase

    # When: an authenticated caller requests the saved timeline window.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/climate-timeline/Veg%20Room/main",
            params={
                "start": "2026-03-08T00:00:00Z",
                "end": "2026-03-09T00:00:00Z",
                "timezone": "America/Toronto",
            },
        )

    # Then: the saved aggregate and saved-only rich trajectory cross the route boundary.
    assert response.status_code == 200
    assert response.json()["config_revision"] == "0000009"
    assert response.json()["periods"][0]["period_name"] == "Saved Day"
    assert response.json()["trajectory"]["revision_scope"] == "saved"


@pytest.mark.asyncio
async def test_saved_timeline_retains_photoperiod_when_climate_periods_are_empty() -> None:
    # Given: saved light times, but no configured climate setpoint periods.
    service, source = _service()
    source.configuration = ClimateScheduleConfiguration.from_rows(
        dict(source.configuration.parameters), []
    )
    app = _app(service)
    app.dependency_overrides[get_database] = SavedRevisionDatabase

    # When: a client reads the independent photoperiod and timeline.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/climate-timeline/Veg%20Room/main",
            params={
                "start": "2026-03-08T00:00:00Z",
                "end": "2026-03-09T00:00:00Z",
                "timezone": "America/Toronto",
            },
        )

    # Then: known light times survive, without fabricating climate targets.
    assert response.status_code == 200
    body = response.json()
    assert body["periods"] == []
    assert body["photoperiod"] == {
        "day_start_time": "06:00",
        "night_start_time": "18:00",
        "ramp_up_minutes": 10,
        "ramp_down_minutes": 10,
    }
    assert body["trajectory"] is None


@pytest.mark.asyncio
async def test_saved_response_reuses_matching_worker_trajectory_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a saved snapshot and a worker trajectory with the current config revision.
    service, _ = _service()
    request = TimelinePreviewRequest.model_validate_json(json.dumps(_payload()))
    snapshot = await service.saved("Veg Room", "main", request)
    cached = climate_timeline_preview.project_saved_trajectory(snapshot, "Veg Room", "0000009")

    class Reader:
        def read_rich_trajectory(self, location: str):
            assert location == "Veg Room"
            return cached

    service = replace(service, saved_trajectory_reader=Reader())
    monkeypatch.setattr(
        climate_timeline_preview,
        "project_saved_trajectory",
        lambda *_args: pytest.fail("saved cache miss recomputed the trajectory"),
    )

    # When: the saved Control response is requested for that same revision.
    response = await service.saved_response("Veg Room", "main", snapshot.window, "0000009")

    # Then: the cached rich trajectory crosses the response boundary unchanged.
    assert response.trajectory == cached


@pytest.mark.asyncio
async def test_saved_response_recomputes_worker_cache_with_a_mismatched_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a saved snapshot and a worker trajectory for a different UTC window.
    service, _ = _service()
    request = TimelinePreviewRequest.model_validate_json(json.dumps(_payload()))
    snapshot = await service.saved("Veg Room", "main", request)
    cached = climate_timeline_preview.project_saved_trajectory(snapshot, "Veg Room", "0000009")
    assert cached is not None
    mismatched = cached.model_copy(
        update={
            "window": cached.window.model_copy(
                update={"end": cached.window.end + timedelta(microseconds=1)}
            )
        }
    )
    calls: list[tuple[ClimateScheduleSnapshot, str, str]] = []

    class Reader:
        def read_rich_trajectory(self, location: str):
            assert location == "Veg Room"
            return mismatched

    def recompute(
        snapshot: ClimateScheduleSnapshot, room: str, revision: str
    ) -> RichTrajectoryEnvelope:
        calls.append((snapshot, room, revision))
        return cached

    service = replace(service, saved_trajectory_reader=Reader())
    monkeypatch.setattr(climate_timeline_preview, "project_saved_trajectory", recompute)

    # When: the saved Control response is requested for the snapshot window.
    response = await service.saved_response("Veg Room", "main", snapshot.window, "0000009")

    # Then: the mismatched cache is not authoritative and the request window is recomputed.
    assert response.trajectory == cached
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_equivalent_saved_and_preview_inputs_have_equal_scheduled_trajectories() -> None:
    # Given: a draft that is identical to the saved schedule over one immutable window.
    service, _ = _service()
    payload = _payload(
        periods=[
            {
                "id": "saved-day",
                "period_name": "Saved Day",
                "start_time": "00:00",
                "end_time": "00:00",
                "ramp_minutes": 0,
                "heating_setpoint": 20.0,
                "cooling_setpoint": None,
                "vpd_setpoint": None,
                "co2_setpoint": None,
                "details": "",
            }
        ]
    )
    request = TimelinePreviewRequest.model_validate(payload)
    snapshot = await service.saved("Veg Room", "main", request)
    saved = climate_timeline_preview.project_saved_trajectory(snapshot, "Veg Room", "cfg-9")
    assert saved is not None

    # When: the equivalent immutable draft is evaluated for preview.
    preview = await service.preview("Veg Room", "main", request)

    # Then: draft provenance differs only in metadata; scheduled values and intervals are equal.
    saved_scheduled = tuple(
        segment.model_dump(exclude={"source", "trajectory_kind"})
        for segment in saved.segments
        if segment.trajectory_kind == "scheduled"
    )
    preview_scheduled = tuple(
        segment.model_dump(exclude={"source", "trajectory_kind"})
        for segment in preview.trajectory.segments
        if segment.trajectory_kind == "scheduled"
    )
    assert preview_scheduled == saved_scheduled


@pytest.mark.asyncio
async def test_preview_returns_draft_trajectory_and_echoes_request_identity_without_writes() -> (
    None
):
    # Given: a saved 20 C schedule and a complete 24 C draft served through the HTTP route.
    service, source = _service()

    # When: an authenticated API client previews the draft for an authorized room.
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/climate-timeline/Veg%20Room/main/preview", json=_payload()
        )

    # Then: the request identity and draft-only trajectory are returned with no write-capable call.
    assert response.status_code == 200
    body = TimelinePreviewResponse.model_validate_json(response.content, strict=False)
    assert (body.request_id, body.draft_revision) == ("preview-17", 4)
    assert body.trajectory.revision_scope == "draft"
    assert isinstance(body.trajectory.segments[0], StepTrajectorySegment)
    assert body.trajectory.segments[0].value == 24.0
    assert source.write_calls == 0


@pytest.mark.asyncio
async def test_preview_requires_api_key_when_api_authentication_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: API-key enforcement and a valid complete draft.
    monkeypatch.setenv("CEA_API_KEY_REQUIRE", "true")
    monkeypatch.setenv("CEA_API_KEY", "test-key")
    service, _ = _service()

    # When: a caller omits the API key from the protected preview endpoint.
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/climate-timeline/Veg%20Room/main/preview", json=_payload()
        )

    # Then: authentication rejects the request before route evaluation.
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_preview_rejects_overlapping_full_draft_with_typed_error_and_preserves_saved_projection() -> (
    None
):
    # Given: a draft whose two half-open periods overlap and a known saved projection.
    service, source = _service()
    request = TimelinePreviewRequest.model_validate_json(json.dumps(_payload()))
    saved = await service.saved("Veg Room", "main", request)
    overlap: list[dict[str, object]] = [
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
            "id": "night",
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

    # When: the invalid full draft reaches the preview endpoint.
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/climate-timeline/Veg%20Room/main/preview", json=_payload(periods=overlap)
        )

    # Then: overlap is typed, no write happens, and saved authority has not changed.
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_period_overlap"
    assert (await service.saved("Veg Room", "main", request)) == saved
    assert source.write_calls == 0


@pytest.mark.asyncio
async def test_preview_rejects_unauthorized_room_cluster() -> None:
    # Given: a valid draft and a service with no room access beyond device-cluster topology.
    service, _ = _service()

    # When: the request uses Flower Room's sensor sub-cluster instead of its authorized device cluster.
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/climate-timeline/Flower%20Room/front/preview", json=_payload()
        )

    # Then: room authorization rejects the cross-plane cluster before evaluation.
    assert response.status_code == 400
