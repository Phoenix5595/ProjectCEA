"""Setpoint ramp transitions: RampState per room setpoint and the RampManager that owns them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.control.ramp_policy import DEFAULT_RAMP_SKIP_THRESHOLD, RAMP_SKIP_THRESHOLDS
from shared.infra_logging import get_logger

if TYPE_CHECKING:
    from app.events.operational_ports import OperationalEventSink

logger = get_logger("app.control.setpoint_manager")


class RampState:
    """Represents the state of a ramp transition."""

    setpoint_type: str
    start_value: float
    target_value: float
    duration_minutes: float
    start_time: datetime
    midpoint_emitted: bool

    def __init__(
        self,
        setpoint_type: str,
        start_value: float,
        target_value: float,
        duration_minutes: float,
        start_time: datetime,
    ):
        self.setpoint_type = setpoint_type
        self.start_value = start_value
        self.target_value = target_value
        self.duration_minutes = duration_minutes
        self.start_time = start_time
        self.midpoint_emitted = False

    @property
    def end_time(self) -> datetime:
        """Get the end time of the ramp."""
        return self.start_time + timedelta(minutes=self.duration_minutes)

    def get_current_value(self, current_time: datetime) -> float:
        """Get the current interpolated value based on time progress."""
        if current_time >= self.end_time:
            return self.target_value

        elapsed_minutes = (current_time - self.start_time).total_seconds() / 60.0
        progress = min(elapsed_minutes / self.duration_minutes, 1.0)

        # Linear interpolation
        return self.start_value + (self.target_value - self.start_value) * progress

    def is_complete(self, current_time: datetime) -> bool:
        """Check if the ramp is complete."""
        return current_time >= self.end_time


class RampManager:
    """Manages ramp transitions for setpoint changes."""

    def __init__(self, event_sink: OperationalEventSink | None = None) -> None:
        """Initialize ramp manager."""
        # Key: (location, cluster, setpoint_type) - ensures room isolation
        self.active_ramps: dict[tuple[str, str, str], RampState] = {}
        self._event_sink = event_sink

    def _emit_lifecycle(
        self,
        event_type: str,
        ramp: RampState,
        location: str,
        cluster: str,
        occurred_at: datetime,
        reason_code: str,
    ) -> None:
        if self._event_sink is None:
            return
        from app.events.operational_models import (
            EntityContext,
            EventCategory,
            EventSeverity,
            EventSource,
            OperationalEvent,
            RampPayload,
        )

        timestamp = (
            occurred_at.astimezone(UTC) if occurred_at.tzinfo else occurred_at.replace(tzinfo=UTC)
        )
        self._event_sink.emit_nowait(
            OperationalEvent(
                occurred_at=timestamp,
                source=EventSource.AUTOMATION,
                category=EventCategory.RAMP,
                severity=EventSeverity.INFO,
                event_type=event_type,
                entity=EntityContext(
                    entity_type="setpoint",
                    entity_id=ramp.setpoint_type,
                    location=location,
                    cluster=cluster,
                ),
                reason_code=reason_code,
                payload=RampPayload(
                    ramp_type=f"setpoint:{ramp.setpoint_type}",
                    start_value=ramp.start_value,
                    target_value=ramp.target_value,
                    duration_seconds=round(ramp.duration_minutes * 60),
                ),
            )
        )

    def _make_key(self, location: str, cluster: str, setpoint_type: str) -> tuple[str, str, str]:
        """Create composite key for room-isolated ramp storage."""
        return (location, cluster, setpoint_type)

    def start_ramp(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        start_value: float,
        target_value: float,
        duration_minutes: float,
        current_time: datetime,
    ) -> None:
        """Start a new ramp transition.

        Args:
            setpoint_type: Type of setpoint (heating, cooling, etc.)
            start_value: Starting setpoint value
            target_value: Target setpoint value
            duration_minutes: Duration of ramp in minutes
            current_time: Current timestamp
        """
        ramp_key = self._make_key(location, cluster, setpoint_type)
        threshold = RAMP_SKIP_THRESHOLDS.get(setpoint_type, DEFAULT_RAMP_SKIP_THRESHOLD)
        delta = abs(target_value - start_value)

        previous_ramp = self.active_ramps.get(ramp_key)
        if previous_ramp is not None:
            self._emit_lifecycle(
                "ramp.interrupted",
                previous_ramp,
                location,
                cluster,
                current_time,
                "target_replaced",
            )

        if duration_minutes <= 0 or delta < threshold:
            # No ramp needed - but MUST cancel any existing ramp to prevent stale data
            if ramp_key in self.active_ramps:
                del self.active_ramps[ramp_key]
                logger.debug(
                    f"RAMP SKIPPED: {location}/{cluster} {setpoint_type} "
                    f"delta={delta:.3f} < threshold={threshold}, cleared stale ramp"
                )
            instant_ramp = RampState(
                setpoint_type, start_value, target_value, duration_minutes, current_time
            )
            self._emit_lifecycle(
                "ramp.started", instant_ramp, location, cluster, current_time, "instant_target"
            )
            self._emit_lifecycle(
                "ramp.completed", instant_ramp, location, cluster, current_time, "instant_target"
            )
            return

        ramp = RampState(setpoint_type, start_value, target_value, duration_minutes, current_time)
        self.active_ramps[ramp_key] = ramp

        logger.info(
            f"RAMP START: {location}/{cluster} {setpoint_type} from {start_value} to {target_value} "
            f"over {duration_minutes} minutes"
        )
        self._emit_lifecycle(
            "ramp.started", ramp, location, cluster, current_time, "period_transition"
        )

    def get_ramp_value(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        nominal_value: float,
        current_time: datetime,
    ) -> tuple[float, float | None]:
        """Get the current ramp value and progress for a setpoint type.

        Args:
            setpoint_type: Type of setpoint
            nominal_value: The nominal (target) value
            current_time: Current timestamp

        Returns:
            Tuple of (effective_value, progress_ratio)
        """
        ramp_key = self._make_key(location, cluster, setpoint_type)
        ramp = self.active_ramps.get(ramp_key)

        if ramp and not ramp.is_complete(current_time):
            current_value = ramp.get_current_value(current_time)
            elapsed = (current_time - ramp.start_time).total_seconds() / 60.0
            progress = min(elapsed / ramp.duration_minutes, 1.0)
            if progress >= 0.5 and not ramp.midpoint_emitted:
                ramp.midpoint_emitted = True
                self._emit_lifecycle(
                    "ramp.midpoint_reached",
                    ramp,
                    location,
                    cluster,
                    current_time,
                    "midpoint_crossed",
                )
            return current_value, progress
        elif ramp and ramp.is_complete(current_time):
            del self.active_ramps[ramp_key]
            logger.info(
                f"RAMP COMPLETE: {location}/{cluster} {setpoint_type} reached {nominal_value}"
            )
            self._emit_lifecycle(
                "ramp.completed", ramp, location, cluster, current_time, "target_reached"
            )

        return nominal_value, None

    def cancel_ramp(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        current_time: datetime | None = None,
    ) -> None:
        """Cancel an active ramp for a specific room."""
        ramp_key = self._make_key(location, cluster, setpoint_type)
        if ramp_key in self.active_ramps:
            ramp = self.active_ramps.pop(ramp_key)
            logger.info(f"RAMP CANCELLED: {location}/{cluster} {setpoint_type}")
            self._emit_lifecycle(
                "ramp.cancelled",
                ramp,
                location,
                cluster,
                current_time or datetime.now(tz=UTC),
                "cancelled",
            )

    def current_ramp_values(
        self, location: str, cluster: str, current_time: datetime
    ) -> dict[str, float]:
        """Snapshot the live interpolated value of every unfinished room ramp.

        Callers snapshot BEFORE clearing a room's ramps so an interrupted
        transition can resume from where the schedule actually was instead of
        re-reading the ended period's table values.
        """
        snapshot: dict[str, float] = {}
        for ramp_key, ramp in self.active_ramps.items():
            if ramp_key[0] != location or ramp_key[1] != cluster:
                continue
            if ramp.is_complete(current_time):
                continue
            snapshot[ramp_key[2]] = ramp.get_current_value(current_time)
        return snapshot

    def has_active_ramps(self, location: str | None = None, cluster: str | None = None) -> bool:
        """Check if any ramps are currently active, optionally filtered by room.

        Args:
            location: Optional location filter
            cluster: Optional cluster filter

        Returns:
            True if active ramps exist (matching filter if provided)
        """
        if location is None and cluster is None:
            return len(self.active_ramps) > 0

        for ramp_key in self.active_ramps:
            if location is not None and ramp_key[0] != location:
                continue
            if cluster is not None and ramp_key[1] != cluster:
                continue
            return True
        return False

    def update_ramp_target(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        new_target: float,
        current_time: datetime,
    ) -> None:
        """Update ramp target mid-ramp - adjusts rate to hit new target by original end time.

        Args:
            setpoint_type: Type of setpoint (heating, cooling, etc.)
            new_target: New target value
            current_time: Current timestamp
        """
        ramp_key = self._make_key(location, cluster, setpoint_type)
        ramp = self.active_ramps.get(ramp_key)
        if ramp and not ramp.is_complete(current_time):
            current_value = ramp.get_current_value(current_time)
            remaining_seconds = (ramp.end_time - current_time).total_seconds()
            remaining_minutes = max(remaining_seconds / 60.0, 0.1)  # Minimum 6 seconds

            # Preserve original end time
            _original_end_time = ramp.end_time

            # Update: new start = current value, new target, same end time
            ramp.start_value = current_value
            ramp.target_value = new_target
            ramp.start_time = current_time
            ramp.duration_minutes = remaining_minutes

            logger.info(
                f"RAMP ADJUSTED: {location}/{cluster} {setpoint_type} {current_value:.2f} -> {new_target:.2f} "
                f"in {remaining_minutes:.1f}min remaining"
            )

    def clear_ramps_for_room(self, location: str, cluster: str) -> None:
        """Clear all active ramps for a specific room - used on mode change."""
        setpoint_types = ["heating", "cooling", "humidity", "co2", "vpd"]
        cleared: list[str] = []
        for setpoint_type in setpoint_types:
            ramp_key = self._make_key(location, cluster, setpoint_type)
            if ramp_key in self.active_ramps:
                ramp = self.active_ramps.pop(ramp_key)
                self._emit_lifecycle(
                    "ramp.interrupted",
                    ramp,
                    location,
                    cluster,
                    datetime.now(tz=UTC),
                    "target_replaced",
                )
                cleared.append(setpoint_type)
        if cleared:
            logger.info(f"RAMPS CLEARED for {location}/{cluster}: {cleared}")

    def resume_ramp(
        self,
        location: str,
        cluster: str,
        setpoint_type: str,
        start_value: float,
        target_value: float,
        duration_minutes: float,
        start_time: datetime,
        current_time: datetime,
    ) -> None:
        """Re-derive a scheduled ramp that began before this process from the clock.

        Emits no lifecycle event (the ramp started in an earlier run) and persists
        nothing; a complete ramp is not stored.
        """
        ramp = RampState(setpoint_type, start_value, target_value, duration_minutes, start_time)
        if ramp.is_complete(current_time):
            return
        ramp.midpoint_emitted = (
            current_time - start_time
        ).total_seconds() / 60.0 / duration_minutes >= 0.5
        self.active_ramps[self._make_key(location, cluster, setpoint_type)] = ramp
