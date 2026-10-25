"""Read paired current and future control publications."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, final

from pydantic import ValidationError

from monitoring_service.control_models import (
    ControlPublicationResponse,
    CurrentPublicationResponse,
    ProjectionPublicationResponse,
)
from monitoring_service.rich_trajectory_models import RichTrajectoryEnvelope
from shared.monitoring_contracts import (
    CurrentSnapshot,
    FutureProjection,
    MonitoringContractViolation,
    Quality,
    validate_projection_timeline,
)
from shared.redis_keys import (
    monitoring_current_publication_key,
    monitoring_future_publication_key,
    monitoring_rich_trajectory_key,
)


class PublicationRedis(Protocol):
    """The atomic multi-key read capability required for shared publications."""

    def mget(self, keys: list[str]) -> Awaitable[list[str | None]]: ...


@final
class ControlPublicationRepository:
    """Read paired current and future facts without recalculating automation state."""

    def __init__(
        self, redis: PublicationRedis, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._redis = redis
        self._clock = clock or (lambda: datetime.now(UTC))

    async def read(self, location: str) -> ControlPublicationResponse:
        """Return publications only when both authorities parse, share one version, and are valid."""
        payloads = await self._redis.mget(_publication_keys(location))
        current_payload = payloads[0] if len(payloads) > 0 else None
        future_payload = payloads[1] if len(payloads) > 1 else None
        rich_payload = payloads[2] if len(payloads) > 2 else None
        current = _parse_current(current_payload)
        future = _parse_future(future_payload)
        trajectory = _parse_trajectory(rich_payload, current)
        if (
            current is None
            or future is None
            or any(item.version != current.version for item in future)
        ):
            return _unavailable_publication()
        now = self._clock()
        if current.valid_until <= now:
            return _unavailable_publication()
        # The future timeline is published every few minutes while its first
        # interval only covers the current minute. Elapsed leading intervals are
        # history, not staleness: drop them and keep the still-valid forecast,
        # so the projection is not unavailable for most of each publish cycle.
        remaining = tuple(item for item in future if item.valid_until > now)
        if not remaining:
            return _unavailable_publication()
        return ControlPublicationResponse(
            current=CurrentPublicationResponse(quality=Quality.EXACT, value=current),
            projection=ProjectionPublicationResponse(
                quality=Quality.ESTIMATED, value=remaining, trajectory=trajectory
            ),
        )


def _publication_keys(location: str) -> list[str]:
    return [
        monitoring_current_publication_key(location),
        monitoring_future_publication_key(location),
        monitoring_rich_trajectory_key(location),
    ]


def _parse_current(payload: str | None) -> CurrentSnapshot | None:
    if payload is None:
        return None
    try:
        return CurrentSnapshot.model_validate_json(payload)
    except ValidationError:
        return None


def _parse_future(payload: str | None) -> tuple[FutureProjection, ...] | None:
    """Normalize legacy single-object or versioned array payloads into a timeline."""
    if payload is None:
        return None
    try:
        decoded = json.loads(payload)
    except ValueError:
        return None
    try:
        items = decoded if isinstance(decoded, list) else [decoded]
        projections = tuple(
            FutureProjection.model_validate_json(json.dumps(item)) for item in items
        )
        return validate_projection_timeline(projections)
    except (ValidationError, MonitoringContractViolation):
        return None


def _parse_trajectory(
    payload: str | None, current: CurrentSnapshot | None
) -> RichTrajectoryEnvelope | None:
    if payload is None:
        return None
    try:
        trajectory = RichTrajectoryEnvelope.model_validate_json(payload, strict=False)
    except ValidationError:
        return None
    if trajectory.revision_scope != "saved" or current is None:
        return None
    return (
        trajectory
        if trajectory.base_config_revision == f"{current.version.config_version:07x}"
        else None
    )


def _unavailable_publication() -> ControlPublicationResponse:
    return ControlPublicationResponse(
        current=CurrentPublicationResponse(quality=Quality.UNAVAILABLE, value=None),
        projection=ProjectionPublicationResponse(quality=Quality.UNAVAILABLE, value=()),
    )
