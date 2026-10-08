"""Recorded photoperiod history assembled only from committed room evidence."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import isfinite
from typing import Final, Literal, cast

from monitoring_service.control_models import (
    ControlHistoryRange,
    ControlRecord,
    ControlRecordValue,
    PhotoperiodTimelinePointOut,
    SAMPLE_SUPPORTED_SPAN,
    TimelineProvenanceModel,
)
from shared.monitoring_contracts import Quality

Phase = Literal["SUN", "MOON", "UNKNOWN"]

# Legacy dedicated room samples and per-light setpoints witness only the window
# an actual retained observation proves. The committed transition stream carries
# no sampling bound: explicit coverage facts decide how long the recorded phase
# may still be held between real SUN/MOON commits.
_SAMPLE_SUPPORT_WINDOW: Final[timedelta] = SAMPLE_SUPPORTED_SPAN

_TRANSITION_SOURCE: Final[str] = "photoperiod_transition"
_LEGACY_SAMPLE_SOURCE: Final[str] = "photoperiod"

_ROOM_PHASES: Final[frozenset[str]] = frozenset({"SUN", "MOON", "UNKNOWN"})
_LIGHT_PHASES: Final[frozenset[str]] = frozenset({"SUN", "MOON"})
_CANONICAL_CLUSTER: Final[str] = "main"

_RECORDED_EXACT = TimelineProvenanceModel(
    origin="recorded", quality=Quality.EXACT, is_aggregated=False
)
_RECORDED_UNAVAILABLE = TimelineProvenanceModel(
    origin="recorded", quality=Quality.UNAVAILABLE, is_aggregated=False
)
_DERIVED_ESTIMATED = TimelineProvenanceModel(
    origin="derived", quality=Quality.ESTIMATED, is_aggregated=False
)


@dataclass(frozen=True, slots=True)
class _Transition:
    """One committed new-source room phase change plus its profile metadata."""

    phase: str
    mode_id: int | None
    submode_id: int | None
    runtime_snapshot_version: int | None
    observed_at: datetime
    id: int


@dataclass(slots=True)
class _Witness:
    """One bounded historical observation span that may still be active."""

    phase: str | None
    started_at: datetime
    expiry: datetime
    order: int = 0
    mode_id: int | None = None
    submode_id: int | None = None
    runtime_snapshot_version: int | None = None
    device_name: str | None = None


@dataclass(frozen=True, slots=True)
class _Entry:
    """One chronological sweep input coupled to exactly one evidence stream."""

    kind: Literal["transition", "sample", "coverage", "light"]
    at: datetime
    id: int = 0
    phase: str | None = None
    invalid: bool = False
    state: str | None = None
    mode_id: int | None = None
    submode_id: int | None = None
    runtime_snapshot_version: int | None = None
    device_name: str | None = None


@dataclass(slots=True)
class _SweepState:
    """Mutable accumulator for one half-open history request sweep."""

    latest_transition: _Transition | None = None
    coverage_state: str | None = None
    outage_since: datetime | None = None
    room_witnesses: list[_Witness] = field(default_factory=list)
    light_witnesses: list[_Witness] = field(default_factory=list)


def build_photoperiod_history(
    history_range: ControlHistoryRange,
    phase_rows: Sequence[ControlRecord],
    coverage_rows: Sequence[ControlRecord],
    light_rows: Iterable[ControlRecord],
) -> tuple[tuple[PhotoperiodTimelinePointOut, ...], list[int]]:
    """Reconstruct recorded room phases from transitions, coverage, and samples.

    The committed ``photoperiod_transition`` stream holds a phase only while
    explicit available coverage is active; an unavailable coverage boundary ends
    that authority until an available boundary resumes it. Legacy dedicated
    samples and per-light setpoints witness only their own 60-second observation
    window, and outside the transition authority agreeing bounded witnesses turn
    into one ``derived`` room phase. Disagreement or an active invalid witness
    degrades the span to UNKNOWN. With no evidence at all the read still emits
    the explicit start anchor from nothing supported at that instant, so the
    lookup never falls back to the current schedule.
    """
    start = history_range.start
    end = history_range.end
    buckets = _entries(phase_rows, coverage_rows, light_rows)
    if not buckets:
        return (
            (
                _point(
                    start,
                    "UNKNOWN",
                    _RECORDED_UNAVAILABLE,
                    None,
                    None,
                    None,
                ),
            ),
            [],
        )

    state = _SweepState()
    for observed_at in sorted(observed for observed in buckets if observed < start):
        _apply(state, observed_at, buckets[observed_at])

    boundaries: set[datetime] = {start}
    boundaries.update(at for at in buckets if start <= at < end)
    # Deprecated observation spans also end at their own expiry instant; expiries
    # never land outside the half-open request window.
    boundaries.update(
        expiry
        for expiry in (
            entry.at + _SAMPLE_SUPPORT_WINDOW
            for entries in buckets.values()
            for entry in entries
            if entry.kind in ("sample", "light")
        )
        if start < expiry < end
    )

    points: list[PhotoperiodTimelinePointOut] = []
    emitted: tuple[object, ...] | None = None
    for boundary in sorted(boundaries):
        # Half-open observation spans apply at their start instant and die at
        # their end instant, so an observation truncated by its successor never
        # buoys the successor's instant with stale agreement.
        if boundary in buckets:
            _apply(state, boundary, buckets[boundary])
        _expire(state, boundary)
        point = _evaluate(state, boundary)
        signature = _signature(point)
        if emitted is None or signature != emitted:
            points.append(point)
            emitted = signature

    return tuple(points), _snapshot_versions(phase_rows)


def _entries(
    phase_rows: Sequence[ControlRecord],
    coverage_rows: Sequence[ControlRecord],
    light_rows: Iterable[ControlRecord],
) -> dict[datetime, list[_Entry]]:
    """Group recognized evidence rows into chronological sweep inputs."""
    buckets: dict[datetime, list[_Entry]] = {}
    for row in phase_rows:
        source = _optional_string(row.get("source"))
        if source not in (_TRANSITION_SOURCE, _LEGACY_SAMPLE_SOURCE):
            continue
        observed_at = _aware(row.get("observed_at"))
        entry = _Entry(
            kind="transition" if source == _TRANSITION_SOURCE else "sample",
            at=observed_at,
            id=_optional_int(row.get("id")) or 0,
            phase=_canonical_phase(_optional_string(row.get("phase"))),
            mode_id=_optional_int(row.get("mode_id")),
            submode_id=_optional_int(row.get("submode_id")),
            runtime_snapshot_version=_optional_int(row.get("runtime_snapshot_version")),
        )
        buckets.setdefault(observed_at, []).append(entry)
    for row in coverage_rows:
        state = _optional_string(row.get("state"))
        if state not in ("available", "unavailable"):
            continue
        observed_at = _aware(row.get("observed_at"))
        buckets.setdefault(observed_at, []).append(
            _Entry(
                kind="coverage",
                at=observed_at,
                id=_optional_int(row.get("id")) or 0,
                state=state,
            )
        )
    for timestamp, entries in _light_entries(light_rows):
        bucket = buckets.setdefault(timestamp, [])
        bucket.extend(entries)
    return buckets


def _light_entries(
    rows: Iterable[ControlRecord],
) -> Iterable[tuple[datetime, tuple[_Entry, ...]]]:
    """Group retained per-device light rows into at most one witness per sample.

    Every per-device witness carries that device's identity; the sweep relies
    on its chronological order to keep each identity's latest sample active.
    Rows without a device identity are not light evidence.
    """
    groups: dict[tuple[str, datetime], list[ControlRecord]] = {}
    for row in rows:
        cluster = _optional_string(row.get("cluster"))
        if cluster is not None and cluster != _CANONICAL_CLUSTER:
            continue
        device = _optional_string(row.get("device_name"))
        timestamp = row.get("timestamp")
        if device is None or not isinstance(timestamp, datetime):
            continue
        groups.setdefault((device, timestamp), []).append(row)
    for (device, timestamp), tied in sorted(groups.items()):
        phase, invalid = _light_witness_phase(tied)
        if phase is None and not invalid:
            continue
        yield timestamp, (
            _Entry(
                kind="light",
                at=timestamp,
                phase=phase,
                invalid=invalid,
                device_name=device,
            ),
        )


def _light_witness_phase(rows: Sequence[ControlRecord]) -> tuple[str | None, bool]:
    """Derive one light witness (phase, invalid) from one tied row group.

    Two distinct recognized modes at one sample never agree; off-device modes
    such as ``day``/``night`` are not phase evidence and stay unconsidered.
    """
    recognized = {
        mode
        for mode in (
            _optional_string(row.get("mode")) for row in rows
        )
        if mode in _LIGHT_PHASES
    }
    if len(recognized) > 1:
        return None, True
    if not recognized:
        return None, False
    phase = next(iter(recognized))
    values = [
        row.get("effective_light_intensity")
        for row in rows
        if _optional_string(row.get("mode")) == phase
    ]
    if any(value is None for value in values):
        return None, True
    finite_values = [_finite_float(value) for value in values]
    if any(value is None for value in finite_values):
        return None, True
    if phase == "MOON" and any(value != 0.0 for value in finite_values):
        return None, True
    return phase, False


def _apply(
    state: _SweepState,
    at: datetime,
    entries: Sequence[_Entry],
) -> None:
    """Apply one boundary instant's evidence before the expired spans are removed."""
    for entry in sorted((item for item in entries if item.kind == "sample"), key=lambda i: i.id):
        for witness in state.room_witnesses:
            if witness.started_at < at and witness.expiry > at:
                witness.expiry = at
        state.room_witnesses.append(
            _Witness(
                phase=entry.phase,
                started_at=at,
                expiry=at + _SAMPLE_SUPPORT_WINDOW,
                order=entry.id,
                mode_id=entry.mode_id,
                submode_id=entry.submode_id,
                runtime_snapshot_version=entry.runtime_snapshot_version,
            )
        )
    for entry in sorted(
        (item for item in entries if item.kind == "transition"), key=lambda i: i.id
    ):
        state.latest_transition = _Transition(
            phase=entry.phase,
            mode_id=entry.mode_id,
            submode_id=entry.submode_id,
            runtime_snapshot_version=entry.runtime_snapshot_version,
            observed_at=at,
            id=entry.id,
        )
    for entry in (item for item in entries if item.kind == "light"):
        device = entry.device_name
        # One identity's newest sample replaces that identity's earlier span
        # while other devices keep their own independent witnesses alive.
        state.light_witnesses = [
            witness
            for witness in state.light_witnesses
            if not (
                witness.device_name == device
                and witness.started_at < at
                and witness.expiry > at
            )
        ]
        state.light_witnesses.append(
            _Witness(
                phase=entry.phase,
                started_at=at,
                expiry=at + SAMPLE_SUPPORTED_SPAN,
                order=0,
                device_name=device,
            )
        )
    for entry in sorted((item for item in entries if item.kind == "coverage"), key=lambda i: i.id):
        state.coverage_state = entry.state
        state.outage_since = at if entry.state == "unavailable" else None


def _expire(state: _SweepState, boundary: datetime) -> None:
    """Drop witnesses whose support ends at or before the current instant."""
    state.room_witnesses = [w for w in state.room_witnesses if w.expiry > boundary]
    state.light_witnesses = [w for w in state.light_witnesses if w.expiry > boundary]


def _evaluate(state: _SweepState, now: datetime) -> PhotoperiodTimelinePointOut:
    """Resolve one read-side boundary point from the current sweep state."""
    if state.coverage_state == "available" and state.latest_transition is not None:
        transition = state.latest_transition
        if transition.phase == "UNKNOWN":
            return _point(
                now,
                "UNKNOWN",
                _RECORDED_UNAVAILABLE,
                transition.mode_id,
                transition.submode_id,
                transition.runtime_snapshot_version,
            )
        return _point(
            now,
            transition.phase,
            _RECORDED_EXACT,
            transition.mode_id,
            transition.submode_id,
            transition.runtime_snapshot_version,
        )

    outage_since = state.outage_since if state.coverage_state == "unavailable" else None
    rooms = [
        witness
        for witness in state.room_witnesses
        if outage_since is None or witness.started_at >= outage_since
    ]
    lights = [
        witness
        for witness in state.light_witnesses
        if outage_since is None or witness.started_at >= outage_since
    ]
    if (
        any(witness.phase is None for witness in lights)
        or len({witness.phase for witness in rooms}) > 1
        or len({witness.phase for witness in lights}) > 1
    ):
        return _point(now, "UNKNOWN", _RECORDED_UNAVAILABLE, None, None, None)
    room_phases = {witness.phase for witness in rooms}
    light_phases = {witness.phase for witness in lights}
    if room_phases and light_phases and room_phases != light_phases:
        return _point(now, "UNKNOWN", _RECORDED_UNAVAILABLE, None, None, None)
    if room_phases:
        newest = max(rooms, key=lambda witness: (witness.started_at, witness.order))
        phase = next(iter(room_phases))
        if phase == "UNKNOWN":
            return _point(
                now,
                "UNKNOWN",
                _RECORDED_UNAVAILABLE,
                newest.mode_id,
                newest.submode_id,
                newest.runtime_snapshot_version,
            )
        return _point(
            now,
            phase,
            _RECORDED_EXACT,
            newest.mode_id,
            newest.submode_id,
            newest.runtime_snapshot_version,
        )
    if light_phases:
        return _point(now, next(iter(light_phases)), _DERIVED_ESTIMATED, None, None, None)
    return _point(now, "UNKNOWN", _RECORDED_UNAVAILABLE, None, None, None)


def _point(
    timestamp: datetime,
    phase: str,
    provenance: TimelineProvenanceModel,
    mode_id: int | None,
    submode_id: int | None,
    runtime_snapshot_version: int | None,
) -> PhotoperiodTimelinePointOut:
    return PhotoperiodTimelinePointOut(
        timestamp=timestamp,
        phase=cast(Phase, phase),
        provenance=provenance,
        mode_id=mode_id,
        submode_id=submode_id,
        runtime_snapshot_version=runtime_snapshot_version,
    )


def _signature(
    point: PhotoperiodTimelinePointOut,
) -> tuple[
    str,
    str,
    Quality,
    bool,
    int | None,
    int | None,
    int | None,
]:
    return (
        point.phase,
        point.provenance.origin,
        point.provenance.quality,
        point.provenance.is_aggregated,
        point.mode_id,
        point.submode_id,
        point.runtime_snapshot_version,
    )


def _snapshot_versions(phase_rows: Sequence[ControlRecord]) -> list[int]:
    versions = [
        version
        for row in phase_rows
        if _optional_string(row.get("source")) in (_TRANSITION_SOURCE, _LEGACY_SAMPLE_SOURCE)
        and (version := _optional_int(row.get("runtime_snapshot_version"))) is not None
    ]
    return list(dict.fromkeys(versions))


def _canonical_phase(phase: str | None) -> str:
    return phase if phase in _ROOM_PHASES else "UNKNOWN"


def _aware(value: ControlRecordValue) -> datetime:
    assert isinstance(value, datetime)
    return value


def _optional_int(value: ControlRecordValue) -> int | None:
    assert value is None or isinstance(value, int)
    return value


def _optional_string(value: ControlRecordValue) -> str | None:
    return value if isinstance(value, str) else None


def _finite_float(value: ControlRecordValue) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    numeric = float(value)
    return numeric if isfinite(numeric) else None
