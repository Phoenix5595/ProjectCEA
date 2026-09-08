"""Single owner of observed MCP23017 relay-board state."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Literal, Protocol
from uuid import UUID

from app.events.operational_models import (
    EntityContext,
    EventCategory,
    EventSeverity,
    EventSource,
    OperationalEvent,
    RelayPayload,
)
from app.events.operational_ports import OperationalEventSink
from app.redis.schema import RELAY_BOARD_SNAPSHOT
from shared.infra_logging import get_logger

logger = get_logger(__name__)


class RelayBoardSampler(Protocol):
    """Read all MCP relay channels in one two-register sample."""

    def sample_all_channels(self) -> tuple[bool, ...] | None: ...


class RelayBoardRedis(Protocol):
    """Minimal synchronous Redis boundary required for board persistence."""

    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class RelayBoardSnapshot:
    """Latest successful GPIO observation and its channel transition times."""

    channels: tuple[bool, ...] | None
    sampled_at: datetime | None
    changed_at: tuple[datetime | None, ...]


@dataclass(frozen=True, slots=True)
class RelayBoardFreshness:
    """Freshness of the last MCP observation without replacing last-good GPIO values."""

    status: Literal["FRESH", "STALE"]
    stale_since: datetime | None


class RelayBoardStateManager:
    """Samples MCP state and persists only initial or changed board snapshots."""

    def __init__(
        self,
        mcp23017: RelayBoardSampler,
        redis: RelayBoardRedis | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        event_sink: OperationalEventSink | None = None,
    ) -> None:
        self._mcp23017 = mcp23017
        self._redis = redis
        self._now = now
        self._event_sink = event_sink
        self._snapshot = RelayBoardSnapshot(None, None, (None,) * 16)
        self._last_persisted_channels: tuple[bool, ...] | None = None
        self._stale_since: datetime | None = None
        self._command_correlations: dict[int, tuple[bool, UUID]] = {}

    def record_command_correlation(self, channel: int, state: bool, correlation_id: UUID) -> None:
        """Suppress an observation event when it confirms the supplied command."""
        self._command_correlations[channel] = (state, correlation_id)

    def get_snapshot(self) -> RelayBoardSnapshot:
        """Return the latest successfully observed board state."""
        return self._snapshot

    def get_freshness(self) -> RelayBoardFreshness:
        """Expose whether a fresh MCP read has succeeded since the last failure."""
        if self._stale_since is None:
            return RelayBoardFreshness(status="FRESH", stale_since=None)
        return RelayBoardFreshness(status="STALE", stale_since=self._stale_since)

    async def on_startup_restore(self) -> bool:
        """Restore persisted timestamps, then reconcile them with live GPIO state."""
        self._restore_persisted_snapshot()
        return await self.sample(force_persist=True)

    async def on_write_done(self) -> bool:
        """Sample the board after a successful direct MCP write."""
        return await self.sample()

    async def sample(self, *, force_persist: bool = False) -> bool:
        """Observe GPIOA/GPIOB once each and retain the last valid state on failure."""
        channels = await asyncio.to_thread(self._mcp23017.sample_all_channels)
        if channels is None:
            was_fresh = self._stale_since is None
            self._mark_stale()
            if was_fresh:
                self._emit_observation_failure()
            logger.warning("Relay board sample failed; retaining the last successful snapshot")
            return False
        if len(channels) != 16:
            was_fresh = self._stale_since is None
            self._mark_stale()
            if was_fresh:
                self._emit_observation_failure()
            logger.warning(
                "Relay board sample had %s channels; retaining last successful snapshot",
                len(channels),
            )
            return False

        was_stale = self._stale_since is not None
        sampled_at = self._now()
        changed_at = self._changed_at_for_sample(channels, sampled_at)
        previous_channels = self._snapshot.channels
        self._snapshot = RelayBoardSnapshot(channels, sampled_at, changed_at)
        self._stale_since = None
        self._emit_observation_transitions(channels, previous_channels)
        if was_stale:
            self._emit_observation_recovery()

        if (
            force_persist
            or self._last_persisted_channels is None
            or channels != self._last_persisted_channels
        ):
            self._persist_snapshot()
            self._last_persisted_channels = channels
        return True

    def _mark_stale(self) -> None:
        """Record the first failed read in a contiguous stale period."""
        if self._stale_since is None:
            self._stale_since = self._now()

    def _emit_observation_transitions(
        self, channels: tuple[bool, ...], previous_channels: tuple[bool, ...] | None
    ) -> None:
        if previous_channels is None:
            for channel, state in enumerate(channels):
                expected = self._command_correlations.get(channel)
                if expected is not None and expected[0] == state:
                    del self._command_correlations[channel]
            return
        for channel, state in enumerate(channels):
            expected = self._command_correlations.get(channel)
            if expected is not None and expected[0] == state:
                del self._command_correlations[channel]
                continue
            if state != previous_channels[channel]:
                self._emit_relay_event("relay.observed", channel, state)

    def _emit_observation_failure(self) -> None:
        self._emit_relay_event("relay.observation_failed", None, None, EventSeverity.WARNING)

    def _emit_observation_recovery(self) -> None:
        self._emit_relay_event("relay.observation_recovered", None, None)

    def _emit_relay_event(
        self,
        event_type: str,
        channel: int | None,
        observed_state: bool | None,
        severity: EventSeverity = EventSeverity.INFO,
    ) -> None:
        if self._event_sink is None:
            return
        entity = (
            EntityContext(entity_type="relay_channel", entity_id=str(channel))
            if channel is not None
            else EntityContext(entity_type="relay_board", entity_id="mcp23017")
        )
        self._event_sink.emit_nowait(
            OperationalEvent(
                occurred_at=self._now(),
                source=EventSource.AUTOMATION,
                category=EventCategory.RELAY,
                severity=severity,
                event_type=event_type,
                entity=entity,
                payload=RelayPayload(observed_state=observed_state),
            )
        )

    def _changed_at_for_sample(
        self, channels: tuple[bool, ...], sampled_at: datetime
    ) -> tuple[datetime | None, ...]:
        if self._snapshot.channels is None:
            return (sampled_at,) * 16
        return tuple(
            sampled_at if channel != previous else changed_at
            for channel, previous, changed_at in zip(
                channels, self._snapshot.channels, self._snapshot.changed_at, strict=True
            )
        )

    def _restore_persisted_snapshot(self) -> None:
        if self._redis is None:
            return
        try:
            raw = self._redis.get(RELAY_BOARD_SNAPSHOT)
            if raw is None:
                return
            parsed = json.loads(raw)
            channels_raw = parsed.get("channels")
            sampled_at_raw = parsed.get("sampled_at")
            changed_at_raw = parsed.get("changed_at")
            if not isinstance(channels_raw, list) or len(channels_raw) != 16:
                return
            if not isinstance(changed_at_raw, list) or len(changed_at_raw) != 16:
                return
            if not isinstance(sampled_at_raw, str):
                return
            channels = tuple(bool(channel) for channel in channels_raw)
            sampled_at = datetime.fromisoformat(sampled_at_raw.replace("Z", "+00:00"))
            changed_at = tuple(
                datetime.fromisoformat(value.replace("Z", "+00:00"))
                if isinstance(value, str)
                else None
                for value in changed_at_raw
            )
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            logger.warning("Ignoring invalid persisted relay board snapshot: %s", error)
            return

        self._snapshot = RelayBoardSnapshot(channels, sampled_at, changed_at)
        self._last_persisted_channels = channels

    def _persist_snapshot(self) -> None:
        if (
            self._redis is None
            or self._snapshot.channels is None
            or self._snapshot.sampled_at is None
        ):
            return
        payload = {
            "channels": list(self._snapshot.channels),
            "sampled_at": self._iso(self._snapshot.sampled_at),
            "changed_at": [
                self._iso(value) if value is not None else None
                for value in self._snapshot.changed_at
            ],
        }
        try:
            self._redis.set(RELAY_BOARD_SNAPSHOT, json.dumps(payload, separators=(",", ":")))
        except (ConnectionError, OSError) as error:
            logger.warning("Failed to persist relay board snapshot: %s", error)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
