"""Apply orchestration that separates committed persistence from invalidation."""

from __future__ import annotations

from typing import Protocol, final

from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus
from app.repositories.climate_periods import ClimatePeriodRepository
from app.repositories.climate_timeline_apply import TimelineApplyCommit, TimelineApplyRepository
from app.schemas.climate_timeline import TimelineApplyRequest, TimelineApplyResponse
from app.state import get_state_manager


class TimelineConfigurationInvalidator(Protocol):
    """Post-commit notification boundary for saved climate timeline authority."""

    async def invalidate(self, location: str, cluster: str, revision: str) -> None:
        """Invalidate consumers only after a revision has committed."""
        ...


class TimelineApplyValidationError(ValueError):
    """A reviewed aggregate is not valid for authoritative persistence."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors: tuple[str, ...] = errors
        super().__init__(*errors)


@final
class SavedTimelineConfigurationInvalidator:
    """Invalidate cached schedules and notify configuration consumers after commit."""

    async def invalidate(self, location: str, cluster: str, revision: str) -> None:
        """Publish the committed revision after clearing schedule cache entries."""
        state = get_state_manager()
        _ = await state.delete(f"schedules:loc:{location}:cluster:{cluster}")
        _ = await state.delete(f"schedules:loc:{location}:cluster:{cluster}:climate")
        _ = await state.delete("schedules:all")
        _ = await get_event_bus().publish(
            ConfigChangeEvent(
                event_type=ConfigEventType.SCHEDULE_CHANGED,
                location=location,
                cluster=cluster,
                config_type="climate_timeline",
                data={"config_revision": revision},
            )
        )


@final
class ClimateTimelineApplyService:
    """Validate a reviewed draft, commit it, then invalidate saved timeline readers."""

    def __init__(
        self, repository: TimelineApplyRepository, invalidator: TimelineConfigurationInvalidator
    ) -> None:
        self._repository: TimelineApplyRepository = repository
        self._invalidator: TimelineConfigurationInvalidator = invalidator

    async def apply(
        self, location: str, cluster: str, request: TimelineApplyRequest
    ) -> TimelineApplyResponse:
        """Persist a complete valid draft and return its new saved revision."""
        valid, errors = ClimatePeriodRepository().validate_24h_coverage(
            [period.model_dump() for period in request.periods]
        )
        if not valid:
            raise TimelineApplyValidationError(tuple(errors))
        commit = await self._repository.apply(location, cluster, request)
        await self._invalidator.invalidate(location, cluster, commit.config_revision)
        return _response(request, commit)


def _response(request: TimelineApplyRequest, commit: TimelineApplyCommit) -> TimelineApplyResponse:
    """Build the exact saved baseline returned only after invalidation is scheduled."""
    return TimelineApplyResponse(
        request_id=request.request_id,
        config_revision=commit.config_revision,
        mode_id=request.mode_id,
        submode_id=request.submode_id,
        periods=request.periods,
        photoperiod=request.photoperiod,
    )
