from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
import json
from typing import final

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.monitoring_publication.rich import project_saved_trajectory
from app.repositories.climate_timeline_snapshot import (
    ClimateProfileConfiguration,
    ClimateScheduleConfiguration,
    ClimateScheduleSnapshot,
    ClimateScheduleSnapshotBuilder,
    TimelineWindow,
    frozen,
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
        self.config_revision: str = "cfg-9"
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

    async def read_profile(
        self, location: str, cluster: str, mode_id: int, submode_id: int | None
    ) -> ClimateProfileConfiguration:
        del location, cluster
        return ClimateProfileConfiguration(
            frozen({"mode_id": mode_id, "submode_id": submode_id}),
            self.configuration,
            self.config_revision,
            True,
        )

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


async def _project_profile(
    window: TimelineWindow, periods: tuple[Mapping[str, object], ...]
) -> RichTrajectoryEnvelope | None:
    configuration = ClimateScheduleConfiguration.from_rows(
        {
            "day_start_time": "06:00",
            "night_start_time": "18:00",
            "light_ramp_up_minutes": 15,
            "light_ramp_down_minutes": 15,
        },
        periods,
    )
    builder = ClimateScheduleSnapshotBuilder(ReadOnlySchedules(configuration))
    profile = await builder.build_profile("Flower Room", "main", 2, 4, window)
    return project_saved_trajectory(
        profile.schedule, "Flower Room", profile.profile.config_revision
    )


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
async def test_all_null_reviewed_draft_is_reviewable_without_fake_numeric_values() -> None:
    # Given: a reviewable draft whose climate targets are intentionally all NULL.
    service, _ = _service()
    payload = _payload(
        periods=[
            {
                "id": "draft-constant",
                "period_name": "Constant",
                "start_time": "00:00",
                "end_time": "00:00",
                "ramp_minutes": 0,
                "heating_setpoint": None,
                "cooling_setpoint": None,
                "vpd_setpoint": None,
                "co2_setpoint": None,
                "details": "",
            }
        ]
    )
    request = TimelinePreviewRequest.model_validate(payload)

    # When: the all-NULL draft is reviewed.
    preview = await service.preview("Veg Room", "main", request)

    # Then: no numeric trajectory exists and no fake zero is invented.
    assert preview.trajectory is None


@pytest.mark.asyncio
async def test_preview_rejects_a_stale_request_revision_with_a_typed_conflict() -> None:
    # Given: a reviewed draft and a configuration revision that has since advanced.
    service, source = _service()
    request = TimelinePreviewRequest.model_validate_json(json.dumps(_payload()))
    source.config_revision = "cfg-10"

    # When: the stale draft is reviewed.
    with pytest.raises(climate_timeline_preview.TimelinePreviewRevisionConflictError) as conflict:
        await service.preview("Veg Room", "main", request)

    # Then: the conflict names both revisions instead of silently rebasing.
    assert conflict.value.expected == "cfg-9"
    assert conflict.value.current == "cfg-10"


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


def _climate_period(
    period_id: str,
    start_time: str,
    end_time: str,
    heating: float,
    ramp_minutes: int = 0,
) -> Mapping[str, object]:
    return {
        "id": period_id,
        "period_name": period_id,
        "start_time": start_time,
        "end_time": end_time,
        "ramp_minutes": ramp_minutes,
        "heating_setpoint": heating,
        "cooling_setpoint": None,
        "vpd_setpoint": None,
        "co2_setpoint": None,
        "details": "",
    }


@pytest.mark.asyncio
async def test_profile_forecast_interprets_stored_clocks_in_toronto_for_summer_and_winter() -> None:
    # Given: an exact Flower/Bulk saved profile with a 06:00 Toronto recurrence.
    for now, expected_start in (
        (datetime(2026, 7, 1, 12, tzinfo=UTC), datetime(2026, 7, 1, 10, tzinfo=UTC)),
        (datetime(2026, 1, 15, 12, tzinfo=UTC), datetime(2026, 1, 15, 11, tzinfo=UTC)),
    ):
        window = TimelineWindow.daily(now)
        trajectory = await _project_profile(
            window, (_climate_period("day", "06:00", "10:00", 22.0),)
        )

        # When: the saved profile recurrence is projected over that Toronto day.
        assert trajectory is not None
        day = next(
            segment
            for segment in trajectory.segments
            if isinstance(segment, StepTrajectorySegment)
            and segment.source.period.period_id == "day"
        )

        # Then: UTC endpoints follow the seasonal Toronto offset and retain exact provenance.
        assert (day.start, day.end) == (expected_start, expected_start + timedelta(hours=4))
        assert (day.source.mode, day.source.submode, day.source.config_revision) == (
            "2",
            "4",
            "cfg-9",
        )
        assert trajectory.base_config_revision == "cfg-9"
        assert trajectory.warnings == ()


@pytest.mark.asyncio
async def test_overnight_and_equal_clock_all_day_occurrences_cover_the_local_day() -> None:
    # Given: a rolling window beginning at Toronto 22:00 and an overnight 22:00–06:00 row.
    overnight_window = TimelineWindow.rolling(datetime(2026, 7, 2, 2, tzinfo=UTC))
    overnight = await _project_profile(
        overnight_window, (_climate_period("overnight", "22:00", "06:00", 18.0),)
    )

    # When: the occurrence is expanded from the preceding Toronto local date.
    assert overnight is not None
    overnight_steps = tuple(
        sorted(
            (
                segment
                for segment in overnight.segments
                if isinstance(segment, StepTrajectorySegment)
                and segment.trajectory_kind == "scheduled"
                and segment.source.period.period_id == "overnight"
            ),
            key=lambda segment: segment.start,
        )
    )

    # Then: both authoritative daily slices cover the same overnight occurrence without a gap.
    assert overnight_steps[0].start == overnight_window.start
    assert overnight_steps[-1].end == datetime(2026, 7, 2, 10, tzinfo=UTC)
    assert all(
        left.end == right.start for left, right in zip(overnight_steps, overnight_steps[1:])
    )
    assert all(segment.value == 18.0 for segment in overnight_steps)

    # Given: an all-day row represented by equal 06:00 start and end clocks.
    all_day_window = TimelineWindow.daily(datetime(2026, 7, 1, 12, tzinfo=UTC))
    all_day = await _project_profile(
        all_day_window, (_climate_period("all-day", "06:00", "06:00", 20.0),)
    )

    # Then: adjacent daily recurrences cover precisely the UTC instants of the local-day window.
    assert all_day is not None
    all_day_steps = tuple(
        sorted(
            (
                segment
                for segment in all_day.segments
                if isinstance(segment, StepTrajectorySegment)
                and segment.trajectory_kind == "scheduled"
                and segment.source.period.period_id == "all-day"
            ),
            key=lambda segment: segment.start,
        )
    )
    assert all_day_steps[0].start == all_day_window.start
    assert all_day_steps[-1].end == all_day_window.end
    assert all(
        left.end == right.start for left, right in zip(all_day_steps, all_day_steps[1:])
    )
    assert all(segment.value == 20.0 for segment in all_day_steps)


@pytest.mark.asyncio
async def test_dst_fold_and_gap_resolve_to_elapsed_utc_endpoints_with_profile_warnings() -> None:
    # Given: recurring periods whose start clock is ambiguous in fall or nonexistent in spring.
    cases = (
        (
            datetime(2026, 11, 1, 12, tzinfo=UTC),
            "01:30",
            "02:30",
            datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
            datetime(2026, 11, 1, 7, 30, tzinfo=UTC),
            "fold-first",
        ),
        (
            datetime(2026, 3, 8, 12, tzinfo=UTC),
            "02:30",
            "04:00",
            datetime(2026, 3, 8, 7, 30, tzinfo=UTC),
            datetime(2026, 3, 8, 8, 0, tzinfo=UTC),
            "gap-forward",
        ),
    )
    for now, start_time, end_time, expected_start, expected_end, assumption in cases:
        trajectory = await _project_profile(
            TimelineWindow.daily(now),
            (_climate_period("dst-period", start_time, end_time, 19.0),),
        )

        # When: each profile recurrence is resolved using Toronto's actual round-trip mapping.
        assert trajectory is not None
        period = next(
            segment
            for segment in trajectory.segments
            if isinstance(segment, StepTrajectorySegment)
            and segment.source.period.period_id == "dst-period"
        )

        # Then: the chosen fold/gap convention is explicit and the UTC interval is elapsed-time correct.
        assert (period.start, period.end) == (expected_start, expected_end)
        assert len(trajectory.warnings) == 1
        warning = trajectory.warnings[0]
        assert warning.code == "profile.dst_assumption"
        assert warning.detail.startswith(assumption)
        assert (period.source.mode, period.source.submode, period.source.config_revision) == (
            "2",
            "4",
            "cfg-9",
        )


@pytest.mark.asyncio
async def test_draft_preview_uses_toronto_wall_clocks_for_the_selected_profile() -> None:
    # Given: a complete selected-profile draft whose daytime row begins at Toronto 06:00.
    service, _ = _service()
    payload = _payload(
        periods=[
            dict(_climate_period("draft-day", "06:00", "10:00", 22.0)),
            dict(_climate_period("draft-night", "10:00", "06:00", 20.0)),
        ]
    )
    payload["window"] = {
        "start": "2026-07-01T04:00:00Z",
        "end": "2026-07-02T04:00:00Z",
        "timezone": "America/Toronto",
    }
    request = TimelinePreviewRequest.model_validate(payload)

    # When: the immutable selected-profile draft is previewed.
    preview = await service.preview("Veg Room", "main", request)

    # Then: its daytime interval uses Toronto's summer offset and draft provenance.
    assert preview.trajectory is not None
    day = next(
        segment
        for segment in preview.trajectory.segments
        if isinstance(segment, StepTrajectorySegment)
        and segment.source.period.period_id == "draft-day"
    )
    assert (day.start, day.end) == (
        datetime(2026, 7, 1, 10, tzinfo=UTC),
        datetime(2026, 7, 1, 14, tzinfo=UTC),
    )
    assert (
        day.source.mode,
        day.source.submode,
        day.source.config_revision,
        day.source.draft_revision,
    ) == ("1", None, "cfg-9", "4")


@pytest.mark.asyncio
async def test_saved_profile_mid_ramp_window_matches_full_recurrence() -> None:
    # Given: a saved day target ramping from the same profile's preceding night target.
    periods = (
        _climate_period("day", "06:00", "18:00", 24.0, ramp_minutes=30),
        _climate_period("night", "18:00", "06:00", 20.0),
    )
    full_window = TimelineWindow.daily(datetime(2026, 7, 1, 12, tzinfo=UTC))
    full = await _project_profile(full_window, periods)
    clipped_window = TimelineWindow.rolling(datetime(2026, 7, 1, 10, 15, tzinfo=UTC))
    clipped = await _project_profile(clipped_window, periods)
    assert full is not None
    assert clipped is not None

    # When: the saved profile is projected both across the whole local day and from mid-ramp.
    full_ramp = next(
        segment
        for segment in full.segments
        if segment.metric == "heating"
        and segment.trajectory_kind == "scheduled"
        and segment.shape == "linear"
        and segment.source.period.period_id == "day"
    )
    clipped_ramp = next(
        segment
        for segment in clipped.segments
        if segment.metric == "heating"
        and segment.trajectory_kind == "scheduled"
        and segment.shape == "linear"
        and segment.source.period.period_id == "day"
        and segment.start == clipped_window.start
    )

    # Then: its clipped first value equals full-recurrence sampling at the same instant.
    elapsed = (clipped_window.start - full_ramp.start).total_seconds()
    duration = (full_ramp.end - full_ramp.start).total_seconds()
    expected_start = full_ramp.start_value + (
        (full_ramp.end_value - full_ramp.start_value) * elapsed / duration
    )
    assert clipped_ramp.start_value == expected_start == 22.0
    assert clipped_ramp.end_value == full_ramp.end_value == 24.0
    post_ramp = next(
        segment
        for segment in clipped.segments
        if isinstance(segment, StepTrajectorySegment)
        and segment.source.period.period_id == "day"
        and segment.start == full_ramp.end
    )
    assert post_ramp.value == 24.0
    assert (
        clipped_ramp.source.mode,
        clipped_ramp.source.submode,
        clipped_ramp.source.config_revision,
    ) == ("2", "4", "cfg-9")


@pytest.mark.asyncio
async def test_spring_gap_reversed_occurrence_is_skipped_without_shifting_valid_end() -> None:
    # Given: a valid 00:00–02:00 occurrence and a 02:30–03:00 row on Toronto's spring-gap day.
    window = TimelineWindow.daily(datetime(2026, 3, 8, 12, tzinfo=UTC))
    trajectory = await _project_profile(
        window,
        (
            _climate_period("before-gap", "00:00", "02:00", 18.0),
            _climate_period("reversed", "02:30", "03:00", 19.0),
        ),
    )

    # When: each boundary is resolved independently and the reversed interval is discarded.
    assert trajectory is not None
    before_gap = next(
        segment
        for segment in trajectory.segments
        if isinstance(segment, StepTrajectorySegment)
        and segment.source.period.period_id == "before-gap"
    )
    gap = next(
        segment
        for segment in trajectory.segments
        if segment.metric == "heating"
        and segment.trajectory_kind == "scheduled"
        and segment.shape == "unavailable"
    )

    # Then: 02:00 moves to 03:00 local; it is not stretched to 04:00 to fit the bad next row.
    assert (before_gap.start, before_gap.end) == (
        datetime(2026, 3, 8, 5, tzinfo=UTC),
        datetime(2026, 3, 8, 7, tzinfo=UTC),
    )
    assert (gap.start, gap.end) == (before_gap.end, window.end)
    assert not any(
        segment.source.period.period_id == "reversed" for segment in trajectory.segments
    )
    assert any(
        warning.code == "profile.dst_assumption"
        and warning.detail.startswith("gap-forward: before-gap")
        for warning in trajectory.warnings
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("start", ["not-an-instant", "2026-03-08T00:00:00"])
async def test_preview_rejects_malformed_or_naive_window_with_422(start: str) -> None:
    service, source = _service()
    payload = _payload()
    payload["window"] = {
        "start": start,
        "end": "2026-03-09T00:00:00Z",
        "timezone": "UTC",
    }
    async with AsyncClient(
        transport=ASGITransport(app=_app(service)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/climate-timeline/Veg%20Room/main/preview", json=payload
        )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_preview_window"
    assert source.write_calls == 0
