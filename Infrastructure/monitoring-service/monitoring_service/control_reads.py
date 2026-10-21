"""Runtime facades for the monitoring service's control read repositories."""

from __future__ import annotations

from typing import Protocol, final

from monitoring_service.cagg_watermarks import CaggWatermarkCache
from monitoring_service.control_history_repository import (
    ControlHistoryRepository,
    _CONTROL_CAGG_RELATIONS,
)
from monitoring_service.control_models import (
    ControlHistoryEnvelope,
    ControlHistoryRange,
    ControlPublicationResponse,
    ControlReadScope,
    RelayTimelineRange,
    RelayTimelineResponse,
)
from monitoring_service.control_publication_repository import ControlPublicationRepository
from monitoring_service.database import ReadOnlyDatabase
from monitoring_service.history_overview_models import (
    HistoryOverviewControlResponse,
    HistoryOverviewRange,
)
from monitoring_service.history_overview_repository import HistoryOverviewRepository
from monitoring_service.redis_resources import RedisReadClient
from monitoring_service.relay_timeline_repository import RelayTimelineRepository
from monitoring_service.sensor_models import MonitoringUnavailableError


class RuntimeReadResources(Protocol):
    """Expose owned read clients while the application lifespan is active."""

    database: ReadOnlyDatabase | None
    redis_client: RedisReadClient | None


@final
class RuntimeControlReads:
    """Connect control repositories to the monitoring service's owned read clients."""

    def __init__(self, resources: RuntimeReadResources) -> None:
        self._resources = resources
        self._watermark_cache = CaggWatermarkCache(_CONTROL_CAGG_RELATIONS)

    async def overview(
        self,
        location: str,
        history_range: HistoryOverviewRange,
        max_points: int = 600,
        scope: ControlReadScope = "all",
    ) -> HistoryOverviewControlResponse:
        """Resolve only the owned database for fixed historical summaries."""
        database = self._resources.database
        if database is None:
            raise MonitoringUnavailableError("history overview aggregate coverage is unavailable")
        return await HistoryOverviewRepository(database).control(
            location, history_range, max_points, scope
        )

    async def history(
        self,
        location: str,
        history_range: ControlHistoryRange,
        max_points: int | None = None,
        scope: ControlReadScope = "all",
    ) -> ControlHistoryEnvelope:
        """Read recorded history only when the service database client is available."""
        database = self._resources.database
        if database is None:
            raise RuntimeError("monitoring database resource is unavailable")
        return await ControlHistoryRepository(database, self._watermark_cache).read(
            location, history_range, max_points, scope
        )

    async def relay_timeline(
        self,
        location: str,
        history_range: RelayTimelineRange,
        limit: int,
        cursor: str | None = None,
    ) -> RelayTimelineResponse:
        """Read physical relay timelines from the owned monitoring database client."""
        database = self._resources.database
        if database is None:
            raise RuntimeError("monitoring database resource is unavailable")
        return await RelayTimelineRepository(database, self._watermark_cache).read(
            location, history_range, limit, cursor
        )

    async def publications(self, location: str) -> ControlPublicationResponse:
        """Read shared publications only when the service Redis client is available."""
        redis = self._resources.redis_client
        if redis is None:
            raise RuntimeError("monitoring Redis resource is unavailable")
        return await ControlPublicationRepository(redis).read(location)
