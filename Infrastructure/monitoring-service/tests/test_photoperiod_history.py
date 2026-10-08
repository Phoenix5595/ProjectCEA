"""Recorded photoperiod history reconstruction from committed room facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import final

import pytest

from monitoring_service.control_models import (
    ControlHistoryRange,
    PhotoperiodTimelinePointOut,
)
from monitoring_service.control_repository import ControlHistoryRepository
from monitoring_service.photoperiod_history import build_photoperiod_history

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _range(start: datetime, end: datetime) -> ControlHistoryRange:
    return ControlHistoryRange(start=start, end=end)


def _m(minutes: int) -> datetime:
    """NOW plus a minute offset."""
    return NOW + timedelta(minutes=minutes)


def _s(seconds: int) -> datetime:
    """NOW plus a second offset."""
    return NOW + timedelta(seconds=seconds)


def _h(hours: int) -> datetime:
    """NOW plus an hour offset."""
    return NOW + timedelta(hours=hours)


def _transition(
    ident: int,
    observed_at: datetime,
    phase: str,
    *,
    mode_id: int | None = 3,
    submode_id: int | None = None,
    version: int | None = 2,
    source: str = "photoperiod_transition",
) -> dict[str, str | int | datetime | None]:
    """One committed monitoring_room_photoperiod row of either stream."""
    return {
        "id": ident,
        "observed_at": observed_at,
        "phase": phase,
        "mode_id": mode_id,
        "submode_id": submode_id,
        "runtime_snapshot_version": version,
        "source": source,
    }


def _coverage(
    ident: int, observed_at: datetime, state: str
) -> dict[str, str | int | datetime]:
    return {
        "id": ident,
        "observed_at": observed_at,
        "location": "Flower Room",
        "cluster": "main",
        "state": state,
        "reason": "initial",
        "runtime_snapshot_version": 1,
    }


def _light_sample(
    observed_at: datetime,
    device_name: str,
    mode: str,
    value: float | None,
    *,
    cluster: str = "main",
) -> dict[str, str | float | datetime | None]:
    return {
        "timestamp": observed_at,
        "cluster": cluster,
        "device_name": device_name,
        "mode": mode,
        "effective_light_intensity": value,
        "nominal_light_intensity": value,
        "ramp_progress_light": None,
    }


def _shape(point: PhotoperiodTimelinePointOut) -> tuple:
    return (
        point.timestamp,
        point.phase,
        point.provenance.origin,
        point.provenance.quality.value,
        point.mode_id,
        point.submode_id,
        point.runtime_snapshot_version,
    )


def _shapes(points: tuple[PhotoperiodTimelinePointOut, ...]) -> list[tuple]:
    return [_shape(point) for point in points]


def test_empty_evidence_yields_explicit_unknown_without_schedule_fallback() -> None:
    # Given: no committed phase, coverage, or retained light facts at all.
    # When: the photoperiod history is assembled for a recorded window.
    # Then: the read anchors to one explicit unavailable start boundary instead
    # of extrapolating the current schedule.
    points, versions = build_photoperiod_history(_range(_m(-60), _h(60)), (), (), [])

    assert _shapes(points) == [
        (_m(-60), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]
    assert versions == []


def test_new_phase_transition_requires_available_coverage() -> None:
    # Given: one committed transition with no explicit availability fact.
    # When: the span is assembled while coverage is unknown.
    # Then: the seeded transition cannot establish authority.
    points, _ = build_photoperiod_history(
        _range(_m(-1), _h(1)), [_transition(1, NOW, "SUN")], (), []
    )

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]


def test_available_coverage_holds_phase_without_heartbeat_rows() -> None:
    # Given: one available boundary, a first SUN commit, then MOON and SUN one
    # second apart through a day whose remaining minutes are unrecorded.
    # When: the history is assembled from only these committed rows.
    # Then: every gap stays covered by the held transition with no heartbeat.
    rows = [
        _transition(10, NOW, "SUN"),
        _transition(11, _h(2), "MOON"),
        _transition(12, _h(2) + timedelta(seconds=1), "SUN"),
    ]
    points, versions = build_photoperiod_history(
        _range(NOW, _h(24)), rows, [_coverage(1, NOW, "available")], []
    )

    assert [(point.phase, point.timestamp) for point in points] == [
        ("SUN", NOW),
        ("MOON", _h(2)),
        ("SUN", _h(2) + timedelta(seconds=1)),
    ]
    assert versions == [2]
    for point in points:
        assert point.provenance.origin == "recorded"
        assert point.provenance.quality == "exact"
        assert point.provenance.is_aggregated is False


def test_unavailable_coverage_interrupts_the_recorded_phase() -> None:
    # Given: coverage closes thirty minutes after the SUN commit.
    # When: the transition authority no longer has available coverage.
    # Then: it degrades to an explicit unavailable span with null metadata.
    rows = [_transition(10, NOW, "SUN")]
    coverage = [
        _coverage(1, NOW, "available"),
        _coverage(2, _m(30), "unavailable"),
    ]
    points, _ = build_photoperiod_history(_range(NOW, _h(1)), rows, coverage, [])

    assert _shapes(points) == [
        (NOW, "SUN", "recorded", "exact", 3, None, 2),
        (_m(30), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]


def test_outage_cutoff_rejects_preoutage_light_witnesses() -> None:
    # Given: a retired light witnessed SUN ten seconds before the outage inside
    # its own still-unexpired 60-second sample.
    # When: only observers at or after the outage boundary may re-establish.
    # Then: the ten-second-old stale witness cannot fill the outage span.
    rows = [_transition(10, NOW, "SUN")]
    coverage = [
        _coverage(1, NOW, "available"),
        _coverage(2, _m(30), "unavailable"),
    ]
    lights = [_light_sample(_s(30 * 60 - 10), "dac_a", "SUN", 40.0)]
    points, _ = build_photoperiod_history(_range(NOW, _h(1)), rows, coverage, lights)

    assert _shapes(points) == [
        (NOW, "SUN", "recorded", "exact", 3, None, 2),
        (_m(30), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]


def test_witness_after_outage_boundary_reestablishes_derived_phase() -> None:
    # Given: a retained light sample observed one minute after the outage start.
    # When: it supports an unexpired 60-second agreement window.
    # Then: that post-outage span alone re-establishes derived SUN.
    rows = [_transition(10, NOW, "SUN")]
    coverage = [
        _coverage(1, NOW, "available"),
        _coverage(2, _m(30), "unavailable"),
    ]
    lights = [_light_sample(_m(31), "dac_a", "SUN", 60.0)]
    points, _ = build_photoperiod_history(_range(NOW, _h(1)), rows, coverage, lights)

    assert [(point.phase, point.provenance.quality) for point in points] == [
        ("SUN", "exact"),
        ("UNKNOWN", "unavailable"),
        ("SUN", "estimated"),
        ("UNKNOWN", "unavailable"),
    ]
    assert [point.timestamp for point in points] == [
        NOW,
        _m(30),
        _m(31),
        _m(32),
    ]


def test_legacy_room_sample_supports_sixty_seconds_then_expires() -> None:
    # Given: one old dedicated sample from the incomplete legacy producer.
    # When: no transition or coverage authority exists around it.
    # Then: the sample owns exactly [observed_at, observed_at+60s), not infinity.
    rows = [_transition(20, NOW, "MOON", mode_id=1, version=2, source="photoperiod")]
    points, versions = build_photoperiod_history(_range(_m(-1), _h(1)), rows, (), [])

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None),
        (NOW, "MOON", "recorded", "exact", 1, None, 2),
        (_s(60), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]
    assert versions == [2]


def test_legacy_sample_tail_is_truncated_half_open_by_its_successor() -> None:
    # Given: a successor legacy sample observed one second before the
    # predecessor's nominal support ends.
    # When: the sweep truncates the overlapping spans at the successor instant.
    # Then: that instant alone belongs to the newer row, never to both.
    rows = [
        _transition(20, NOW, "SUN", source="photoperiod"),
        _transition(21, _s(59), "MOON", source="photoperiod"),
    ]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), rows, (), [])

    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _m(-1)),
        ("SUN", NOW),
        ("MOON", _s(59)),
        ("UNKNOWN", _s(119)),
    ]


def test_legacy_predecessor_anchors_start_only_within_its_window() -> None:
    # Given: a legacy sample thirty seconds before the requested start.
    # When: carry-in covers only unsupported spans.
    # Then: the anchor holds until that sample's own support expires.
    rows = [
        _transition(5, _s(-30), "MOON", mode_id=1, version=2, source="photoperiod")
    ]
    points, _ = build_photoperiod_history(_range(NOW, _h(1)), rows, (), [])

    assert _shapes(points) == [
        (NOW, "MOON", "recorded", "exact", 1, None, 2),
        (_s(30), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]


def test_light_consensus_supports_retired_devices_within_their_window() -> None:
    # Given: retained raw main-cluster setpoints of devices no longer registered
    # by today's automation topology.
    # When: their modes agree SUN with finite effective intensities.
    # Then: consensus becomes derived SUN up to the last sample's 60-second arm.
    lights = [
        _light_sample(NOW, "retired_dac_a", "SUN", 90.0),
        _light_sample(_s(10), "retired_dac_b", "SUN", 88.0),
        _light_sample(_s(20), "retired_dac_a", "SUN", 84.0),
    ]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)

    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _m(-1)),
        ("SUN", NOW),
        ("UNKNOWN", _s(80)),
    ]
    derived = points[1]
    assert derived.provenance.origin == "derived"
    assert derived.provenance.quality == "estimated"
    assert derived.provenance.is_aggregated is False
    assert derived.mode_id is None
    assert derived.submode_id is None
    assert derived.runtime_snapshot_version is None


def test_same_phase_distinct_devices_remain_independent_witnesses() -> None:
    # Given: two devices witness the same phase; one stops contributing at +30s.
    # When: the other still supports the phase afterwards.
    # Then: any single remaining valid witness sustains consensus.
    lights = [
        _light_sample(NOW, "dac_a", "SUN", 90.0),
        _light_sample(NOW, "dac_b", "SUN", 90.0),
        _light_sample(_s(30), "dac_b", "SUN", 20.0),
    ]
    points, _ = build_photoperiod_history(_range(_s(-10), _h(1)), (), (), lights)

    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _s(-10)),
        ("SUN", NOW),
        ("UNKNOWN", _s(90)),
    ]


def test_newest_device_sample_replaces_its_older_witness() -> None:
    # Given: one device witnesses SUN and later commits MOON inside its own
    # unexpired support while an agreeing peer turns MOON in between.
    # When: each identity's latest sample is the only one kept active.
    # Then: the transition is MOON exactly at the newest sample instead of an
    # unknown span propped up by that device's lingering predecessor.
    lights = [
        _light_sample(NOW, "dac_a", "SUN", 90.0),
        _light_sample(_s(20), "dac_b", "MOON", 0.0),
        _light_sample(_s(30), "dac_a", "MOON", 0.0),
    ]
    points, _ = build_photoperiod_history(_range(_s(-10), _h(1)), (), (), lights)

    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _s(-10)),
        ("SUN", NOW),
        ("UNKNOWN", _s(20)),
        ("MOON", _s(30)),
        ("UNKNOWN", _s(90)),
    ]


def test_off_mode_light_rows_are_not_phase_evidence() -> None:
    # Given: ordinary automation mode strings never designate a photoperiod.
    # When: only those rows exist.
    # Then: no witness is derived and the span stays an explicit unknown.
    lights = [
        _light_sample(NOW, "light_f_1", "day", 40.0),
        _light_sample(_s(10), "light_f_1", "night", 0.0),
    ]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)
    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _m(-1))
    ]


def test_zero_intensity_sun_stays_sun() -> None:
    # Given: a SUN witness with a zero effective intensity.
    # When: intensity sign is the only difference between phases.
    # Then: SUN is never degraded through the intensity value alone.
    lights = [_light_sample(NOW, "dac_a", "SUN", 0.0)]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None),
        (NOW, "SUN", "derived", "estimated", None, None, None),
        (_s(60), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]


def test_zero_intensity_moon_witness_is_valid() -> None:
    # Given: an explicit MOON witness whose dimmer is fully off.
    # When: MOON's phase proven holds with zero effective intensity.
    # Then: the span supports estimated MOON.
    lights = [_light_sample(NOW, "dac_a", "MOON", 0.0)]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)

    assert [(point.phase, point.provenance.quality) for point in points] == [
        ("UNKNOWN", "unavailable"),
        ("MOON", "estimated"),
        ("UNKNOWN", "unavailable"),
    ]


def test_moon_nonzero_intensity_invalidates_its_witness() -> None:
    # Given: an explicit MOON witnessed with a lit dimming level.
    # When: the combined sample contradicts itself.
    # Then: that active invalid witness collapses the span to an UNKNOWN one.
    lights = [_light_sample(NOW, "dac_a", "MOON", 12.5)]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]


def test_conflicting_supported_witnesses_resolve_only_their_own_spans() -> None:
    # Given: a legacy SUN sample and a MOON light row whose windows overlap and
    # then differ.
    # When: both spans stay supported; the SUN sample expires first.
    # Then: the disagreement is UNKNOWN, and the surviving witness alone may
    # speak once the other support ends.
    rows = [_transition(20, NOW, "SUN", source="photoperiod")]
    lights = [_light_sample(_s(30), "dac_a", "MOON", 0.0)]
    points, _ = build_photoperiod_history(_range(NOW, _m(2)), rows, (), lights)

    assert _shapes(points) == [
        (NOW, "SUN", "recorded", "exact", 3, None, 2),
        (_s(30), "UNKNOWN", "recorded", "unavailable", None, None, None),
        (_s(60), "MOON", "derived", "estimated", None, None, None),
        (_s(90), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]


def test_tied_contradictory_light_rows_return_unknown() -> None:
    # Given: one device logs SUN and MOON at one tied timestamp.
    # When: the group cannot agree on a witness.
    # Then: that span stays unknown instead of picking a row.
    lights = [
        _light_sample(NOW, "dac_a", "SUN", 90.0),
        _light_sample(NOW, "dac_a", "MOON", 4.0),
    ]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]


def test_tied_contradictory_legacy_samples_return_unknown() -> None:
    # Given: two dedicated legacy rows share one timestamp with different phases.
    # When: no coverage authority exists to arbitrate.
    # Then: neither row is arbitrarily preferred.
    rows = [
        _transition(20, NOW, "SUN", source="photoperiod"),
        _transition(21, NOW, "MOON", source="photoperiod"),
    ]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), rows, (), [])

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]


def test_missing_effective_intensity_invalidates_light_witness() -> None:
    # Given: a recognized SUN mode with no effective intensity value.
    # When: the row cannot prove its dimming state.
    # Then: the invalid witness yields an unknown span, not a guessed phase.
    lights = [_light_sample(NOW, "dac_a", "SUN", None)]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]


def test_non_canonical_light_cluster_is_not_evidence() -> None:
    # Given: retained light rows belonging to a room sensor cluster.
    # When: photoperiod assembly reads only the main cluster.
    # Then: no band is derived from other equipment.
    lights = [_light_sample(NOW, "dac_a", "SUN", 90.0, cluster="front")]
    points, _ = build_photoperiod_history(_range(_m(-1), _h(1)), (), (), lights)
    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _m(-1))
    ]


def test_unrecognized_source_cannot_establish_transition_continuity() -> None:
    # Given: a committed row whose source is not a recognized stream, next to a
    # real transition with available coverage.
    # When: the sweep reads the timeline.
    # Then: only the real transition speaks.
    rows = [
        _transition(1, NOW, "SUN", source="photoperiod_snapshot"),
        _transition(2, _h(1), "MOON"),
    ]
    coverage = [_coverage(5, NOW, "available")]
    points, versions = build_photoperiod_history(
        _range(_m(-1), _h(2)), rows, coverage, []
    )

    assert [(point.phase, point.timestamp) for point in points] == [
        ("UNKNOWN", _m(-1)),
        ("MOON", _h(1)),
    ]
    assert versions == [2]


def test_dedicated_unknown_observation_supplies_span_metadata() -> None:
    # Given: a committed UNKNOWN phase change with profile metadata present.
    # When: the span resolves through that dedicated observation.
    # Then: the metadata is preserved while the span stays unavailable.
    rows = [
        _transition(10, NOW, "UNKNOWN", mode_id=4, submode_id=None, version=5)
    ]
    points, versions = build_photoperiod_history(
        _range(_m(-1), _h(1)), rows, [_coverage(1, NOW, "available")], []
    )

    assert _shapes(points) == [
        (_m(-1), "UNKNOWN", "recorded", "unavailable", None, None, None),
        (NOW, "UNKNOWN", "recorded", "unavailable", 4, None, 5),
    ]
    assert versions == [5]


def test_transition_exactly_at_exclusive_end_is_never_emitted() -> None:
    # Given: availability begins at start and a transition commits exactly at end.
    # When: the half-open window is assembled.
    # Then: only the start anchor exists; the end event stays outside.
    rows = [_transition(2, _h(1), "SUN")]
    coverage = [_coverage(1, _m(-10), "available")]
    points, _ = build_photoperiod_history(_range(_m(-10), _h(1)), rows, coverage, [])

    assert _shapes(points) == [
        (_m(-10), "UNKNOWN", "recorded", "unavailable", None, None, None)
    ]


@final
class PhotoperiodHistoryDatabase:
    """Routes every control-history family to deterministic fixture rows."""

    def __init__(
        self,
        *,
        light_rows: tuple[dict[str, object], ...] = (),
        photoperiod_rows: tuple[dict[str, object], ...] = (),
        coverage_rows: tuple[dict[str, object], ...] = (),
        setpoint_rows: tuple[dict[str, object], ...] = (),
    ) -> None:
        self.light_rows = light_rows
        self.photoperiod_rows = photoperiod_rows
        self.coverage_rows = coverage_rows
        self.setpoint_rows = setpoint_rows
        self.queries: list[str] = []

    async def fetch(
        self, query: str, *arguments: str | int | float | datetime
    ) -> list[dict[str, object]]:
        self.queries.append(query)
        if "monitoring_photoperiod_coverage" in query:
            return list(self.coverage_rows)
        if "monitoring_room_photoperiod" in query:
            return list(self.photoperiod_rows)
        if "effective_light_intensity" in query:
            return list(self.light_rows)
        if "effective_setpoints" in query:
            return list(self.setpoint_rows)
        return []


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_repository_survives_provenance_transitions_in_budget_collapse() -> None:
    # Given: recorded SUN under available coverage, an explicit outage, then
    # agreeing post-outage light witnesses whose support expires.
    # When: the same window is read with and without a point budget.
    # Then: every phase and provenance transition survives the collapse intact.
    database = PhotoperiodHistoryDatabase(
        light_rows=(
            _light_sample(_m(12), "dac_a", "SUN", 100.0),
            _light_sample(_m(13), "dac_a", "SUN", 100.0),
            _light_sample(_m(14), "dac_a", "SUN", 100.0),
        ),
        photoperiod_rows=(_transition(10, NOW, "SUN"),),
        coverage_rows=(
            _coverage(1, NOW, "available"),
            _coverage(2, _m(10), "unavailable"),
        ),
    )
    repository = ControlHistoryRepository(database)
    history_range = _range(NOW, _m(20))

    unbudgeted = await repository.read("Flower Room", history_range)
    database.queries.clear()
    budgeted = await repository.read("Flower Room", history_range, max_points=20)

    expected = [
        (NOW, "SUN", "recorded", "exact", 3, None, 2),
        (_m(10), "UNKNOWN", "recorded", "unavailable", None, None, None),
        (_m(12), "SUN", "derived", "estimated", None, None, None),
        (_m(15), "UNKNOWN", "recorded", "unavailable", None, None, None),
    ]
    assert _shapes(unbudgeted.photoperiod) == expected
    assert unbudgeted.photoperiod == budgeted.photoperiod
    assert unbudgeted.runtime_snapshot_version == 2


@pytest.mark.anyio
async def test_repository_without_any_evidence_anchors_explicit_unknown() -> None:
    # Given: a database that returns no rows for any control-history family.
    # When: history is read for a window.
    # Then: the photoperiod section carries the explicit unavailable start
    # anchor and no committed-evidence snapshot version.
    repository = ControlHistoryRepository(PhotoperiodHistoryDatabase())
    envelope = await repository.read("Flower Room", _range(_m(-30), _m(0)))

    assert [(point.phase, point.timestamp) for point in envelope.photoperiod] == [
        ("UNKNOWN", _m(-30))
    ]
    anchor = envelope.photoperiod[0]
    assert anchor.provenance.origin == "recorded"
    assert anchor.provenance.quality == "unavailable"
    assert anchor.mode_id is None
    assert anchor.submode_id is None
    assert anchor.runtime_snapshot_version is None
    assert envelope.runtime_snapshot_version == 0
