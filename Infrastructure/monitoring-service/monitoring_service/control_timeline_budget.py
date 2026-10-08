from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Final, TypeVar

from monitoring_service.control_models import (
    ClimateTimelinePointOut,
    ClimateTimelineSeriesOut,
    ControlHistoryEnvelope,
    DeviceTimelineSeriesOut,
    LightTimelinePointOut,
    LightTimelineSeriesOut,
    PhotoperiodTimelinePointOut,
    PidTimelineSeriesOut,
    TimelineLinearOut,
    TimelineStepOut,
)

# One DFR0971 DAC code expressed in intensity percentage points (100%/4095
# codes). Recorded light ramps may be simplified only within this error bound,
# so a reduced ramp is indistinguishable at DAC resolution from the recorded
# samples it summarizes.
ONE_DAC_CODE_PERCENT: Final[float] = 100.0 / 4095.0

_TargetPoint = ClimateTimelinePointOut
_Point = TypeVar("_Point")
_Signature = TypeVar("_Signature")


def budget_control_history(
    envelope: ControlHistoryEnvelope, max_points: int
) -> ControlHistoryEnvelope:
    return envelope.model_copy(
        update={
            "climate": tuple(_budget_climate(series, max_points) for series in envelope.climate),
            "lights": tuple(_budget_light(series, max_points) for series in envelope.lights),
            "devices": tuple(_budget_devices(series, max_points) for series in envelope.devices),
            "pid": tuple(_budget_pid(series, max_points) for series in envelope.pid),
            "photoperiod": _collapse_photoperiod(envelope.photoperiod),
        }
    )


def _budget_climate(series: ClimateTimelineSeriesOut, budget: int) -> ClimateTimelineSeriesOut:
    points, steps, linear = _budget_targets(series.points, budget)
    return series.model_copy(update={"points": points, "steps": steps, "linear": linear})


def _budget_light(series: LightTimelineSeriesOut, budget: int) -> LightTimelineSeriesOut:
    """Reduce one recorded light series without erasing ramp geometry.

    Held samples and unavailable coverage become explicit steps, exactly like
    the climate ladder. Each ramp run is simplified with a shape-preserving
    reducer whose chord error stays within one DAC code, and it is split at
    every retained direction reversal or discontinuity so genuine deviations
    survive. Retained vertices are recorded setpoint samples; interpolated
    values are segment geometry, not independently measured dimmer feedback.
    Mandatory boundaries (holds, unavailable coverage, ramp run boundaries
    and retained reversals) are never dropped to satisfy the point budget:
    when the tolerance-preserving reduction exceeds it, the full
    reduced shape is returned and the series reports the approximation through
    ``is_aggregated`` instead of silently discarding real deviations.
    """
    steps: list[TimelineStepOut] = []
    chains: list[tuple[LightTimelinePointOut, ...]] = []
    dropped_samples = False
    run: list[LightTimelinePointOut] = []

    def flush_run() -> None:
        nonlocal dropped_samples
        if not run:
            return
        reduced = _reduce_ramp_run(tuple(run), ONE_DAC_CODE_PERCENT)
        dropped_samples = dropped_samples or len(reduced) < len(run)
        chains.append(reduced)
        run.clear()

    for point in series.points:
        if point.value is None:
            flush_run()
            if not steps or steps[-1].value is not None:
                steps.append(
                    TimelineStepOut(
                        timestamp=point.timestamp, value=None, provenance=point.provenance
                    )
                )
        elif point.ramp_progress is None:
            flush_run()
            if not steps or steps[-1].value != point.value:
                steps.append(
                    TimelineStepOut(
                        timestamp=point.timestamp, value=point.value, provenance=point.provenance
                    )
                )
        else:
            run.append(point)
    flush_run()

    linear: list[TimelineLinearOut] = []
    for chain in chains:
        if len(chain) == 1:
            vertex = chain[0]
            linear.append(
                TimelineLinearOut(
                    start=vertex.timestamp,
                    end=vertex.timestamp,
                    start_value=_non_null(vertex.value),
                    end_value=_non_null(vertex.value),
                    provenance=vertex.provenance,
                )
            )
            continue
        for left, right in zip(chain, chain[1:]):
            linear.append(
                TimelineLinearOut(
                    start=left.timestamp,
                    end=right.timestamp,
                    start_value=_non_null(left.value),
                    end_value=_non_null(right.value),
                    provenance=left.provenance,
                )
            )

    used = len(steps) + 2 * len(linear)
    aggregated = series.provenance.is_aggregated or dropped_samples or used > budget
    return series.model_copy(
        update={
            "points": (),
            "steps": tuple(steps),
            "linear": tuple(linear),
            "provenance": series.provenance.model_copy(update={"is_aggregated": aggregated}),
        }
    )


def _reduce_ramp_run(
    run: tuple[LightTimelinePointOut, ...], tolerance: float
) -> tuple[LightTimelinePointOut, ...]:
    """Return a sample subset whose chords stay within the tolerance.

    Douglas-Peucker over (timestamp, intensity): monotonic samples within the
    tolerance collapse to their endpoints, while any larger deviation — a
    genuine direction reversal or restart discontinuity — forces a retained
    vertex at the worst sample, splitting the run into exact chord segments.
    """
    if len(run) <= 2:
        return run
    kept = [False] * len(run)
    kept[0] = kept[-1] = True
    stack: list[tuple[int, int]] = [(0, len(run) - 1)]
    while stack:
        low, high = stack.pop()
        if high - low < 2:
            continue
        start = run[low]
        end = run[high]
        start_value = _non_null(start.value)
        end_value = _non_null(end.value)
        duration = (end.timestamp - start.timestamp).total_seconds()
        worst_index: int | None = None
        worst_error = tolerance
        for index in range(low + 1, high):
            sample = run[index]
            offset = (sample.timestamp - start.timestamp).total_seconds()
            fraction = offset / duration if duration > 0 else 0.0
            interpolated = start_value + fraction * (end_value - start_value)
            error = abs(_non_null(sample.value) - interpolated)
            if error > worst_error:
                worst_error = error
                worst_index = index
        if worst_index is not None:
            kept[worst_index] = True
            stack.append((low, worst_index))
            stack.append((worst_index, high))
    return tuple(point for point, keep in zip(run, kept) if keep)


def _budget_targets(
    points: tuple[_TargetPoint, ...], budget: int
) -> tuple[tuple[_TargetPoint, ...], tuple[TimelineStepOut, ...], tuple[TimelineLinearOut, ...]]:
    steps: list[TimelineStepOut] = []
    linear: list[TimelineLinearOut] = []
    index = 0
    while index < len(points):
        point = points[index]
        if point.value is None:
            if not steps or steps[-1].value is not None:
                steps.append(
                    TimelineStepOut(
                        timestamp=point.timestamp, value=None, provenance=point.provenance
                    )
                )
            index += 1
            continue
        if point.ramp_progress is None:
            if not steps or steps[-1].value != point.value:
                steps.append(
                    TimelineStepOut(
                        timestamp=point.timestamp, value=point.value, provenance=point.provenance
                    )
                )
            index += 1
            continue
        ramp_start = point
        index += 1
        while index < len(points) and points[index].ramp_progress is not None:
            index += 1
        ramp_end = points[index - 1]
        linear.append(
            TimelineLinearOut(
                start=ramp_start.timestamp,
                end=ramp_end.timestamp,
                start_value=_non_null(ramp_start.value),
                end_value=_non_null(ramp_end.value),
                provenance=ramp_start.provenance,
            )
        )
    step_budget = max(0, budget - 2 * len(linear))
    return (), _limit_transitions(steps, step_budget, lambda point: point.value), tuple(linear)


def _budget_devices(series: DeviceTimelineSeriesOut, budget: int) -> DeviceTimelineSeriesOut:
    return series.model_copy(
        update={
            "points": _limit_transitions(
                series.points,
                budget,
                lambda point: (point.device_state, point.device_mode, point.control_reason),
            )
        }
    )


def _budget_pid(series: PidTimelineSeriesOut, budget: int) -> PidTimelineSeriesOut:
    return series.model_copy(
        update={
            "points": _limit_transitions(
                series.points, budget, lambda point: (point.pid_output, point.duty_cycle_percent)
            )
        }
    )


def _limit_transitions(
    points: Sequence[_Point], budget: int, signature: Callable[[_Point], _Signature]
) -> tuple[_Point, ...]:
    if budget == 0:
        return ()
    changes = [
        point
        for index, point in enumerate(points)
        if index == 0 or signature(point) != signature(points[index - 1])
    ]
    if len(changes) <= budget:
        return tuple(changes)
    if budget == 1:
        return (changes[0],)
    return tuple(
        changes[round(index * (len(changes) - 1) / (budget - 1))] for index in range(budget)
    )


def _collapse_photoperiod(
    points: tuple[PhotoperiodTimelinePointOut, ...],
) -> tuple[PhotoperiodTimelinePointOut, ...]:
    """Collapse adjacent identical spans without losing provenance shifts.

    Phase and unknown boundaries are all mandatory: the collapse only merges
    neighbours whose phase, provenance, and profile metadata agree, so an
    adjacent SUN derived span never merges into a SUN recorded span.
    """
    return _limit_transitions(
        points,
        len(points),
        lambda point: (
            point.phase,
            point.provenance.origin,
            point.provenance.quality,
            point.provenance.is_aggregated,
            point.mode_id,
            point.submode_id,
            point.runtime_snapshot_version,
        ),
    )


def _non_null(value: float | None) -> float:
    assert value is not None
    return value
