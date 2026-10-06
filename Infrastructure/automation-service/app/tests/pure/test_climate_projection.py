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
                    "mode_id": 1,
                    "submode_id": None,
                    "period_name": "day",
                    "start_time": "06:00",
                    "end_time": "18:00",
                    "heating_setpoint": 20,
                    "cooling_setpoint": 25,
                    "vpd_setpoint": 1,
                    "co2_setpoint": 800,
                    "ramp_minutes": 30,
                },
                {
                    "id": 2,
                    "mode_id": 1,
                    "submode_id": None,
                    "period_name": "night",
                    "start_time": "18:00",
                    "end_time": "06:00",
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


def _value_at(
    snapshot: MonitoringSnapshot, metric: str, when: datetime, now: datetime
) -> tuple[float | None, Quality]:
    series = {item.name: item for item in project_climate_timelines(snapshot, lambda: now)}
    point = next(point for point in series[metric].points if point.timestamp == when)
    return point.value, point.provenance.quality


def test_calendar_phase_uses_highest_phase_order_at_local_midnight() -> None:
    when = datetime(2026, 3, 9, 4, tzinfo=UTC)
    events = [
        {
            "id": 1,
            "start_date": date(2026, 3, 8),
            "end_date": date(2026, 3, 10),
            "phase_order": 1,
            "target_mode_id": 2,
            "destination_configured": True,
        },
        {
            "id": 2,
            "start_date": date(2026, 3, 8),
            "end_date": date(2026, 3, 10),
            "phase_order": 2,
            "target_mode_id": 3,
            "destination_configured": True,
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
                    "target_submode_id": None,
                    "destination_configured": True,
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
            "destination_configured": True,
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


def test_inactive_profile_periods_do_not_contaminate_active_forecast() -> None:
    # Given: active profile (1, base) heating 22 and an inactive profile (2, base)
    # heating 18 that share the same 06:00 clock.
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 22,
            "ramp_minutes": 0,
        },
        {
            "id": 2,
            "mode_id": 2,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    ]
    snapshot = _snapshot(
        active={"mode_id": 1, "submode_id": None, "mode_name": "veg"},
        periods=periods,
        start=datetime(2026, 6, 1, 10, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, tzinfo=UTC),
    )
    point = _point(snapshot, "heating", datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    assert point.value == 22
    assert point.nominal_value == 22


def test_unconfigured_destination_falls_back_to_active() -> None:
    # Given: a resolved destination without persisted mode parameters; the
    # calendar cannot activate it, so the projection keeps the active profile.
    periods = (
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 22,
            "ramp_minutes": 0,
        },
        {
            "id": 2,
            "mode_id": 2,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    )
    snapshot = _snapshot(
        active={"mode_id": 1, "submode_id": None, "mode_name": "veg"},
        events=[
            {
                "id": 5,
                "start_date": date(2026, 6, 2),
                "end_date": date(2026, 6, 2),
                "phase_order": 4,
                "target_mode_id": 2,
                "target_submode_id": None,
                "auto_mode_transition": True,
                "destination_configured": False,
            }
        ],
        periods=periods,
        start=datetime(2026, 6, 1, 10, tzinfo=UTC),
        end=datetime(2026, 6, 3, 10, tzinfo=UTC),
    )
    point = _point(snapshot, "heating", datetime(2026, 6, 2, 10, 0, tzinfo=UTC))
    assert point.mode == "1"
    assert point.value == 22


def test_unscoped_rows_never_substitute_known_active_identity() -> None:
    # Given: legacy period rows without any profile identity; a known active
    # identity must never fall back to them.
    periods = [
        {
            "id": 1,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 22,
            "ramp_minutes": 0,
        }
    ]
    snapshot = _snapshot(
        active={"mode_id": 1, "submode_id": None, "mode_name": "veg"},
        periods=periods,
        start=datetime(2026, 6, 1, 10, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, tzinfo=UTC),
    )
    point = _point(snapshot, "heating", datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    assert point.value is None
    assert point.nominal_value is None
    assert point.provenance.quality is Quality.UNAVAILABLE


def test_calendar_destination_projects_its_exact_stored_profile() -> None:
    # Given: a configured calendar destination (2, base) with stored heating 18;
    # the active-only gate is gone, so its exact profile is projectable.
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 22,
            "ramp_minutes": 0,
        },
        {
            "id": 2,
            "mode_id": 2,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    ]
    snapshot = _snapshot(
        active={"mode_id": 1, "submode_id": None, "mode_name": "veg"},
        events=[
            {
                "id": 5,
                "start_date": date(2026, 6, 2),
                "end_date": date(2026, 6, 2),
                "phase_order": 4,
                "target_mode_id": 2,
                "target_submode_id": None,
                "auto_mode_transition": True,
                "destination_configured": True,
            }
        ],
        periods=periods,
        start=datetime(2026, 6, 1, 10, tzinfo=UTC),
        end=datetime(2026, 6, 3, 10, tzinfo=UTC),
    )
    point = _point(snapshot, "heating", datetime(2026, 6, 2, 10, 0, tzinfo=UTC))
    assert point.mode == "2"
    assert point.value == 18


def test_same_mode_different_submode_periods_do_not_cross_match() -> None:
    # Given: base and stretch profiles of the same mode keep separate rows; the
    # base projection must never pick the stretch row at the shared clock.
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 22,
            "ramp_minutes": 0,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": 2,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    ]
    snapshot = _snapshot(
        active={"mode_id": 1, "submode_id": None, "mode_name": "flower"},
        periods=periods,
        start=datetime(2026, 6, 1, 10, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, tzinfo=UTC),
    )
    assert _point(snapshot, "heating", datetime(2026, 6, 1, 10, 0, tzinfo=UTC)).value == 22
    destination = _snapshot(
        active={"mode_id": 1, "submode_id": None, "mode_name": "flower"},
        events=[
            {
                "id": 6,
                "start_date": date(2026, 6, 1),
                "end_date": date(2026, 6, 1),
                "phase_order": 4,
                "target_mode_id": 1,
                "target_submode_id": 2,
                "destination_configured": True,
            }
        ],
        periods=periods,
        start=datetime(2026, 6, 1, 10, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, tzinfo=UTC),
    )
    assert _point(destination, "heating", datetime(2026, 6, 1, 10, 0, tzinfo=UTC)).value == 18


def test_period_gap_is_unavailable_not_held() -> None:
    # Given: a covered morning, a covered overnight tail, and an uncovered
    # afternoon gap; the gap returns None instead of holding a neighbour row.
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "12:00",
            "heating_setpoint": 20,
            "ramp_minutes": 0,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    ]
    snapshot = _snapshot(
        periods=periods,
        start=datetime(2026, 6, 1, 7, tzinfo=UTC),
        end=datetime(2026, 6, 2, 7, tzinfo=UTC),
    )
    covered, quality = _value_at(
        snapshot, "heating", datetime(2026, 6, 1, 10, 0, tzinfo=UTC), datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    )
    assert (covered, quality) == (20, Quality.EXACT)
    # The gap's first instant (day end 12:00 Toronto = 16:00Z) is a boundary and
    # is unavailable; the projection never holds a neighbour row across the gap.
    gap, gap_quality = _value_at(
        snapshot, "heating", datetime(2026, 6, 1, 16, 0, tzinfo=UTC), datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    )
    assert gap is None
    assert gap_quality is Quality.UNAVAILABLE


def test_scheduled_ramp_midpoint_estimates_between_targets() -> None:
    # Given: a 20→24 scheduled ramp over 30 minutes; the canonical estimate at
    # minute 15 is 22, not a held minute-zero value through the entire ramp.
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 20,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 24,
            "ramp_minutes": 30,
        },
    ]
    snapshot = _snapshot(
        periods=periods,
        start=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
    )
    midpoint, quality = _value_at(snapshot, "heating", datetime(2026, 6, 1, 10, 15, tzinfo=UTC), datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    assert midpoint == 22
    assert quality is Quality.EXACT
    start_value, _ = _value_at(snapshot, "heating", datetime(2026, 6, 1, 10, 0, tzinfo=UTC), datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    assert start_value == 20
    end_value, _ = _value_at(snapshot, "heating", datetime(2026, 6, 1, 10, 30, tzinfo=UTC), datetime(2026, 6, 1, 10, 0, tzinfo=UTC))
    assert end_value == 24


def test_overnight_tail_uses_previous_date_start() -> None:
    # Given: an overnight night period; instants after local midnight interpolate
    # from the previous local date's 18:00 occurrence, and post-ramp holds target.
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 20,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 18,
            "ramp_minutes": 30,
        },
    ]
    snapshot = _snapshot(
        periods=periods,
        start=datetime(2026, 6, 1, 7, tzinfo=UTC),
        end=datetime(2026, 6, 2, 8, tzinfo=UTC),
    )
    # Local midnight (06-02 00:00 Toronto = 06:00Z... adjusted for EDT: 04:00Z)
    # is inside the overnight night tail started on the previous local date.
    tail, _ = _value_at(snapshot, "heating", datetime(2026, 6, 2, 4, 0, tzinfo=UTC), datetime(2026, 6, 1, 10, tzinfo=UTC))
    assert tail == 18
    mid_ramp, _ = _value_at(snapshot, "heating", datetime(2026, 6, 1, 22, 15, tzinfo=UTC), datetime(2026, 6, 1, 10, tzinfo=UTC))
    assert mid_ramp == 19


def test_ramp_threshold_skips_heating_but_interpolates_vpd() -> None:
    when = datetime(2026, 3, 9, 14, 15, tzinfo=UTC)
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "morning",
            "start_time": "09:00",
            "end_time": "10:00",
            "heating_setpoint": 20,
            "cooling_setpoint": 25,
            "vpd_setpoint": 1,
            "co2_setpoint": 800,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "late",
            "start_time": "10:00",
            "end_time": "18:00",
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
                    "target_submode_id": None,
                    "destination_configured": True,
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
        "mode_id": 1,
        "submode_id": None,
    }
    point = _point(_snapshot(anchors=[anchor], anchor_quality=Quality.ESTIMATED), "heating", when)
    assert point.value == 15
    assert point.provenance.quality is Quality.ESTIMATED


def test_live_anchor_governs_until_end_then_holds_nominal() -> None:
    # Given: an in-flight compatible live anchor 21→24 over the remaining 15
    # minutes of the executing period's scheduled ramp window.
    when = datetime(2026, 6, 1, 10, 15, tzinfo=UTC)
    anchor = {
        "setpoint_type": "heating",
        "start_value": 21,
        "target_value": 24,
        "start_time": when,
        "duration_minutes": 15,
        "mode_id": 1,
        "submode_id": None,
    }
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 24,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 20,
            "ramp_minutes": 30,
        },
    ]
    snapshot = _snapshot(
        anchors=[anchor],
        periods=periods,
        start=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 11, 0, tzinfo=UTC),
    )
    assert _value_at(snapshot, "heating", when, when) == (21, Quality.EXACT)
    soon, _ = _value_at(snapshot, "heating", when + timedelta(minutes=1), when)
    assert abs(soon - 21.2) < 1e-9
    ended, _ = _value_at(snapshot, "heating", when + timedelta(minutes=15), when)
    assert ended == 24
    # The nominal hold continued through the day: at the day/night boundary the
    # value is still the held 24 before the night ramp starts from it.
    held, _ = _value_at(snapshot, "heating", datetime(2026, 6, 1, 22, 0, tzinfo=UTC), when)
    assert held == 24
    # The next local occurrence keeps its own scheduled ramp; the completed
    # anchor never leaks into it.
    next_day, _ = _value_at(
        snapshot, "heating", datetime(2026, 6, 2, 10, 15, tzinfo=UTC), when
    )
    assert next_day == 22


def test_no_ramp_anchor_holds_nominal_inside_scheduled_ramp_window() -> None:
    # Given: the executing period has no active ramp (remaining zero); the
    # projection holds the actual nominal instead of inventing the scheduled ramp.
    when = datetime(2026, 6, 1, 10, 15, tzinfo=UTC)
    anchor = {
        "setpoint_type": "heating",
        "start_value": 24,
        "target_value": 24,
        "start_time": when,
        "duration_minutes": 0,
        "mode_id": 1,
        "submode_id": None,
    }
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 24,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 20,
            "ramp_minutes": 30,
        },
    ]
    snapshot = _snapshot(
        anchors=[anchor],
        periods=periods,
        start=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
    )
    held, _ = _value_at(snapshot, "heating", when, when)
    assert held == 24
    held_minute, _ = _value_at(snapshot, "heating", when + timedelta(minutes=1), when)
    assert held_minute == 24


def test_early_ended_anchor_holds_nominal_until_next_period_boundary() -> None:
    # Given: an in-flight ramp that ends at 10:25Z while the scheduled ramp window
    # runs until 10:30Z; the nominal holds from the anchor end onward.
    when = datetime(2026, 6, 1, 10, 15, tzinfo=UTC)
    anchor = {
        "setpoint_type": "heating",
        "start_value": 23,
        "target_value": 24,
        "start_time": when,
        "duration_minutes": 10,
        "mode_id": 1,
        "submode_id": None,
    }
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 24,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "night",
            "start_time": "18:00",
            "end_time": "06:00",
            "heating_setpoint": 20,
            "ramp_minutes": 30,
        },
    ]
    snapshot = _snapshot(
        anchors=[anchor],
        periods=periods,
        start=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 10, 0, tzinfo=UTC),
    )
    mid, _ = _value_at(snapshot, "heating", when + timedelta(minutes=5), when)
    assert mid == 23.5
    ended_early, _ = _value_at(snapshot, "heating", when + timedelta(minutes=10), when)
    assert ended_early == 24
    still_holding, _ = _value_at(snapshot, "heating", when + timedelta(minutes=14), when)
    assert still_holding == 24


def test_live_anchor_does_not_leak_into_calendar_destination() -> None:
    # Given: the same live anchor; a calendar destination with another profile
    # must keep its own configured value, never inherit the anchor.
    when = datetime(2026, 6, 1, 10, 15, tzinfo=UTC)
    anchor = {
        "setpoint_type": "heating",
        "start_value": 21,
        "target_value": 24,
        "start_time": when,
        "duration_minutes": 15,
        "mode_id": 1,
        "submode_id": None,
    }
    periods = [
        {
            "id": 1,
            "mode_id": 1,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 24,
            "ramp_minutes": 30,
        },
        {
            "id": 2,
            "mode_id": 2,
            "submode_id": None,
            "period_name": "day",
            "start_time": "06:00",
            "end_time": "18:00",
            "heating_setpoint": 18,
            "ramp_minutes": 0,
        },
    ]
    snapshot = _snapshot(
        anchors=[anchor],
        events=[
            {
                "id": 7,
                "start_date": date(2026, 6, 2),
                "end_date": date(2026, 6, 2),
                "phase_order": 4,
                "target_mode_id": 2,
                "target_submode_id": None,
                "destination_configured": True,
            }
        ],
        periods=periods,
        start=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        end=datetime(2026, 6, 2, 11, 0, tzinfo=UTC),
    )
    destination, _ = _value_at(
        snapshot, "heating", datetime(2026, 6, 2, 10, 0, tzinfo=UTC), when
    )
    assert destination == 18


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
