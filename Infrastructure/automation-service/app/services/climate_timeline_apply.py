"""Apply orchestration that separates committed persistence from invalidation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, final

from app.events import ConfigChangeEvent, ConfigEventType, get_event_bus
from app.repositories.climate_periods import ClimatePeriodRepository
from app.repositories.climate_timeline_apply import TimelineApplyCommit, TimelineApplyRepository
from app.schemas.climate_timeline import TimelineApplyRequest, TimelineApplyResponse
from app.state import get_state_manager
from shared.infra_logging import get_logger
from shared.redis_keys import climate_period_cache_key

if TYPE_CHECKING:
    from app.database import DatabaseManager

logger = get_logger(__name__)


class TimelineConfigurationInvalidator(Protocol):
    """Post-commit notification boundary for saved climate timeline authority."""

    async def invalidate(
        self,
        location: str,
        cluster: str,
        revision: str,
        mode_id: int | None,
        submode_id: int | None,
    ) -> str | None:
        """Invalidate consumers only after a revision has committed.

        Returns a truthful post-commit warning and never raises to imply a
        rollback of the already committed persistence.
        """
        ...


class TimelineApplyValidationError(ValueError):
    """A reviewed aggregate is not valid for authoritative persistence."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors: tuple[str, ...] = errors
        super().__init__(*errors)


@final
class SavedTimelineConfigurationInvalidator:
    """Invalidate only affected profiles after persistence committed."""

    def __init__(
        self,
        database: DatabaseManager,
        *,
        affected_identities: Sequence[tuple[int, int | None]] = (),
    ) -> None:
        self._database = database
        self._affected_identities = tuple(affected_identities)

    async def invalidate(
        self,
        location: str,
        cluster: str,
        revision: str,
        mode_id: int | None,
        submode_id: int | None,
    ) -> str | None:
        """Delete named cache entries; inactive preparation never refreshes runtime."""
        failed = False
        identities = (
            {(mode_id, submode_id)} if mode_id is not None else set(self._affected_identities)
        )
        active_identity: tuple[int, int | None] | None = None
        try:
            active = await self._database.room_mode_repo.get_active_mode(location, cluster)
            if active is not None and isinstance(active.get("mode_id"), int):
                active_identity = (active["mode_id"], active.get("submode_id"))
            if mode_id is None:
                rows = await self._database.climate_periods_repo.get_periods(location, cluster)
                identities.update(
                    (row["mode_id"], row.get("submode_id"))
                    for row in rows
                    if isinstance(row.get("mode_id"), int)
                )
                if active_identity is not None:
                    identities.add(active_identity)
        except Exception:
            logger.exception("Committed profile save: cannot read invalidation authority")
            failed = True

        affected_active = active_identity is not None and (
            mode_id is None or active_identity == (mode_id, submode_id)
        )
        keys = [
            climate_period_cache_key(location, cluster, profile_mode, profile_submode)
            for profile_mode, profile_submode in sorted(
                identities, key=lambda pair: (pair[0], pair[1] if pair[1] is not None else -1)
            )
        ]
        if affected_active:
            keys.extend(
                (
                    f"schedules:loc:{location}:cluster:{cluster}",
                    f"schedules:loc:{location}:cluster:{cluster}:climate",
                    f"schedule:{location}:{cluster}",
                    "schedules:all",
                )
            )
        try:
            state = get_state_manager()
            for key in keys:
                try:
                    await state.delete(key)
                except Exception:
                    logger.exception("Committed profile save: failed to invalidate %s", key)
                    failed = True
        except Exception:
            logger.exception("Committed profile save: cache boundary unavailable")
            failed = True

        if affected_active:
            try:
                published = await get_event_bus().publish(
                    ConfigChangeEvent(
                        event_type=ConfigEventType.SCHEDULE_CHANGED,
                        location=location,
                        cluster=cluster,
                        config_type="climate_timeline",
                        data={
                            "config_revision": revision,
                            "mode_id": mode_id,
                            "submode_id": submode_id,
                        },
                    )
                )
                if not published:
                    logger.error("Committed profile save: notification was not accepted")
                    failed = True
            except Exception:
                logger.exception("Committed profile save: notification failed")
                failed = True
        return "configuration_notification_failed" if failed else None


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
        try:
            warning = await self._invalidator.invalidate(
                location, cluster, commit.config_revision, request.mode_id, request.submode_id
            )
        except Exception:
            logger.exception("Profile committed but its notification boundary failed")
            warning = "configuration_notification_failed"
        return _response(request, commit, warning)


def _response(
    request: TimelineApplyRequest, commit: TimelineApplyCommit, warning: str | None
) -> TimelineApplyResponse:
    """Build the exact saved baseline returned only after invalidation is scheduled."""
    return TimelineApplyResponse(
        request_id=request.request_id,
        config_revision=commit.config_revision,
        mode_id=request.mode_id,
        submode_id=request.submode_id,
        periods=request.periods,
        photoperiod=request.photoperiod,
        parameters_configured=True,
        notification_warning=warning,
    )
