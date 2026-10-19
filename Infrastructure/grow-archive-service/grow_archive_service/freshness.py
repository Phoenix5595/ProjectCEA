"""Publication-freshness guard for grow archive heavy work.

Reads the automation-service current monitoring publications from Redis
(read-only) once per second and pauses heavy archive work whenever either
room's publication is absent, unparseable, expired (``valid_until <= now``)
or older than five seconds. Heavy work resumes only after three consecutive
fresh checks. Redis is never written.

The guard uses the existing read-only Redis client
(``monitoring_service.redis_resources.RedisReadClient``, injected as the
:class:`PublicationReader` protocol) and parses
``shared.monitoring_contracts.CurrentSnapshot`` payloads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, final

from pydantic import ValidationError

from shared.infra_logging import get_logger
from shared.monitoring_contracts import CurrentSnapshot
from shared.redis_keys import monitoring_current_publication_key

logger = get_logger(__name__)

PUBLICATION_ROOMS: Final[tuple[str, ...]] = ("Flower Room", "Veg Room")
CHECK_INTERVAL_SECONDS: Final[float] = 1.0
MAX_OBSERVATION_AGE: Final[timedelta] = timedelta(seconds=5)
RESUME_FRESH_CHECKS: Final[int] = 3

DETAIL_FRESH: Final = "fresh"
DETAIL_ABSENT: Final = "publication_absent"
DETAIL_INVALID: Final = "publication_invalid"
DETAIL_EXPIRED: Final = "publication_expired"
DETAIL_STALE: Final = "publication_stale"
DETAIL_READ_FAILED: Final = "publication_read_failed"
DETAIL_FRESHNESS_PENDING: Final = "freshness_pending"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PublicationReader(Protocol):
    """The read-only Redis subset needed to observe current publications."""

    async def mget(self, keys: list[str]) -> list[str | None]: ...


class HeavyWorkGate(Protocol):
    """The cooperative pause/resume surface driven by the freshness guard."""

    def request_pause(self, reason: str) -> None: ...

    def allow_resume(self) -> None: ...


@dataclass(frozen=True, slots=True)
class RoomFreshness:
    """One room's publication freshness after a single check."""

    room: str
    key: str
    fresh: bool
    detail: str
    observed_at: datetime | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class FreshnessVerdict:
    """The combined freshness decision for one check cycle."""

    checked_at: datetime
    rooms: tuple[RoomFreshness, ...]
    heavy_work_paused: bool
    pause_reason: str | None
    fresh_streak: int

    @property
    def all_fresh(self) -> bool:
        return bool(self.rooms) and all(room.fresh for room in self.rooms)


@final
class PublicationFreshnessGuard:
    """Poll current publications and gate heavy work on their freshness.

    The guard starts paused: heavy work is allowed only after
    ``resume_fresh_checks`` consecutive fresh checks. Every stale condition
    (missing key, invalid payload, expired window, observation older than
    ``max_observation_age`` or a failed Redis read) pauses heavy work at the
    next cooperative checkpoint.
    """

    def __init__(
        self,
        reader: PublicationReader,
        gate: HeavyWorkGate,
        *,
        now: Callable[[], datetime] = _utc_now,
        interval_seconds: float = CHECK_INTERVAL_SECONDS,
        max_observation_age: timedelta = MAX_OBSERVATION_AGE,
        resume_fresh_checks: int = RESUME_FRESH_CHECKS,
        rooms: tuple[str, ...] = PUBLICATION_ROOMS,
    ) -> None:
        if not rooms:
            raise ValueError("rooms must not be empty")
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be > 0, got {interval_seconds}")
        if resume_fresh_checks < 1:
            raise ValueError(f"resume_fresh_checks must be >= 1, got {resume_fresh_checks}")
        self._reader = reader
        self._gate = gate
        self._now = now
        self._interval_seconds = interval_seconds
        self._max_observation_age = max_observation_age
        self._resume_fresh_checks = resume_fresh_checks
        self._rooms = rooms
        # Starts paused until three consecutive fresh checks accumulate; the
        # heavy-work control is created with the matching initial pause.
        self._paused = True
        self._fresh_streak = 0
        self._verdict: FreshnessVerdict | None = None

    @property
    def paused(self) -> bool:
        """True while heavy work is paused by this guard."""
        return self._paused

    @property
    def verdict(self) -> FreshnessVerdict | None:
        """The most recent verdict, or None before the first check."""
        return self._verdict

    async def poll_once(self) -> FreshnessVerdict:
        """Read both publications, evaluate freshness and update the gate."""
        now = self._now()
        keys = [monitoring_current_publication_key(room) for room in self._rooms]
        payloads: list[str | None]
        read_failed = False
        try:
            payloads = await self._reader.mget(keys)
        except Exception:
            logger.warning("freshness publication read failed", exc_info=True)
            payloads = [None] * len(keys)
            read_failed = True
        if len(payloads) != len(keys):
            payloads = [None] * len(keys)
            read_failed = True

        rooms = tuple(
            self._evaluate_room(room, key, payload, now, read_failed=read_failed)
            for room, key, payload in zip(self._rooms, keys, payloads, strict=True)
        )
        all_fresh = all(room.fresh for room in rooms)
        self._fresh_streak = self._fresh_streak + 1 if all_fresh else 0

        if not all_fresh and not self._paused:
            self._paused = True
            self._gate.request_pause(self._pause_reason_for(rooms))
        elif all_fresh and self._paused and self._fresh_streak >= self._resume_fresh_checks:
            self._paused = False
            self._gate.allow_resume()

        verdict = FreshnessVerdict(
            checked_at=now,
            rooms=rooms,
            heavy_work_paused=self._paused,
            pause_reason=self._pause_reason_for(rooms) if self._paused else None,
            fresh_streak=self._fresh_streak,
        )
        self._verdict = verdict
        if verdict.all_fresh and not verdict.heavy_work_paused:
            logger.debug("publications fresh: %s", [room.room for room in rooms])
        return verdict

    async def run(self, stop: asyncio.Event) -> None:
        """Poll once per interval until ``stop`` is set.

        Individual poll failures never stop the loop: they evaluate as stale
        publications, which keeps heavy work paused.
        """
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            started = loop.time()
            try:
                await self.poll_once()
            except Exception:
                logger.warning("freshness poll failed", exc_info=True)
            remaining = self._interval_seconds - (loop.time() - started)
            if remaining > 0:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=remaining)
                except TimeoutError:
                    pass

    def _evaluate_room(
        self,
        room: str,
        key: str,
        payload: str | None,
        now: datetime,
        *,
        read_failed: bool,
    ) -> RoomFreshness:
        if read_failed:
            return RoomFreshness(room=room, key=key, fresh=False, detail=DETAIL_READ_FAILED)
        if payload is None:
            return RoomFreshness(room=room, key=key, fresh=False, detail=DETAIL_ABSENT)
        try:
            snapshot = CurrentSnapshot.model_validate_json(payload)
        except ValidationError:
            return RoomFreshness(room=room, key=key, fresh=False, detail=DETAIL_INVALID)
        if snapshot.valid_until <= now:
            return RoomFreshness(
                room=room,
                key=key,
                fresh=False,
                detail=DETAIL_EXPIRED,
                observed_at=snapshot.observed_at,
                valid_until=snapshot.valid_until,
            )
        if now - snapshot.observed_at > self._max_observation_age:
            return RoomFreshness(
                room=room,
                key=key,
                fresh=False,
                detail=DETAIL_STALE,
                observed_at=snapshot.observed_at,
                valid_until=snapshot.valid_until,
            )
        return RoomFreshness(
            room=room,
            key=key,
            fresh=True,
            detail=DETAIL_FRESH,
            observed_at=snapshot.observed_at,
            valid_until=snapshot.valid_until,
        )

    @staticmethod
    def _pause_reason_for(rooms: tuple[RoomFreshness, ...]) -> str:
        for room in rooms:
            if not room.fresh:
                return room.detail
        return DETAIL_FRESHNESS_PENDING
