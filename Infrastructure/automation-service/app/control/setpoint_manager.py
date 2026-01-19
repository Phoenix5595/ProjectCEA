"""Setpoint Manager - Handles setpoint calculations and ramp transitions."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.database import DatabaseManager
from shared.logging import LoggingContext, get_logger

logger = get_logger(__name__)


class RampState:
    """Represents the state of a ramp transition."""

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

    # Setpoint-type aware skip thresholds - ramps are skipped if delta is below these
    SKIP_THRESHOLDS: dict[str, float] = {
        "heating": 0.1,  # °C - temperature precision
        "cooling": 0.1,  # °C - temperature precision
        "vpd": 0.01,  # kPa - VPD precision (2 decimal places)
        "co2": 10.0,  # ppm - CO2 precision
        "humidity": 1.0,  # % - humidity precision
    }

    def __init__(self, redis_client=None):
        """Initialize ramp manager."""
        # Key: (location, cluster, setpoint_type) - ensures room isolation
        self.active_ramps: dict[tuple[str, str, str], RampState] = {}
        self._redis = redis_client

    def set_redis(self, redis_client) -> None:
        """Set Redis client for ramp persistence."""
        self._redis = redis_client

    def _make_key(self, location: str, cluster: str, setpoint_type: str) -> tuple[str, str, str]:
        """Create composite key for room-isolated ramp storage."""
        return (location, cluster, setpoint_type)

    def _persist_ramp(self, location: str, cluster: str, ramp: RampState) -> None:
        """Persist ramp state to Redis using sync method."""
        if not self._redis:
            return
        try:
            self._redis.persist_ramp(
                location,
                cluster,
                ramp.setpoint_type,
                ramp.start_value,
                ramp.target_value,
                ramp.duration_minutes,
                ramp.start_time,
            )
        except Exception as e:
            logger.warning(f"Failed to persist ramp: {e}")

    def _clear_persisted_ramp(self, location: str, cluster: str, setpoint_type: str) -> None:
        """Clear persisted ramp from Redis."""
        if not self._redis:
            return
        try:
            self._redis.clear_persisted_ramp(location, cluster, setpoint_type)
        except Exception as e:
            logger.warning(f"Failed to clear persisted ramp: {e}")

    def restore_ramps_from_redis(self) -> int:
        """Restore active ramps from Redis on startup. Returns count of restored ramps."""
        if not self._redis:
            logger.warning("No Redis client - cannot restore ramps")
            return 0

        try:
            ramps = self._redis.get_persisted_ramps()
            restored = 0
            now = datetime.now()

            for ramp_data in ramps:
                try:
                    location = ramp_data["location"]
                    cluster = ramp_data["cluster"]
                    setpoint_type = ramp_data["setpoint_type"]
                    start_time = ramp_data["start_time"]
                    duration = ramp_data["duration_minutes"]

                    ramp_key = self._make_key(location, cluster, setpoint_type)
                    self.active_ramps[ramp_key] = RampState(
                        setpoint_type,
                        ramp_data["start_value"],
                        ramp_data["target_value"],
                        duration,
                        start_time,
                    )

                    elapsed = (now - start_time).total_seconds() / 60.0
                    progress = min(elapsed / duration * 100, 100)
                    logger.info(
                        f"RAMP RESTORED: {location}/{cluster} {setpoint_type} "
                        f"at {progress:.1f}% ({elapsed:.1f}/{duration:.0f} min)"
                    )
                    restored += 1
                except Exception as e:
                    logger.warning(f"Failed to restore ramp: {e}")

            return restored
        except Exception as e:
            logger.error(f"Failed to restore ramps from Redis: {e}")
            return 0

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
        threshold = self.SKIP_THRESHOLDS.get(setpoint_type, 0.1)
        delta = abs(target_value - start_value)

        if duration_minutes <= 0 or delta < threshold:
            # No ramp needed - but MUST cancel any existing ramp to prevent stale data
            if ramp_key in self.active_ramps:
                del self.active_ramps[ramp_key]
                logger.debug(
                    f"RAMP SKIPPED: {location}/{cluster} {setpoint_type} "
                    f"delta={delta:.3f} < threshold={threshold}, cleared stale ramp"
                )
            return

        ramp = RampState(setpoint_type, start_value, target_value, duration_minutes, current_time)
        self.active_ramps[ramp_key] = ramp

        logger.info(
            f"RAMP START: {location}/{cluster} {setpoint_type} from {start_value} to {target_value} "
            f"over {duration_minutes} minutes"
        )

        if self._redis:
            self._persist_ramp(location, cluster, ramp)

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
            return current_value, progress
        elif ramp and ramp.is_complete(current_time):
            del self.active_ramps[ramp_key]
            logger.info(
                f"RAMP COMPLETE: {location}/{cluster} {setpoint_type} reached {nominal_value}"
            )
            if self._redis:
                self._clear_persisted_ramp(location, cluster, setpoint_type)

        return nominal_value, None

    def cancel_ramp(self, location: str, cluster: str, setpoint_type: str) -> None:
        """Cancel an active ramp for a specific room."""
        ramp_key = self._make_key(location, cluster, setpoint_type)
        if ramp_key in self.active_ramps:
            del self.active_ramps[ramp_key]
            logger.info(f"RAMP CANCELLED: {location}/{cluster} {setpoint_type}")
            if self._redis:
                self._clear_persisted_ramp(location, cluster, setpoint_type)

    def get_active_ramps(self) -> dict[str, dict[str, Any]]:
        """Get information about all active ramps."""
        return {
            ramp_key: {
                "setpoint_type": ramp.setpoint_type,
                "start_value": ramp.start_value,
                "target_value": ramp.target_value,
                "duration_minutes": ramp.duration_minutes,
                "start_time": ramp.start_time.isoformat(),
                "end_time": ramp.end_time.isoformat(),
            }
            for ramp_key, ramp in self.active_ramps.items()
        }

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
            original_end_time = ramp.end_time

            # Update: new start = current value, new target, same end time
            ramp.start_value = current_value
            ramp.target_value = new_target
            ramp.start_time = current_time
            ramp.duration_minutes = remaining_minutes

            logger.info(
                f"RAMP ADJUSTED: {location}/{cluster} {setpoint_type} {current_value:.2f} -> {new_target:.2f} "
                f"in {remaining_minutes:.1f}min remaining"
            )

    def clear_all_ramps(self) -> None:
        """Clear all active ramps - used on service startup."""
        if self.active_ramps:
            ramp_types = list(self.active_ramps.keys())
            self.active_ramps.clear()
            logger.info(f"RAMPS CLEARED: {ramp_types}")

    def clear_ramps_for_room(self, location: str, cluster: str) -> None:
        """Clear all active ramps for a specific room - used on mode change."""
        setpoint_types = ["heating", "cooling", "humidity", "co2", "vpd"]
        cleared = []
        for setpoint_type in setpoint_types:
            ramp_key = self._make_key(location, cluster, setpoint_type)
            if ramp_key in self.active_ramps:
                del self.active_ramps[ramp_key]
                cleared.append(setpoint_type)
        if cleared:
            logger.debug(f"RAMPS CLEARED for {location}/{cluster}: {cleared}")


class SetpointManager:
    """Calculates effective setpoints with ramp transitions."""

    def __init__(self, database: DatabaseManager):
        """Initialize setpoint manager.

        Args:
            database: Database manager instance
        """
        self.database = database
        self.ramp_manager = RampManager()

    async def compute_effective_setpoints(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        current_mode: str | None,
        setpoint_data: dict[str, Any],
        sensor_values: dict[str, float | None] | None = None,
        previous_mode: str | None = None,
    ) -> dict[str, Any]:
        """Compute effective setpoints accounting for ramp transitions.

        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current timestamp
            current_mode: Current climate mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT)
            setpoint_data: Setpoint data from database
            sensor_values: Optional sensor values for ramp start initialization
            previous_mode: Previous climate mode for transition detection

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

            # Check for mode transitions that require ramping
            mode_changed = self._detect_mode_change(current_mode, previous_mode)

            if mode_changed:
                # CRITICAL: Clear ALL stale ramps when mode changes to prevent sawtoothing
                self.ramp_manager.clear_ramps_for_room(location, cluster)

                if ramp_in_duration > 0:
                    await self._handle_mode_transition_ramp(
                        location,
                        cluster,
                        current_mode,
                        nominal_values,
                        sensor_values,
                        ramp_in_duration,
                        current_time,
                        result,
                        previous_mode,
                    )
                else:
                    self._apply_nominal_values(result, nominal_values)
            elif self.ramp_manager.has_active_ramps(location, cluster):
                # Continue applying active ramps (no mode change, but ramps still in progress)
                self._apply_ramp_values(location, cluster, result, nominal_values, current_time)
            else:
                # No ramping needed, use nominal values directly
                self._apply_nominal_values(result, nominal_values)

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

    def _detect_mode_change(self, current_mode: str | None, previous_mode: str | None) -> bool:
        """Detect if a mode change occurred. Returns False on startup."""
        if previous_mode is None:
            return False
        return current_mode != previous_mode

    async def _handle_mode_transition_ramp(
        self,
        location: str,
        cluster: str,
        current_mode: str | None,
        nominal_values: dict[str, float | None],
        sensor_values: dict[str, float | None] | None,
        ramp_in_duration: float,
        current_time: datetime,
        result: dict[str, Any],
        previous_mode: str | None = None,
    ) -> None:
        """Handle ramp transitions when mode changes."""
        if current_mode is None:
            return

        logger.debug(
            f"RAMP DEBUG: {location}/{cluster} - mode_changed=True, current_mode={current_mode}"
        )

        # Determine ramp start values based on previous mode
        ramp_starts = await self._calculate_ramp_start_values(
            location, cluster, current_mode, nominal_values, sensor_values, previous_mode
        )

        # Start ramps for each setpoint type
        setpoint_types = ["heating", "cooling", "humidity", "co2", "vpd"]
        ramps_started = []
        for setpoint_type in setpoint_types:
            nominal_value = nominal_values[setpoint_type]
            start_value = ramp_starts.get(setpoint_type)

            if nominal_value is not None and start_value is not None:
                self.ramp_manager.start_ramp(
                    location,
                    cluster,
                    setpoint_type,
                    start_value,
                    nominal_value,
                    ramp_in_duration,
                    current_time,
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

    async def _calculate_ramp_start_values(
        self,
        location: str,
        cluster: str,
        current_mode: str,
        nominal_values: dict[str, float | None],
        sensor_values: dict[str, float | None] | None,
        previous_mode: str | None = None,
    ) -> dict[str, float | None]:
        """Calculate starting values for ramps based on previous mode.

        Uses the previous mode's setpoints as the ramp starting point.
        Falls back to inferred previous mode if previous_mode is None.
        """
        ramp_starts: dict[str, float | None] = {}

        def _extract_setpoints(setpoint_data: dict[str, Any]) -> dict[str, float | None]:
            """Helper to extract setpoint values from database record."""
            return {
                "heating": setpoint_data.get("heating_setpoint"),
                "cooling": setpoint_data.get("cooling_setpoint"),
                "humidity": setpoint_data.get("humidity"),
                "co2": setpoint_data.get("co2"),
                "vpd": setpoint_data.get("vpd"),
            }

        # If we know the previous mode, use its setpoints as ramp start
        if previous_mode:
            logger.debug(
                f"RAMP: {previous_mode} -> {current_mode}, fetching {previous_mode} setpoints as start"
            )
            prev_setpoint_data = await self.database.get_setpoint(location, cluster, previous_mode)
            if prev_setpoint_data:
                ramp_starts = _extract_setpoints(prev_setpoint_data)
                logger.debug(f"RAMP: Using {previous_mode} setpoints as ramp start: {ramp_starts}")
                return ramp_starts
            else:
                logger.warning(
                    f"RAMP: {previous_mode} setpoints not found for {location}/{cluster}"
                )

        # Fallback: Infer previous mode from current mode (backward compatibility / service startup)
        # Maps current_mode -> (primary_previous, secondary_fallback)
        mode_fallbacks: dict[str, tuple[str, str | None]] = {
            "PRE_DAY": ("NIGHT", None),
            "DAY": ("PRE_DAY", "NIGHT"),  # PRE_DAY if exists, else NIGHT
            "PRE_NIGHT": ("DAY", None),
            "NIGHT": ("PRE_NIGHT", "DAY"),  # PRE_NIGHT if exists, else DAY
        }

        fallbacks = mode_fallbacks.get(current_mode)
        if not fallbacks:
            logger.warning(f"RAMP: Unknown mode {current_mode}, cannot determine ramp start")
            return ramp_starts

        primary, secondary = fallbacks
        logger.debug(f"RAMP: Inferring previous mode for {current_mode}, trying {primary}")

        # Try primary fallback
        primary_data = await self.database.get_setpoint(location, cluster, primary)
        if primary_data:
            ramp_starts = _extract_setpoints(primary_data)
            logger.debug(f"RAMP: Using {primary} setpoints as ramp start: {ramp_starts}")
            return ramp_starts

        # Try secondary fallback if primary not found
        if secondary:
            logger.debug(f"RAMP: {primary} not found, falling back to {secondary}")
            secondary_data = await self.database.get_setpoint(location, cluster, secondary)
            if secondary_data:
                ramp_starts = _extract_setpoints(secondary_data)
                logger.debug(f"RAMP: Using {secondary} setpoints as ramp start: {ramp_starts}")
                return ramp_starts

        # Ultimate fallback: use nominal values (target = start, so no actual ramp)
        logger.warning(
            f"RAMP: No previous mode setpoints found for {location}/{cluster}, using nominal values"
        )
        ramp_starts = {
            "heating": nominal_values["heating"],
            "cooling": nominal_values["cooling"],
            "humidity": nominal_values["humidity"],
            "co2": nominal_values["co2"],
            "vpd": nominal_values["vpd"],
        }
        return ramp_starts

    def _apply_ramp_values(
        self,
        location: str,
        cluster: str,
        result: dict[str, Any],
        nominal_values: dict[str, float | None],
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

    def get_ramp_state(self) -> dict[str, dict[str, Any]]:
        """Get current ramp state for persistence."""
        return self.ramp_manager.get_active_ramps()

    def restore_ramp_state(
        self, ramp_data: dict[str, dict[str, Any]], current_time: datetime
    ) -> None:
        """Restore ramp state from persisted data."""
        for ramp_key, ramp_info in ramp_data.items():
            try:
                start_value = ramp_info["start_value"]
                target_value = ramp_info["target_value"]
                duration_minutes = ramp_info["duration_minutes"]
                start_time = datetime.fromisoformat(ramp_info["start_time"])

                # Recreate ramp state
                ramp_state = RampState(
                    ramp_key, start_value, target_value, duration_minutes, start_time
                )

                # Only restore if not complete
                if not ramp_state.is_complete(current_time):
                    self.ramp_manager.active_ramps[ramp_key] = ramp_state
                    logger.info(f"Restored ramp state for {ramp_key}")
                else:
                    logger.debug(f"Skipping completed ramp restore for {ramp_key}")

            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to restore ramp state for {ramp_key}: {e}")
