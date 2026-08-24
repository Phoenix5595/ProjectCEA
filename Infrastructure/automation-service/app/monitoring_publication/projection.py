"""Version-triggered publication of automation-owned future projection facts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from typing_extensions import override

from shared.monitoring_contracts import FutureProjection, PublicationVersion


@dataclass(frozen=True, slots=True)
class ProjectionPublicationRequest:
    """One background refresh trigger bound to a configuration/algorithm revision."""

    location: str
    version: PublicationVersion
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ProjectionPublicationError(
                "projection refresh observed_at must be timezone-aware"
            )
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ProjectionPublicationError(ValueError):
    """A factory result cannot safely replace the cached projection authority."""

    detail: str

    @override
    def __str__(self) -> str:
        return self.detail


class ProjectionStore(Protocol):
    """Atomic storage boundary for one location's cached future projection."""

    def read_projection(self, location: str) -> FutureProjection | None: ...

    def replace_projection(self, location: str, projection: FutureProjection) -> None:
        """Atomically replace the complete projection payload for ``location``."""


class ProjectionFactory(Protocol):
    """Authority-preserving future fact builder, invoked only after a cache trigger."""

    def build(self, request: ProjectionPublicationRequest) -> FutureProjection: ...


class ProjectionPublisher:
    """Publish a complete projection only when its cache is missing, stale, or mismatched."""

    def __init__(self, store: ProjectionStore, factory: ProjectionFactory) -> None:
        self._store: ProjectionStore = store
        self._factory: ProjectionFactory = factory

    def publish_if_stale(self, request: ProjectionPublicationRequest) -> FutureProjection:
        """Return the current authority, rebuilding it only for an explicit cache trigger."""
        cached = self._store.read_projection(request.location)
        if cached is not None and projection_is_current(cached, request):
            return cached

        projection = self._factory.build(request)
        _validate_projection(projection, request)
        self._store.replace_projection(request.location, projection)
        return projection


def projection_is_current(
    projection: FutureProjection | None, request: ProjectionPublicationRequest
) -> bool:
    """Whether a cached projection exactly matches the requested authority and remains valid."""
    return (
        projection is not None
        and projection.version == request.version
        and projection.valid_until > request.observed_at
    )


def _validate_projection(
    projection: FutureProjection, request: ProjectionPublicationRequest
) -> None:
    if projection.version != request.version:
        raise ProjectionPublicationError(
            "projection factory returned a mismatched publication version"
        )
    if projection.valid_until <= request.observed_at:
        raise ProjectionPublicationError("projection factory returned an expired projection")
