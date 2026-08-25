from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta

from app.repositories.monitoring_snapshot_types import MonitoringSnapshot, frozen, frozen_rows
from app.schemas.monitoring_models import (
    AnchorFingerprint,
    MonitoringRange,
    ProjectionRevision,
    Quality,
    RuntimeSnapshotVersion,
)
from app.services.climate_projection import LOCAL_TZ, project_climate_timelines


def _snapshot(
    *,
    location: str = "Flower Room",
    start: datetime = datetime(2026, 3, 8, tzinfo=UTC),
    end: datetime = datetime(2026, 3, 11, tzinfo=UTC),
    active: Mapping[str, object] | None = None,
    events: Sequence[Mapping[str, object]] | None = None,
    periods: Sequence[Mapping[str, object]] | None = None,
    anchors: Sequence[Mapping[str, object]] | None = None,
    anchor_quality: Quality = Quality.EXACT,
) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        range=MonitoringRange(start=start, end=end),
        location=location,
        cluster="main",
        active_mode=frozen(active or {"mode_id": 1, "submode_id": None, "mode_name": "drying"}),
        calendar_events=frozen_rows(events or []),
        calendar_applications=(),
        climate_periods=frozen_rows(
            periods
            or [
                {
                    "id": 1,
                    "start_time": "06:00",
                    "heating_setpoint": 20,
                    "cooling_setpoint": 25,
                    "vpd_setpoint": 1,
                    "co2_setpoint": 800,
                    "ramp_minutes": 30,
                },
                {
                    "id": 2,
                    "start_time": "18:00",
                    "heating_setpoint": 18,
                    "cooling_setpoint": 27,
                    "vpd_setpoint": 1.2,
                    "co2_setpoint": 500,
                    "ramp_minutes": 30,
                },
            ]
        ),
        mode_parameters=None,
        light_targets=(),
        light_programs=(),
        expected_lights=(),
        effective_setpoint_predecessors=(),
        ramp_anchors=frozen_rows(anchors or []),
        automation_state_predecessors=(),
        photoperiod_predecessor=None,
        source_cursors=(),
        projection_revision=ProjectionRevision("revision"),
        anchor_fingerprint=AnchorFingerprint("anchor"),
        anchor_observed_at=start,
        anchor_quality=anchor_quality,
        anchor_valid_until=end,
        runtime_snapshot_version=RuntimeSnapshotVersion(1),
    )


def _point(snapshot: MonitoringSnapshot, metric: str, now: datetime):
    series = {item.name: item for item in project_climate_timelines(snapshot, lambda: now)}
    return next(point for point in series[metric].points if point.timestamp == now)


def test_calendar_phase_uses_highest_phase_order_at_local_midnight() -> None:
    when = datetime(2026, 3, 9, 4, tzinfo=UTC)
    events = [
        {
            "id": 1,
            "start_date": date(2026, 3, 8),
            "end_date": date(2026, 3, 10),
            "phase_order": 1,
            "target_mode_id": 2,
        },
        {
            "id": 2,
            "start_date": date(2026, 3, 8),
            "end_date": date(2026, 3, 10),
            "phase_order": 2,
            "target_mode_id": 3,
        },
    ]
    point = _point(_snapshot(events=events), "heating", when)
    assert point.mode == "3"
    assert point.provenance.quality is Quality.UNAVAILABLE


def test_disabled_auto_does_not_transition_mode() -> None:
    when = datetime(2026, 3, 9, 12, tzinfo=UTC)
    point = _point(
        _snapshot(
            events=[
                {
                    "id": 1,
                    "start_date": date(2026, 3, 9),
                    "end_date": date(2026, 3, 9),
                    "target_mode_id": 2,
                    "auto_mode_transition": False,
                }
            ]
        ),
        "heating",
        when,
    )
    assert point.mode == "1"
    assert point.provenance.quality is Quality.EXACT


def test_same_mode_noop_keeps_configured_period() -> None:
    when = datetime(2026, 3, 9, 12, tzinfo=UTC)
    point = _point(
        _snapshot(
            events=[
                {
                    "id": 1,
                    "start_date": date(2026, 3, 9),
                    "end_date": date(2026, 3, 9),
                    "target_mode_id": 1,
                }
            ]
        ),
        "heating",
        when,
    )
    assert point.value == 20
    assert point.provenance.quality is Quality.EXACT


def test_drying_fallback_transitions_to_veg_after_last_plan() -> None:
    when = datetime(2026, 3, 10, 12, tzinfo=UTC)
    events = [
        {
            "id": 1,
            "start_date": date(2026, 3, 1),
            "end_date": date(2026, 3, 9),
            "target_mode_id": 2,
            "target_mode_name": "veg",
        }
    ]
    point = _point(_snapshot(events=events), "heating", when)
    assert point.mode == "2"
    assert point.provenance.quality is Quality.UNAVAILABLE


def test_veg_stays_current_despite_flower_calendar_events() -> None:
    when = datetime(2026, 3, 9, 12, tzinfo=UTC)
    point = _point(
        _snapshot(
            location="Veg Room",
            events=[
                {
                    "id": 1,
                    "start_date": date(2026, 3, 9),
                    "end_date": date(2026, 3, 9),
                    "target_mode_id": 2,
                }
            ],
        ),
        "heating",
        when,
    )
    assert point.mode == "1"
    assert point.value == 20


def test_ramp_threshold_skips_heating_but_interpolates_vpd() -> None:
    when = datetime(2026, 3, 9, 14, 15, tzinfo=UTC)
    periods = [
        {
            "id": 1,
            "start_time": "09:00",
            "heating_setpoint": 20,
            "cooling_setpoint": 25,
            "vpd_setpoint": 1,
            "co2_setpoint": 800,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "start_time": "10:00",
            "heating_setpoint": 20.05,
            "cooling_setpoint": 25,
            "vpd_setpoint": 1.02,
            "co2_setpoint": 800,
            "ramp_minutes": 30,
        },
    ]
    snapshot = _snapshot(start=datetime(2026, 3, 9, 14, 5, tzinfo=UTC), periods=periods)
    assert _point(snapshot, "heating", when).value == 20.05
    assert _point(snapshot, "vpd", when).value == 1.01


def test_missing_coverage_unavailable_for_future_mode_transition() -> None:
    when = datetime(2026, 3, 9, 12, tzinfo=UTC)
    point = _point(
        _snapshot(
            events=[
                {
                    "id": 1,
                    "start_date": date(2026, 3, 9),
                    "end_date": date(2026, 3, 9),
                    "target_mode_id": 2,
                }
            ]
        ),
        "co2",
        when,
    )
    assert point.value is None
    assert point.provenance.quality is Quality.UNAVAILABLE


def test_missing_anchor_is_estimated_when_snapshot_marks_it_estimated() -> None:
    when = datetime(2026, 3, 9, 12, tzinfo=UTC)
    point = _point(_snapshot(anchor_quality=Quality.ESTIMATED), "heating", when)
    assert point.provenance.quality is Quality.ESTIMATED


def test_stale_anchor_downgrades_crossing_now_ramp() -> None:
    when = datetime(2026, 3, 9, 12, tzinfo=UTC)
    anchor = {
        "setpoint_type": "heating",
        "start_value": 10,
        "target_value": 20,
        "duration_minutes": 20,
        "start_time": when - timedelta(minutes=10),
    }
    point = _point(_snapshot(anchors=[anchor], anchor_quality=Quality.ESTIMATED), "heating", when)
    assert point.value == 15
    assert point.provenance.quality is Quality.ESTIMATED


def test_dst_spring_forward_and_fall_back_use_toronto_midnights() -> None:
    spring = _snapshot(
        start=datetime(2026, 3, 8, tzinfo=UTC), end=datetime(2026, 3, 11, tzinfo=UTC)
    )
    fall = _snapshot(
        start=datetime(2026, 10, 31, tzinfo=UTC), end=datetime(2026, 11, 3, tzinfo=UTC)
    )
    spring_midnights = [
        point.timestamp
        for point in project_climate_timelines(spring, lambda: spring.range.start)[0].points
        if point.timestamp.astimezone(LOCAL_TZ).hour == 0
    ]
    fall_midnights = [
        point.timestamp
        for point in project_climate_timelines(fall, lambda: fall.range.start)[0].points
        if point.timestamp.astimezone(LOCAL_TZ).hour == 0
    ]
    assert any(
        later - earlier == timedelta(hours=23)
        for earlier, later in zip(spring_midnights, spring_midnights[1:], strict=False)
    )
    assert any(
        later - earlier == timedelta(hours=25)
        for earlier, later in zip(fall_midnights, fall_midnights[1:], strict=False)
    )
