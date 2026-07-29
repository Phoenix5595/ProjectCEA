"""Single owner of observed MCP23017 relay-board state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from typing import Protocol

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


class RelayBoardStateManager:
    """Samples MCP state and persists only initial or changed board snapshots."""

    def __init__(self, mcp23017: RelayBoardSampler, redis: RelayBoardRedis | None = None) -> None:
        self._mcp23017 = mcp23017
        self._redis = redis
        self._snapshot = RelayBoardSnapshot(None, None, (None,) * 16)
        self._last_persisted_channels: tuple[bool, ...] | None = None

    def get_snapshot(self) -> RelayBoardSnapshot:
        """Return the latest successfully observed board state."""
        return self._snapshot

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
            logger.warning("Relay board sample failed; retaining the last successful snapshot")
            return False
        if len(channels) != 16:
            logger.warning(
                "Relay board sample had %s channels; retaining last successful snapshot",
                len(channels),
            )
            return False

        sampled_at = datetime.now(UTC)
        changed_at = self._changed_at_for_sample(channels, sampled_at)
        self._snapshot = RelayBoardSnapshot(channels, sampled_at, changed_at)

        if (
            force_persist
            or self._last_persisted_channels is None
            or channels != self._last_persisted_channels
        ):
            self._persist_snapshot()
            self._last_persisted_channels = channels
        return True

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
