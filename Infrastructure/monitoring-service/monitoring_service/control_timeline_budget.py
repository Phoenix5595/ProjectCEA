from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

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

_TargetPoint = ClimateTimelinePointOut | LightTimelinePointOut
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
    points, steps, linear = _budget_targets(series.points, budget)
    return series.model_copy(update={"points": points, "steps": steps, "linear": linear})


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
    return _limit_transitions(
        points,
        len(points),
        lambda point: (
            point.phase,
            point.mode_id,
            point.submode_id,
            point.runtime_snapshot_version,
        ),
    )


def _non_null(value: float | None) -> float:
    assert value is not None
    return value
