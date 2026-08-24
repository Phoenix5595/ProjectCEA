"""Bounded, nonblocking handoff for authoritative current monitoring facts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anyio

from shared.monitoring_contracts import CurrentSnapshot


class CurrentPublicationWriter(Protocol):
    """Write one atomic current snapshot from the independent worker."""

    def write_current(self, location: str, snapshot: CurrentSnapshot) -> bool:
        """Return whether Redis accepted the atomic snapshot write."""
        ...


@dataclass(frozen=True, slots=True)
class CurrentPublicationHealth:
    """Non-control health state for the current-publication handoff."""

    pending: bool
    replaced_snapshots: int
    failed_publications: int


class CurrentPublicationPublisher:
    """Mutably retain one latest snapshot so control ticks never wait for Redis I/O."""

    def __init__(self, location: str, writer: CurrentPublicationWriter) -> None:
        self._location: str = location
        self._writer: CurrentPublicationWriter = writer
        self._pending: CurrentSnapshot | None = None
        self._replaced_snapshots: int = 0
        self._failed_publications: int = 0

    @property
    def health(self) -> CurrentPublicationHealth:
        """Report isolated publication status without observing Redis."""
        return CurrentPublicationHealth(
            pending=self._pending is not None,
            replaced_snapshots=self._replaced_snapshots,
            failed_publications=self._failed_publications,
        )

    def enqueue(self, snapshot: CurrentSnapshot) -> None:
        """Replace any stale handoff synchronously, without performing I/O."""
        if self._pending is not None:
            self._replaced_snapshots += 1
        self._pending = snapshot

    async def flush_once(self) -> bool | None:
        """Write one handoff, reporting publication failure without affecting control."""
        snapshot = self._pending
        if snapshot is None:
            return None
        self._pending = None
        try:
            published = await anyio.to_thread.run_sync(
                self._writer.write_current,
                self._location,
                snapshot,
            )
        except (ConnectionError, OSError, TimeoutError):
            self._failed_publications += 1
            return False
        if not published:
            self._failed_publications += 1
            return False
        return True
