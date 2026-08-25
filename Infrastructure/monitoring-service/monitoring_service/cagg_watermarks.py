"""Bounded process-local CAGG watermark cache."""

from __future__ import annotations

import re

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Final

from monitoring_service.sensor_models import MonitoringUnavailableError

CAGG_WATERMARK_TTL_SECONDS: Final[float] = 5.0


@dataclass(frozen=True, slots=True)
class CaggWatermarkEntry:
    """An immutable watermark snapshot and its monotonic fetch time."""

    watermark: datetime
    fetched_at: float


class CaggWatermarkCache:
    """Cache known CAGG watermarks for five seconds using a monotonic clock."""

    def __init__(
        self, relations: frozenset[str], *, clock: Callable[[], float] = monotonic
    ) -> None:
        self._relations: frozenset[str] = relations
        self._clock: Callable[[], float] = clock
        self._entries: dict[str, CaggWatermarkEntry] = {}

    async def get(self, relation: str, fetch: Callable[[], Awaitable[datetime]]) -> datetime:
        """Return a fresh watermark, refreshing only after the TTL expires."""
        if relation not in self._relations:
            raise MonitoringUnavailableError(f"unsupported CAGG relation: {relation}")
        cached = self._entries.get(relation)
        if cached is not None and self._clock() - cached.fetched_at < CAGG_WATERMARK_TTL_SECONDS:
            return cached.watermark
        watermark = await fetch()
        self._entries[relation] = CaggWatermarkEntry(watermark, self._clock())
        return watermark


def watermark_sql(relation: str) -> str:
    """Return a validated index-backed materialized-view watermark query."""
    if not re.fullmatch(r"[a-z_0-9]+", relation):
        raise MonitoringUnavailableError(f"unsupported CAGG relation: {relation}")
    return f'SELECT max(bucket) AS materialization_watermark FROM "{relation}"'
