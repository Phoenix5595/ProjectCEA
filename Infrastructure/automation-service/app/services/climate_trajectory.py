"""Public API for pure piecewise climate trajectories."""

from __future__ import annotations

from app.schemas.climate_timeline import RichTrajectoryEnvelope

from .climate_trajectory_effective import effective_segments
from .climate_trajectory_segments import scheduled_segments
from .climate_trajectory_types import (
    ClimatePeriodTrajectory,
    MetricTarget,
    RuntimeOverride,
    TrajectoryRequest,
)

__all__ = [
    "ClimatePeriodTrajectory",
    "MetricTarget",
    "RuntimeOverride",
    "TrajectoryRequest",
    "project_trajectories",
]


def project_trajectories(request: TrajectoryRequest) -> RichTrajectoryEnvelope:
    """Materialize scheduled and effective segments at semantic boundaries only."""
    metrics = tuple(
        sorted({target.metric for period in request.periods for target in period.targets})
    )
    scheduled = tuple(
        segment for metric in metrics for segment in scheduled_segments(request, metric)
    )
    effective = tuple(
        segment
        for metric in metrics
        for segment in effective_segments(request, metric, scheduled_segments(request, metric))
    )
    source = request.periods[0].source
    assumptions = (
        ("assumes override remains active",)
        if any(override.end is None for override in request.overrides)
        else ()
    )
    return RichTrajectoryEnvelope(
        contract_version=1,
        room=request.room,
        generated_at=request.window.start,
        window=request.window,
        revision_scope="saved",
        base_config_revision=source.config_revision,
        segments=scheduled + effective,
        assumptions=assumptions,
    )
