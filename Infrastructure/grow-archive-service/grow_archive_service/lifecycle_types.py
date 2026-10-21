from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TYPE_CHECKING
from uuid import UUID

from .capture import PriorArchive
from .jobs import ArchiveJobQueue
from .manifest import ArchiveManifest
from .models import JobClaim, JobKind

if TYPE_CHECKING:
    from .exporter import LoadPin


@dataclass(frozen=True, slots=True)
class PlannedJob:
    """One durable archive job the tick ensured exists."""

    dataset_id: UUID
    kind: JobKind
    requested_revision: int
    job_id: UUID


@dataclass(frozen=True, slots=True)
class ExpiredInterval:
    """One nongrow interval whose observations were removed."""

    source_table: str
    room: str
    start_at: datetime
    end_at: datetime
    rows: int


@dataclass(frozen=True, slots=True)
class HeldScope:
    """One scope deliberately held back from expiry or removal."""

    scope: str
    reference: str
    code: str


@dataclass(frozen=True, slots=True)
class LifecycleTickResult:
    """The complete observable outcome of one lifecycle planning tick."""

    enabled: bool
    now: datetime
    planned: tuple[PlannedJob, ...] = ()
    expired: tuple[ExpiredInterval, ...] = ()
    held: tuple[HeldScope, ...] = ()
    errors: tuple[str, ...] = ()
    paused_early: bool = False


@dataclass(frozen=True, slots=True)
class NongrowExpiryResult:
    """One bounded nongrow expiry pass plus scopes deliberately retained."""

    expired: tuple[ExpiredInterval, ...] = ()
    held: tuple[HeldScope, ...] = ()
    deleted_rows: int = 0
    paused_early: bool = False


@dataclass(frozen=True, slots=True)
class _IdentityAssignment:
    source_table: str
    identity: int
    room: str
    start: datetime
    end: datetime
    precedence: int


@dataclass(frozen=True, slots=True)
class _Segment:
    start: datetime
    end: datetime
    room: str


class DerivedHistoryMaintenance(Protocol):
    """The step-5 derived-aggregate maintenance surface owned by its worker."""

    async def preflight(self, source_tables: Sequence[str]) -> Any: ...

    async def refresh(
        self, source_table: str, room: str | None, start: datetime, end: datetime
    ) -> None: ...






class ExportExecutor(Protocol):
    """The real step-6 handlers used by the one archive dispatcher."""
    queue: ArchiveJobQueue

    async def export_dataset(self, claim: JobClaim) -> UUID: ...

    async def merge_annual(self, claim: JobClaim) -> UUID: ...

    async def run_load(self, claim: JobClaim) -> "LoadPin": ...

    async def cleanup_artifacts(self, claim: JobClaim) -> int: ...

    async def expire_loads(self) -> int: ...

    async def reverify_artifact(self, artifact_id: UUID) -> ArchiveManifest: ...
    async def prior_archive_path(self, dataset: Mapping[str, Any]) -> PriorArchive: ...

    async def release_prior_archive(self, path: Path) -> None: ...


class FreshnessGate(Protocol):
    """The read-only freshness view used to pause expiry batches."""

    @property
    def paused(self) -> bool: ...


def _effective_segments(assignments: Sequence[_IdentityAssignment]) -> tuple[_Segment, ...]:
    """Resolve date-bounded assignments; conflicts remain deliberately uncovered.

    Explicit owner corrections (precedence 1) override frozen capture snapshots
    (precedence 0). Two different rooms at the same precedence are ambiguous,
    so that interval is not assigned to either room and can never be expired.
    """
    points = sorted({point for item in assignments for point in (item.start, item.end)})
    resolved: list[_Segment] = []
    for start, end in zip(points, points[1:]):
        if start >= end:
            continue
        covering = [item for item in assignments if item.start <= start and item.end >= end]
        if not covering:
            continue
        highest = max(item.precedence for item in covering)
        rooms = {item.room for item in covering if item.precedence == highest}
        if len(rooms) != 1:
            continue
        room = next(iter(rooms))
        if resolved and resolved[-1].end == start and resolved[-1].room == room:
            resolved[-1] = _Segment(resolved[-1].start, end, room)
        else:
            resolved.append(_Segment(start, end, room))
    return tuple(resolved)


class _IdentityRoomIndex:
    """Bounded override-first / frozen-snapshot room resolution for bulk expiry."""

    def __init__(self, assignments: Sequence[_IdentityAssignment]) -> None:
        grouped: dict[str, dict[int, list[_IdentityAssignment]]] = defaultdict(lambda: defaultdict(list))
        for item in assignments:
            grouped[item.source_table][item.identity].append(item)
        self._segments: dict[str, dict[int, tuple[_Segment, ...]]] = {
            table: {identity: _effective_segments(items) for identity, items in identities.items()}
            for table, identities in grouped.items()
        }

    def identities_for_room(self, source_table: str, room: str) -> frozenset[int]:
        """Every identity with a resolved interval in this room."""
        return frozenset(
            identity for identity, segments in self._segments.get(source_table, {}).items()
            if any(segment.room == room for segment in segments)
        )

    def ids_for(self, source_table: str, room: str, start: datetime, end: datetime) -> frozenset[int]:
        """Identities whose effective room covers the complete half-open interval."""
        if start >= end:
            raise ValueError("identity room interval must be nonempty")
        result: set[int] = set()
        for identity, segments in self._segments.get(source_table, {}).items():
            position = start
            for segment in segments:
                if segment.end <= position:
                    continue
                if segment.start > position or segment.room != room:
                    break
                position = min(segment.end, end)
                if position >= end:
                    result.add(identity)
                    break
        return frozenset(result)

    def covers_identity(self, source_table: str, identity: int, start: datetime, end: datetime) -> bool:
        """Whether every instant in an observation span has one known room."""
        if start >= end:
            return False
        position = start
        for segment in self._segments.get(source_table, {}).get(identity, ()):
            if segment.end <= position:
                continue
            if segment.start > position:
                return False
            position = min(segment.end, end)
            if position >= end:
                return True
        return False

    def boundaries(self, source_table: str, start: datetime, end: datetime) -> tuple[datetime, ...]:
        """Effective-room change points strictly inside ``(start, end)``."""
        points: set[datetime] = set()
        for segments in self._segments.get(source_table, {}).values():
            for segment in segments:
                if start < segment.start < end:
                    points.add(segment.start)
                if start < segment.end < end:
                    points.add(segment.end)
        return tuple(sorted(points))



def _unique_held(items: Sequence[HeldScope]) -> list[HeldScope]:
    seen: set[tuple[str, str, str]] = set()
    result: list[HeldScope] = []
    for item in items:
        key = (item.scope, item.reference, item.code)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
