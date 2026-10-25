"""Setpoint Manager - Handles setpoint calculations and ramp transitions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any

from app.control.ramp_policy import DEFAULT_RAMP_SKIP_THRESHOLD, RAMP_SKIP_THRESHOLDS
from app.control.scheduler import LOCAL_TZ
from shared.infra_logging import LoggingContext, get_logger

if TYPE_CHECKING:
    from app.database import DatabaseManager
    from app.events.operational_ports import OperationalEventSink

logger = get_logger(__name__)


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


class SetpointManager:
    """Calculates effective setpoints with ramp transitions for climate periods."""

    def __init__(
        self,
        database: DatabaseManager | None = None,  # DatabaseManager for querying climate periods
        state_manager=None,  # StateManager (optional)
        event_sink: OperationalEventSink | None = None,
    ):
        """Initialize setpoint manager.

        Args:
            database: DatabaseManager for querying climate_periods
            state_manager: StateManager (optional)
            event_sink: Optional operational-event sink for ramp lifecycle events
        """
        self.database: DatabaseManager | None = database
        self.ramp_manager = RampManager(event_sink)
        self._state = state_manager  # type: ignore
        # Captured-profile ramp retarget authority per room.
        self._profile_identity: dict[tuple[str, str], Any] = {}
        self._profile_nominal: dict[tuple[str, str], dict[str, float | None]] = {}

    async def compute_effective_setpoints(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        current_period: str | None,
        setpoint_data: dict[str, Any],
        sensor_values: Mapping[str, float | None] | None = None,
        previous_period: str | None = None,
    ) -> dict[str, Any]:
        """Compute effective setpoints accounting for ramp transitions.

        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current timestamp
            current_period: Current climate period name
            setpoint_data: Setpoint data from climate_periods table
            sensor_values: Optional sensor values for ramp start initialization
            previous_period: Previous climate period name for transition detection

        Returns:
            Dict with effective/nominal setpoints and ramp progress values
        """
        with LoggingContext(operation="compute_effective_setpoints"):
            result = self._initialize_result_dict()

            # Get nominal setpoints
            nominal_values = self._extract_nominal_setpoints(setpoint_data)
            ramp_in_duration = setpoint_data.get("ramp_in_duration", 0) or 0

            # Store nominal values
            self._store_nominal_values(result, nominal_values)

            # Captured-profile identity: same-label profile/submode changes
            # or changed targets retarget; an unchanged set preserves ramps.
            room_key = (location, cluster)
            climate_identity = setpoint_data.get("climate_identity")
            prior_identity = self._profile_identity.get(room_key)
            prior_nominal = self._profile_nominal.get(room_key)
            identity_changed = (
                prior_identity is not None
                and climate_identity is not None
                and prior_identity != climate_identity
            )
            targets_changed = prior_nominal is not None and prior_nominal != nominal_values

            # Check for period transitions that require ramping
            period_changed = self._detect_mode_change(current_period, previous_period)

            if period_changed or identity_changed or targets_changed:
                # CRITICAL: Clear ALL stale ramps when the authority changes to
                # prevent sawtoothing. Snapshot the live interpolated values
                # first so the replacement ramps resume from where the
                # schedule actually was.
                in_flight = self.ramp_manager.current_ramp_values(location, cluster, current_time)
                self.ramp_manager.clear_ramps_for_room(location, cluster)

                if ramp_in_duration > 0:
                    await self._handle_mode_transition_ramp(
                        location,
                        cluster,
                        current_period,
                        nominal_values,
                        sensor_values,
                        ramp_in_duration,
                        current_time,
                        result,
                        previous_period,
                        in_flight,
                        setpoint_data.get("period_start_time"),
                        prior_requested=prior_nominal,
                    )
                else:
                    self._apply_nominal_values(result, nominal_values)
            elif (
                prior_nominal is None
                and ramp_in_duration > 0
                and not self.ramp_manager.has_active_ramps(location, cluster)
            ):
                # First evaluation of this room since process start: like light ramps,
                # re-derive an in-progress scheduled ramp from the clock.
                await self._resume_scheduled_ramps(
                    location,
                    cluster,
                    nominal_values,
                    ramp_in_duration,
                    current_time,
                    result,
                    setpoint_data,
                )
            elif self.ramp_manager.has_active_ramps(location, cluster):
                # Continue applying active ramps (no period change, but ramps still in progress)
                self._apply_ramp_values(location, cluster, result, nominal_values, current_time)
            else:
                # No ramping needed, use nominal values directly
                self._apply_nominal_values(result, nominal_values)

            # Track the authority consumed for this computation.
            if climate_identity is not None:
                self._profile_identity[room_key] = climate_identity
            self._profile_nominal[room_key] = nominal_values

            return result

    def _initialize_result_dict(self) -> dict[str, Any]:
        """Initialize the result dictionary with None values."""
        return {
            "effective_heating_setpoint": None,
            "effective_cooling_setpoint": None,
            "effective_humidity_setpoint": None,
            "effective_co2_setpoint": None,
            "effective_vpd_setpoint": None,
            "nominal_heating_setpoint": None,
            "nominal_cooling_setpoint": None,
            "nominal_humidity_setpoint": None,
            "nominal_co2_setpoint": None,
            "nominal_vpd_setpoint": None,
            "ramp_progress_heating": None,
            "ramp_progress_cooling": None,
            "ramp_progress_humidity": None,
            "ramp_progress_co2": None,
            "ramp_progress_vpd": None,
        }

    def _extract_nominal_setpoints(self, setpoint_data: dict[str, Any]) -> dict[str, float | None]:
        """Extract nominal setpoint values from database data."""
        return {
            "heating": setpoint_data.get("heating_setpoint"),
            "cooling": setpoint_data.get("cooling_setpoint"),
            "humidity": setpoint_data.get("humidity"),
            "co2": setpoint_data.get("co2"),
            "vpd": setpoint_data.get("vpd"),
        }

    def _store_nominal_values(
        self, result: dict[str, Any], nominal_values: dict[str, float | None]
    ) -> None:
        """Store nominal values in result dict."""
        result["nominal_heating_setpoint"] = nominal_values["heating"]
        result["nominal_cooling_setpoint"] = nominal_values["cooling"]
        result["nominal_humidity_setpoint"] = nominal_values["humidity"]
        result["nominal_co2_setpoint"] = nominal_values["co2"]
        result["nominal_vpd_setpoint"] = nominal_values["vpd"]

    def _detect_mode_change(self, current_period: str | None, previous_period: str | None) -> bool:
        """Detect if a period change occurred. Returns False on startup."""
        if previous_period is None:
            return False
        return current_period != previous_period

    async def _handle_mode_transition_ramp(
        self,
        location: str,
        cluster: str,
        current_period: str | None,
        nominal_values: Mapping[str, float | None],
        sensor_values: Mapping[str, float | None] | None,
        ramp_in_duration: float,
        current_time: datetime,
        result: dict[str, Any],
        previous_period: str | None = None,
        in_flight: Mapping[str, float] | None = None,
        period_start_time: str | None = None,
        *,
        prior_requested: Mapping[str, float | None] | None = None,
    ) -> None:
        """Handle ramp transitions when the schedule authority changes.

        With in-flight values the replacement starts at the observation time
        with only the remaining duration to the scheduled deadline: no value
        jump and no extension past that deadline. Without in-flight values the
        late-observation policy anchors at the scheduled boundary with the
        configured duration.
        """
        if current_period is None:
            return

        anchor = self._scheduled_boundary(current_time, period_start_time)
        deadline = anchor + timedelta(minutes=ramp_in_duration)
        if current_time >= deadline:
            # The scheduled ramp window already elapsed (e.g. an outage spanned
            # the transition): the schedule already reached the target.
            logger.info(
                f"RAMP WINDOW ELAPSED: {location}/{cluster} - applying nominal "
                f"{current_period} setpoints directly"
            )
            self._apply_nominal_values(result, nominal_values)
            return

        remaining_minutes = max(0.0, (deadline - current_time).total_seconds() / 60.0)

        logger.debug(
            f"RAMP DEBUG: {location}/{cluster} - period_changed=True, current_period={current_period}"
        )

        # Determine ramp start values based on the interrupted ramp, the last
        # requested prior-profile values, the previous period, then sensors.
        ramp_starts = await self._calculate_ramp_start_values(
            location,
            cluster,
            current_period,
            nominal_values,
            sensor_values,
            previous_period,
            in_flight,
            prior_requested=prior_requested,
        )

        # Start ramps for each setpoint type
        setpoint_types = ["heating", "cooling", "humidity", "co2", "vpd"]
        ramps_started = []
        for setpoint_type in setpoint_types:
            nominal_value = nominal_values[setpoint_type]
            start_value = ramp_starts.get(setpoint_type)

            if nominal_value is not None and start_value is not None:
                if in_flight:
                    # Resume at the live value toward the original deadline.
                    duration_minutes = remaining_minutes
                    ramp_time = current_time
                else:
                    duration_minutes = ramp_in_duration
                    ramp_time = anchor
                self.ramp_manager.start_ramp(
                    location,
                    cluster,
                    setpoint_type,
                    start_value,
                    nominal_value,
                    duration_minutes,
                    ramp_time,
                )
                ramps_started.append(setpoint_type)
            elif nominal_value is None:
                logger.debug(
                    f"RAMP SKIP: {location}/{cluster} {setpoint_type} - nominal_value is None"
                )
            elif start_value is None:
                logger.debug(
                    f"RAMP SKIP: {location}/{cluster} {setpoint_type} - start_value is None"
                )

        if ramps_started:
            logger.info(
                f"RAMPS INITIATED: {location}/{cluster} {ramps_started} over {ramp_in_duration}min"
            )
        else:
            logger.warning(f"NO RAMPS STARTED: {location}/{cluster} - check setpoint configuration")

        # Apply current ramp values
        self._apply_ramp_values(location, cluster, result, nominal_values, current_time)

    def _scheduled_boundary(
        self, current_time: datetime, period_start_time: str | None
    ) -> datetime:
        """Resolve the scheduled period boundary as the ramp anchor.

        The climate_periods row stores a local wall-clock ``HH:MM`` start. The
        anchor is the most recent occurrence of that wall clock at or before the
        control loop's time, so ramp progress matches the schedule trajectory
        even when a transition is observed late. Falls back to the observation
        time when the row carries no usable start.
        """
        if not period_start_time:
            return current_time
        try:
            hour_text, minute_text = str(period_start_time).split(":")[:2]
            hour, minute = int(hour_text), int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, TypeError):
            logger.warning(
                f"RAMP ANCHOR: unusable period start {period_start_time!r}; anchoring at observation time"
            )
            return current_time

        local_now = current_time if current_time.tzinfo else current_time.replace(tzinfo=LOCAL_TZ)
        local_now = local_now.astimezone(LOCAL_TZ)
        boundary = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if boundary > local_now:
            boundary -= timedelta(days=1)
        anchor = boundary.astimezone(current_time.tzinfo or LOCAL_TZ)
        if current_time.tzinfo is None:
            anchor = anchor.replace(tzinfo=None)
        if anchor > current_time:  # defensive: never anchor in the future
            anchor = current_time
        return anchor

    async def _resume_scheduled_ramps(
        self,
        location: str,
        cluster: str,
        nominal_values: Mapping[str, float | None],
        ramp_in_duration: float,
        current_time: datetime,
        result: dict[str, Any],
        setpoint_data: Mapping[str, Any],
    ) -> None:
        """Resume the current period's scheduled ramp at its clock position.

        Like light ramps, the position comes from the scheduled boundary and the
        cyclic predecessor period's targets; nothing is carried over from an
        earlier run. Outside the ramp window the nominal values apply.
        """
        period_start_time = setpoint_data.get("period_start_time")
        if not period_start_time:
            self._apply_nominal_values(result, nominal_values)
            return
        anchor = self._scheduled_boundary(current_time, period_start_time)
        deadline = anchor + timedelta(minutes=ramp_in_duration)
        if current_time >= deadline:
            self._apply_nominal_values(result, nominal_values)
            return

        sources = await self._scheduled_ramp_sources(location, cluster, setpoint_data)
        resumed: list[str] = []
        for setpoint_type in ("heating", "cooling", "humidity", "co2", "vpd"):
            target = nominal_values[setpoint_type]
            source = sources.get(setpoint_type)
            if target is None or source is None:
                continue
            threshold = RAMP_SKIP_THRESHOLDS.get(setpoint_type, DEFAULT_RAMP_SKIP_THRESHOLD)
            if abs(target - source) < threshold:
                continue
            self.ramp_manager.resume_ramp(
                location,
                cluster,
                setpoint_type,
                source,
                target,
                ramp_in_duration,
                anchor,
                current_time,
            )
            resumed.append(setpoint_type)
        if resumed:
            logger.info(
                f"RAMP RESUMED: {location}/{cluster} {resumed} from {anchor.isoformat()} "
                f"over {ramp_in_duration}min"
            )
        self._apply_ramp_values(location, cluster, result, nominal_values, current_time)

    async def _scheduled_ramp_sources(
        self, location: str, cluster: str, setpoint_data: Mapping[str, Any]
    ) -> dict[str, float | None]:
        """Targets of the period that hands off to the current one.

        Same cyclic predecessor rule as the published trajectory: rows of the
        exact captured profile ordered by start clock, previous row, wrapping. A
        single-period profile is its own predecessor (no ramp).
        """
        identity = setpoint_data.get("climate_identity")
        if self.database is None or not isinstance(identity, tuple | list) or len(identity) != 5:
            return {}
        mode_id, submode_id, _, start_hhmm, _ = identity
        if mode_id is None:
            return {}
        rows = await self.database.climate_periods_repo.get_periods(
            location, cluster, mode_id, submode_id
        )
        ordered = sorted(
            (
                (minute, row)
                for row in rows
                if (minute := self._start_minute(row.get("start_time"))) is not None
            ),
            key=lambda item: item[0],
        )
        current_minute = self._start_minute(start_hhmm)
        for index, (minute, _row) in enumerate(ordered):
            if minute != current_minute:
                continue
            prev = ordered[index - 1][1]
            return {
                "heating": _as_float(prev.get("heating_setpoint")),
                "cooling": _as_float(prev.get("cooling_setpoint")),
                "humidity": None,  # VPD cascade derives humidity
                "co2": _as_float(prev.get("co2_setpoint")),
                "vpd": _as_float(prev.get("vpd_setpoint")),
            }
        return {}

    @staticmethod
    def _start_minute(value: object) -> int | None:
        """Minute of day of a ``time`` or ``HH:MM[:SS]`` start, else ``None``."""
        if isinstance(value, time):
            return value.hour * 60 + value.minute
        try:
            hour_text, minute_text = str(value).split(":")[:2]
            return int(hour_text) * 60 + int(minute_text)
        except (ValueError, TypeError):
            return None

    async def _calculate_ramp_start_values(
        self,
        location: str,
        cluster: str,
        current_period: str,
        nominal_values: Mapping[str, float | None],
        sensor_values: Mapping[str, float | None] | None,
        previous_period: str | None = None,
        in_flight: Mapping[str, float] | None = None,
        *,
        prior_requested: Mapping[str, float | None] | None = None,
    ) -> dict[str, float | None]:
        """Calculate starting values for ramps based on previous period.

        An interrupted ramp resumes from its live interpolated value. The last
        requested prior-profile values take precedence over a climate_periods
        lookup by a shared period label, because the label match may belong to
        another profile's parameters.
        """
        ramp_starts: dict[str, float | None] = {}
        if in_flight:
            ramp_starts.update(in_flight)

        def _extract_setpoints_from_period(period_data: dict[str, Any]) -> dict[str, float | None]:
            """Helper to extract setpoint values from climate period record."""
            return {
                "heating": period_data.get("heating_setpoint"),
                "cooling": period_data.get("cooling_setpoint"),
                "humidity": None,  # VPD cascade derives humidity
                "co2": period_data.get("co2_setpoint"),
                "vpd": period_data.get("vpd_setpoint"),
            }

        def _fill_missing(sources: Mapping[str, Any]) -> None:
            """Fill only setpoint types the interrupted-ramp snapshot did not provide."""
            for setpoint_type, value in sources.items():
                if ramp_starts.get(setpoint_type) is None:
                    ramp_starts[setpoint_type] = value

        # Prefer last known requested values for the prior profile over a DB
        # lookup by a shared period label; the label match may belong to
        # another profile's parameters.
        if prior_requested is not None:
            _fill_missing(
                {
                    "heating": prior_requested.get("heating"),
                    "cooling": prior_requested.get("cooling"),
                    "humidity": prior_requested.get("humidity"),
                    "co2": prior_requested.get("co2"),
                    "vpd": prior_requested.get("vpd"),
                }
            )
            logger.info(
                f"RAMP: Prior-profile requested ramp starts for {location}/{cluster}: {ramp_starts}"
            )
            return ramp_starts

        # If we know the previous period name, look it up in climate_periods table
        if previous_period and self.database is not None:
            logger.debug(
                f"RAMP: {previous_period} -> {current_period}, fetching {previous_period} period as start"
            )
            periods = await self.database.climate_periods_repo.get_periods(location, cluster)
            for period in periods:
                if period.get("period_name") == previous_period:
                    _fill_missing(_extract_setpoints_from_period(period))
                    logger.info(
                        f"RAMP: Ramp starts for {previous_period} -> {current_period}: {ramp_starts}"
                    )
                    return ramp_starts
            logger.warning(
                f"RAMP: Previous period {previous_period} not found for {location}/{cluster}"
            )

        # Fallback: use sensor values if available
        if sensor_values:
            logger.debug(
                f"RAMP: Using sensor values as ramp start fallback for {location}/{cluster}"
            )
            _fill_missing(
                {
                    "heating": sensor_values.get("dry_bulb"),
                    "cooling": sensor_values.get("dry_bulb"),
                    "humidity": sensor_values.get("humidity"),
                    "co2": sensor_values.get("co2"),
                    "vpd": sensor_values.get("vpd"),
                }
            )
            logger.info(f"RAMP: Using ramp starts: {ramp_starts}")
            return ramp_starts

        # Ultimate fallback: use nominal values (target = start, so no actual ramp)
        logger.warning(
            f"RAMP: No previous period or sensor data for {location}/{cluster}, using nominal values"
        )
        _fill_missing(
            {
                "heating": nominal_values["heating"],
                "cooling": nominal_values["cooling"],
                "humidity": nominal_values["humidity"],
                "co2": nominal_values["co2"],
                "vpd": nominal_values["vpd"],
            }
        )
        return ramp_starts

    def _apply_ramp_values(
        self,
        location: str,
        cluster: str,
        result: dict[str, Any],
        nominal_values: Mapping[str, float | None],
        current_time: datetime,
    ) -> None:
        """Apply current ramp values to result dict for a specific room."""
        setpoint_types = ["heating", "cooling", "humidity", "co2", "vpd"]

        for setpoint_type in setpoint_types:
            nominal_value = nominal_values[setpoint_type]
            if nominal_value is not None:
                effective_value, progress = self.ramp_manager.get_ramp_value(
                    location, cluster, setpoint_type, nominal_value, current_time
                )

                result[f"effective_{setpoint_type}_setpoint"] = effective_value
                result[f"ramp_progress_{setpoint_type}"] = progress

    def _apply_nominal_values(
        self, result: dict[str, Any], nominal_values: dict[str, float | None]
    ) -> None:
        """Apply nominal values directly (no ramping)."""
        result["effective_heating_setpoint"] = nominal_values["heating"]
        result["effective_cooling_setpoint"] = nominal_values["cooling"]
        result["effective_humidity_setpoint"] = nominal_values["humidity"]
        result["effective_co2_setpoint"] = nominal_values["co2"]
        result["effective_vpd_setpoint"] = nominal_values["vpd"]


def _as_float(value: object) -> float | None:
    """Numeric column value as ``float``; anything else (including ``bool``) is ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
