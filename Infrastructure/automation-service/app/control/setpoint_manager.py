"""Setpoint Manager - Handles setpoint calculations and ramp transitions."""
from shared.logging import get_logger, LoggingContext
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from app.database import DatabaseManager

logger = get_logger(__name__)


class RampState:
    """Represents the state of a ramp transition."""

    def __init__(self, setpoint_type: str, start_value: float, target_value: float,
                 duration_minutes: float, start_time: datetime):
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

    def __init__(self):
        """Initialize ramp manager."""
        self.active_ramps: Dict[str, RampState] = {}

    def start_ramp(self, setpoint_type: str, start_value: float, target_value: float,
                   duration_minutes: float, current_time: datetime) -> None:
        """Start a new ramp transition.

        Args:
            setpoint_type: Type of setpoint (heating, cooling, etc.)
            start_value: Starting setpoint value
            target_value: Target setpoint value
            duration_minutes: Duration of ramp in minutes
            current_time: Current timestamp
        """
        if duration_minutes <= 0 or abs(target_value - start_value) < 0.1:
            # No ramp needed for instant changes or very small changes
            return

        ramp_key = setpoint_type
        self.active_ramps[ramp_key] = RampState(
            setpoint_type, start_value, target_value, duration_minutes, current_time
        )

        logger.info(
            f"RAMP START: {setpoint_type} from {start_value} to {target_value} "
            f"over {duration_minutes} minutes"
        )

    def get_ramp_value(self, setpoint_type: str, nominal_value: float,
                       current_time: datetime) -> Tuple[float, Optional[float]]:
        """Get the current ramp value and progress for a setpoint type.

        Args:
            setpoint_type: Type of setpoint
            nominal_value: The nominal (target) value
            current_time: Current timestamp

        Returns:
            Tuple of (effective_value, progress_ratio)
        """
        ramp_key = setpoint_type
        ramp = self.active_ramps.get(ramp_key)

        if ramp and not ramp.is_complete(current_time):
            current_value = ramp.get_current_value(current_time)
            elapsed = (current_time - ramp.start_time).total_seconds() / 60.0
            progress = min(elapsed / ramp.duration_minutes, 1.0)
            return current_value, progress
        elif ramp and ramp.is_complete(current_time):
            # Ramp completed, clean it up
            del self.active_ramps[ramp_key]
            logger.debug(f"RAMP COMPLETE: {setpoint_type} reached {nominal_value}")

        return nominal_value, None

    def cancel_ramp(self, setpoint_type: str) -> None:
        """Cancel an active ramp."""
        ramp_key = setpoint_type
        if ramp_key in self.active_ramps:
            del self.active_ramps[ramp_key]
            logger.info(f"RAMP CANCELLED: {setpoint_type}")

    def get_active_ramps(self) -> Dict[str, Dict[str, Any]]:
        """Get information about all active ramps."""
        return {
            ramp_key: {
                'setpoint_type': ramp.setpoint_type,
                'start_value': ramp.start_value,
                'target_value': ramp.target_value,
                'duration_minutes': ramp.duration_minutes,
                'start_time': ramp.start_time.isoformat(),
                'end_time': ramp.end_time.isoformat()
            }
            for ramp_key, ramp in self.active_ramps.items()
        }


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
        current_mode: Optional[str],
        setpoint_data: Dict[str, Any],
        sensor_values: Optional[Dict[str, Optional[float]]] = None,
        previous_mode: Optional[str] = None
    ) -> Dict[str, Any]:
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
            ramp_in_duration = setpoint_data.get('ramp_in_duration', 0) or 0

            # Store nominal values
            self._store_nominal_values(result, nominal_values)

            # Check for mode transitions that require ramping
            mode_changed = self._detect_mode_change(current_mode, previous_mode)

            if mode_changed and ramp_in_duration > 0:
                await self._handle_mode_transition_ramp(
                    location, cluster, current_mode, nominal_values,
                    sensor_values, ramp_in_duration, current_time, result
                )
            else:
                # No ramping needed, use nominal values directly
                self._apply_nominal_values(result, nominal_values)

            return result

    def _initialize_result_dict(self) -> Dict[str, Any]:
        """Initialize the result dictionary with None values."""
        return {
            'effective_heating_setpoint': None,
            'effective_cooling_setpoint': None,
            'effective_humidity_setpoint': None,
            'effective_co2_setpoint': None,
            'effective_vpd_setpoint': None,
            'nominal_heating_setpoint': None,
            'nominal_cooling_setpoint': None,
            'nominal_humidity_setpoint': None,
            'nominal_co2_setpoint': None,
            'nominal_vpd_setpoint': None,
            'ramp_progress_heating': None,
            'ramp_progress_cooling': None,
            'ramp_progress_humidity': None,
            'ramp_progress_co2': None,
            'ramp_progress_vpd': None
        }

    def _extract_nominal_setpoints(self, setpoint_data: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Extract nominal setpoint values from database data."""
        return {
            'heating': setpoint_data.get('heating_setpoint'),
            'cooling': setpoint_data.get('cooling_setpoint'),
            'humidity': setpoint_data.get('humidity'),
            'co2': setpoint_data.get('co2'),
            'vpd': setpoint_data.get('vpd')
        }

    def _store_nominal_values(self, result: Dict[str, Any], nominal_values: Dict[str, Optional[float]]) -> None:
        """Store nominal values in result dict."""
        result['nominal_heating_setpoint'] = nominal_values['heating']
        result['nominal_cooling_setpoint'] = nominal_values['cooling']
        result['nominal_humidity_setpoint'] = nominal_values['humidity']
        result['nominal_co2_setpoint'] = nominal_values['co2']
        result['nominal_vpd_setpoint'] = nominal_values['vpd']

    def _detect_mode_change(self, current_mode: Optional[str], previous_mode: Optional[str]) -> bool:
        """Detect if a mode change occurred."""
        return current_mode != previous_mode

    async def _handle_mode_transition_ramp(
        self, location: str, cluster: str, current_mode: Optional[str],
        nominal_values: Dict[str, Optional[float]], sensor_values: Optional[Dict[str, Optional[float]]],
        ramp_in_duration: float, current_time: datetime, result: Dict[str, Any]
    ) -> None:
        """Handle ramp transitions when mode changes."""
        if current_mode is None:
            return

        logger.debug(
            f"RAMP DEBUG: {location}/{cluster} - mode_changed=True, current_mode={current_mode}"
        )

        # Determine ramp start values based on mode
        ramp_starts = await self._calculate_ramp_start_values(
            location, cluster, current_mode, nominal_values, sensor_values
        )

        # Start ramps for each setpoint type
        setpoint_types = ['heating', 'cooling', 'humidity', 'co2', 'vpd']
        ramps_started = []
        for setpoint_type in setpoint_types:
            nominal_value = nominal_values[setpoint_type]
            start_value = ramp_starts.get(setpoint_type)

            if nominal_value is not None and start_value is not None:
                self.ramp_manager.start_ramp(
                    setpoint_type, start_value, nominal_value,
                    ramp_in_duration, current_time
                )
                ramps_started.append(setpoint_type)

        # Apply current ramp values
        self._apply_ramp_values(result, nominal_values, current_time)

    async def _calculate_ramp_start_values(
        self, location: str, cluster: str, current_mode: str,
        nominal_values: Dict[str, Optional[float]],
        sensor_values: Optional[Dict[str, Optional[float]]]
    ) -> Dict[str, Optional[float]]:
        """Calculate starting values for ramps based on current mode."""
        ramp_starts = {}

        if current_mode == 'PRE_DAY':
            # Ramp from NIGHT setpoints to DAY setpoints
            # Use current sensor values as NIGHT approximation if no NIGHT setpoints available
            logger.debug(f"RAMP DEBUG: Entering PRE_DAY mode, using sensor values as NIGHT start")
            ramp_starts = {
                'heating': sensor_values.get('temperature') if sensor_values else nominal_values['heating'],
                'cooling': sensor_values.get('temperature') if sensor_values else nominal_values['cooling'],
                'humidity': sensor_values.get('humidity') if sensor_values else nominal_values['humidity'],
                'co2': sensor_values.get('co2') if sensor_values else nominal_values['co2'],
                'vpd': sensor_values.get('vpd') if sensor_values else nominal_values['vpd']
            }

        elif current_mode == 'PRE_NIGHT':
            # Ramp from DAY setpoints to PRE_NIGHT setpoints
            logger.debug(f"RAMP DEBUG: Entering PRE_NIGHT mode, fetching DAY setpoints as start")
            # Fetch DAY setpoints from database to use as ramp start values
            day_setpoint_data = await self.database.get_setpoint(location, cluster, 'DAY')
            if day_setpoint_data:
                ramp_starts = {
                    'heating': day_setpoint_data.get('heating_setpoint'),
                    'cooling': day_setpoint_data.get('cooling_setpoint'),
                    'humidity': day_setpoint_data.get('humidity'),
                    'co2': day_setpoint_data.get('co2'),
                    'vpd': day_setpoint_data.get('vpd')
                }
                logger.debug(f"RAMP DEBUG: Using DAY setpoints as ramp start: {ramp_starts}")
            else:
                # Fallback to current sensor values or nominal values if DAY setpoints not found
                logger.warning(f"RAMP DEBUG: DAY setpoints not found for {location}/{cluster}, using sensor/nominal values as fallback")
                ramp_starts = {
                    'heating': sensor_values.get('temperature') if sensor_values else nominal_values['heating'],
                    'cooling': sensor_values.get('temperature') if sensor_values else nominal_values['cooling'],
                    'humidity': sensor_values.get('humidity') if sensor_values else nominal_values['humidity'],
                    'co2': sensor_values.get('co2') if sensor_values else nominal_values['co2'],
                    'vpd': sensor_values.get('vpd') if sensor_values else nominal_values['vpd']
                }

        return ramp_starts

    def _apply_ramp_values(self, result: Dict[str, Any], nominal_values: Dict[str, Optional[float]],
                          current_time: datetime) -> None:
        """Apply current ramp values to result dict."""
        setpoint_types = ['heating', 'cooling', 'humidity', 'co2', 'vpd']

        for setpoint_type in setpoint_types:
            nominal_value = nominal_values[setpoint_type]
            if nominal_value is not None:
                effective_value, progress = self.ramp_manager.get_ramp_value(
                    setpoint_type, nominal_value, current_time
                )

                result[f'effective_{setpoint_type}_setpoint'] = effective_value
                result[f'ramp_progress_{setpoint_type}'] = progress

    def _apply_nominal_values(self, result: Dict[str, Any], nominal_values: Dict[str, Optional[float]]) -> None:
        """Apply nominal values directly (no ramping)."""
        result['effective_heating_setpoint'] = nominal_values['heating']
        result['effective_cooling_setpoint'] = nominal_values['cooling']
        result['effective_humidity_setpoint'] = nominal_values['humidity']
        result['effective_co2_setpoint'] = nominal_values['co2']
        result['effective_vpd_setpoint'] = nominal_values['vpd']

    def get_ramp_state(self) -> Dict[str, Dict[str, Any]]:
        """Get current ramp state for persistence."""
        return self.ramp_manager.get_active_ramps()

    def restore_ramp_state(self, ramp_data: Dict[str, Dict[str, Any]], current_time: datetime) -> None:
        """Restore ramp state from persisted data."""
        for ramp_key, ramp_info in ramp_data.items():
            try:
                start_value = ramp_info['start_value']
                target_value = ramp_info['target_value']
                duration_minutes = ramp_info['duration_minutes']
                start_time = datetime.fromisoformat(ramp_info['start_time'])

                # Recreate ramp state
                ramp_state = RampState(ramp_key, start_value, target_value, duration_minutes, start_time)

                # Only restore if not complete
                if not ramp_state.is_complete(current_time):
                    self.ramp_manager.active_ramps[ramp_key] = ramp_state
                    logger.info(f"Restored ramp state for {ramp_key}")
                else:
                    logger.debug(f"Skipping completed ramp restore for {ramp_key}")

            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to restore ramp state for {ramp_key}: {e}")
