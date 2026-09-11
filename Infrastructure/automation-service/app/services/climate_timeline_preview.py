"""Read-only orchestration for complete climate timeline draft previews."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import anyio

from app.monitoring_publication.rich import project_saved_trajectory
from app.repositories.climate_periods import ClimatePeriodRepository
from app.repositories.climate_timeline_snapshot import (
    ClimateScheduleDraft,
    ClimateScheduleSnapshot,
    ClimateScheduleSnapshotBuilder,
    TimelineWindow,
)
from app.schemas.climate_timeline import (
    RichTrajectoryEnvelope,
    TimelinePeriodDraft,
    TimelinePhotoperiodDraft,
    TimelinePreviewRequest,
    TimelinePreviewResponse,
    TimelineSavedResponse,
)


@dataclass(frozen=True, slots=True)
class TimelinePreviewValidationError(Exception):
    """A complete draft conflicts with the authoritative period rules."""

    code: str
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TimelinePreviewUnavailableError(Exception):
    """Saved authority cannot produce a finite draft trajectory."""

    detail: str


class SavedTrajectoryReader(Protocol):
    """Read the latest worker-published saved trajectory."""

    def read_rich_trajectory(self, location: str) -> RichTrajectoryEnvelope | None: ...


@dataclass(frozen=True, slots=True)
class ClimateTimelinePreviewService:
    """Build copied schedule snapshots and evaluate drafts without side effects."""

    snapshot_builder: ClimateScheduleSnapshotBuilder
    saved_trajectory_reader: SavedTrajectoryReader | None = None

    async def saved(
        self, location: str, cluster: str, request: TimelinePreviewRequest
    ) -> ClimateScheduleSnapshot:
        """Gather the immutable saved authority that a request overlays."""
        return await self.snapshot_builder.build_saved(location, cluster, _timeline_window(request))

    async def saved_response(
        self, location: str, cluster: str, window: TimelineWindow, config_revision: str
    ) -> TimelineSavedResponse:
        """Return saved schedule authority and its rich trajectory without writes."""
        snapshot = await self.snapshot_builder.build_saved(location, cluster, window)
        schedule = next(
            (
                schedule_slice.schedule
                for schedule_slice in snapshot.slices
                if schedule_slice.schedule
            ),
            None,
        )
        if schedule is None:
            raise TimelinePreviewUnavailableError("saved schedule authority is unavailable")
        mode_id = schedule.mode.get("mode_id")
        submode_id = schedule.mode.get("submode_id")
        if not isinstance(mode_id, int) or not (isinstance(submode_id, int) or submode_id is None):
            raise TimelinePreviewUnavailableError("saved mode identity is unavailable")
        parameters = dict(schedule.parameters)
        try:
            periods = tuple(_period_response(dict(period)) for period in schedule.periods)
            photoperiod = TimelinePhotoperiodDraft.model_validate(
                {
                    "day_start_time": _time_text(parameters["day_start_time"]),
                    "night_start_time": _time_text(parameters["night_start_time"]),
                    "ramp_up_minutes": int(parameters["light_ramp_up_minutes"]),
                    "ramp_down_minutes": int(parameters["light_ramp_down_minutes"]),
                },
                strict=False,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise TimelinePreviewUnavailableError(
                "saved photoperiod authority is unavailable"
            ) from error
        trajectory = None
        if self.saved_trajectory_reader is not None:
            trajectory = await anyio.to_thread.run_sync(
                self.saved_trajectory_reader.read_rich_trajectory, location
            )
            if trajectory is not None and trajectory.base_config_revision != config_revision:
                trajectory = None
        if trajectory is None:
            trajectory = project_saved_trajectory(snapshot, location, config_revision)
        return TimelineSavedResponse(
            config_revision=config_revision,
            mode_id=mode_id,
            submode_id=submode_id,
            periods=periods,
            photoperiod=photoperiod,
            trajectory=trajectory,
        )

    async def preview(
        self, location: str, cluster: str, request: TimelinePreviewRequest
    ) -> TimelinePreviewResponse:
        """Validate and evaluate one complete draft without writing any authority."""
        _validate_periods(request)
        saved = await self.saved(location, cluster, request)
        draft = ClimateScheduleDraft.from_rows(
            mode_id=request.mode_id,
            submode_id=request.submode_id,
            periods=tuple(period.model_dump() for period in request.periods),
            photoperiod=request.photoperiod.model_dump(),
        )
        projected = project_saved_trajectory(
            self.snapshot_builder.preview(saved, draft), location, request.expected_config_revision
        )
        if projected is None:
            raise TimelinePreviewUnavailableError("saved schedule authority is unavailable")
        return TimelinePreviewResponse(
            request_id=request.request_id,
            expected_config_revision=request.expected_config_revision,
            draft_revision=request.draft_revision,
            trajectory=_draft_trajectory(projected, request),
        )


def _timeline_window(request: TimelinePreviewRequest) -> TimelineWindow:
    start = datetime.fromisoformat(request.window.start.replace("Z", "+00:00"))
    end = datetime.fromisoformat(request.window.end.replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None:
        raise TimelinePreviewValidationError(
            "invalid_preview_window", ("preview window timestamps must be timezone-aware",)
        )
    if end <= start:
        raise TimelinePreviewValidationError(
            "invalid_preview_window", ("preview window end must be later than start",)
        )
    return TimelineWindow(start.astimezone(UTC), end.astimezone(UTC), request.window.timezone)


def _time_text(value: Any) -> str:
    return str(value)[:5]


def _period_response(row: dict[str, Any]) -> TimelinePeriodDraft:
    return TimelinePeriodDraft.model_validate(
        {
            "id": str(row["id"]),
            "period_name": row["period_name"],
            "start_time": _time_text(row["start_time"]),
            "end_time": _time_text(row["end_time"]),
            "ramp_minutes": int(row["ramp_minutes"]),
            "heating_setpoint": None
            if row["heating_setpoint"] is None
            else float(row["heating_setpoint"]),
            "cooling_setpoint": None
            if row["cooling_setpoint"] is None
            else float(row["cooling_setpoint"]),
            "vpd_setpoint": None if row["vpd_setpoint"] is None else float(row["vpd_setpoint"]),
            "co2_setpoint": None if row["co2_setpoint"] is None else int(row["co2_setpoint"]),
            "details": row.get("details") or "",
        },
        strict=False,
    )


def _validate_periods(request: TimelinePreviewRequest) -> None:
    valid, errors = ClimatePeriodRepository().validate_24h_coverage(
        [period.model_dump() for period in request.periods]
    )
    if valid:
        return
    code = (
        "invalid_period_overlap"
        if any(error.startswith("Overlap:") for error in errors)
        else "invalid_period_coverage"
    )
    raise TimelinePreviewValidationError(code, tuple(errors))


def _draft_trajectory(
    saved: RichTrajectoryEnvelope, request: TimelinePreviewRequest
) -> RichTrajectoryEnvelope:
    draft_revision = str(request.draft_revision)
    return RichTrajectoryEnvelope.model_validate(
        {
            **saved.model_dump(),
            "revision_scope": "draft",
            "draft_revision": draft_revision,
            "segments": tuple(
                segment.model_dump()
                | {"source": segment.source.model_dump() | {"draft_revision": draft_revision}}
                for segment in saved.segments
            ),
        }
    )
