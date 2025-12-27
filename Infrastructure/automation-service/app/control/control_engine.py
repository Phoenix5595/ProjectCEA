"""Control engine that orchestrates rules, schedules, and PID control."""
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from app.control.relay_manager import RelayManager
from app.control.pid_controller import PIDController
from app.control.scheduler import Scheduler
from app.automation.rules_engine import RulesEngine
from app.database import DatabaseManager
from app.config import ConfigLoader
from app.alarm_manager import AlarmManager

logger = logging.getLogger(__name__)


class ControlEngine:
    """Main control engine that executes automation logic."""
    
    def __init__(
        self,
        relay_manager: RelayManager,
        database: DatabaseManager,
        config: ConfigLoader,
        scheduler: Scheduler,
        rules_engine: RulesEngine,
        alarm_manager: Optional[AlarmManager] = None,
        dfr0971_manager: Optional[Any] = None  # DFR0971Manager (avoid circular import)
    ):
        """Initialize control engine.
        
        Args:
            relay_manager: Relay manager instance
            database: Database manager instance
            config: Config loader instance
            scheduler: Scheduler instance
            rules_engine: Rules engine instance
            alarm_manager: Optional alarm manager instance
            dfr0971_manager: Optional DFR0971 manager for light intensity logging
        """
        self.relay_manager = relay_manager
        self.database = database
        self.config = config
        self.scheduler = scheduler
        self.rules_engine = rules_engine
        self.alarm_manager = alarm_manager
        self.dfr0971_manager = dfr0971_manager
        
        # PID controllers per device
        self._pid_controllers: Dict[Tuple[str, str, str], PIDController] = {}
        
        # Track automation context for logging
        self._automation_context: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        
        # Track current climate mode per location/cluster
        self._current_climate_mode: Dict[Tuple[str, str], str] = {}
        
        # Track ramp state per location/cluster/setpoint_type
        # Format: (location, cluster, setpoint_type) -> {
        #   'current_effective_setpoint': float,
        #   'ramp_start_timestamp': datetime,
        #   'ramp_duration': int (minutes),
        #   'target_setpoint': float,
        #   'last_logged_setpoint': float  # Last setpoint value logged to DB
        # }
        self._ramp_state: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        
        # Track effective setpoints per location/cluster
        # Format: (location, cluster) -> {
        #   'effective_heating_setpoint': float,
        #   'effective_cooling_setpoint': float,
        #   'nominal_heating_setpoint': float,
        #   'nominal_cooling_setpoint': float,
        #   'ramp_progress_heating': float or None,
        #   'ramp_progress_cooling': float or None
        # }
        self._effective_setpoints: Dict[Tuple[str, str], Dict[str, Any]] = {}
        
        logger.info("Control engine initialized")
    
    async def run_control_loop(self) -> None:
        """Run one iteration of the control loop."""
        current_time = datetime.now()
        
        # Get all devices from config
        devices = self.config.get_devices()
        
        # Get sensor values for all locations/clusters
        sensor_mapping = self.config.get_sensor_mapping()
        
        # Process each location/cluster
        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                # Get sensor values for this location/cluster
                sensor_values = await self._get_sensor_values(location, cluster, sensor_mapping)
                
                # Determine current mode and get setpoints
                light_schedule = await self.database.get_light_schedule(location, cluster)
                climate_schedule = await self.database.get_climate_schedule(location, cluster)
                
                current_mode = None
                if light_schedule and climate_schedule:
                    mode_result = self.scheduler.get_climate_mode(
                        location, cluster, current_time,
                        light_schedule.get('day_start_time'),
                        light_schedule.get('day_end_time'),
                        climate_schedule.get('pre_day_duration'),
                        climate_schedule.get('pre_night_duration')
                    )
                    if mode_result:
                        current_mode, _, _ = mode_result
                
                # Get setpoint data for current mode
                setpoint_data = await self.database.get_setpoint(location, cluster, current_mode)
                if not setpoint_data and current_mode:
                    # Fallback to legacy mode=None if mode-based setpoint not found
                    setpoint_data = await self.database.get_setpoint(location, cluster, None)
                
                if setpoint_data:
                    # Compute effective setpoints
                    effective_data = await self._compute_effective_setpoints(
                        location, cluster, current_time, current_mode, setpoint_data, sensor_values
                    )
                    
                    # Store in context
                    self._effective_setpoints[(location, cluster)] = effective_data
                    
                    # Log to database immediately (before device processing)
                    await self.database.log_effective_setpoints(
                        location=location,
                        cluster=cluster,
                        mode=current_mode,
                        effective_heating_setpoint=effective_data['effective_heating_setpoint'],
                        effective_cooling_setpoint=effective_data['effective_cooling_setpoint'],
                        effective_humidity_setpoint=effective_data['effective_humidity_setpoint'],
                        effective_co2_setpoint=effective_data['effective_co2_setpoint'],
                        effective_vpd_setpoint=effective_data['effective_vpd_setpoint'],
                        nominal_heating_setpoint=effective_data['nominal_heating_setpoint'],
                        nominal_cooling_setpoint=effective_data['nominal_cooling_setpoint'],
                        nominal_humidity_setpoint=effective_data['nominal_humidity_setpoint'],
                        nominal_co2_setpoint=effective_data['nominal_co2_setpoint'],
                        nominal_vpd_setpoint=effective_data['nominal_vpd_setpoint'],
                        ramp_progress_heating=effective_data['ramp_progress_heating'],
                        ramp_progress_cooling=effective_data['ramp_progress_cooling'],
                        ramp_progress_humidity=effective_data['ramp_progress_humidity'],
                        ramp_progress_co2=effective_data['ramp_progress_co2'],
                        ramp_progress_vpd=effective_data['ramp_progress_vpd'],
                        timestamp=current_time
                    )
                
                # Process each device
                for device_name, device_info in cluster_devices.items():
                    await self._process_device(
                        location, cluster, device_name, device_info,
                        sensor_values, current_time
                    )
        
        # Log automation state for all devices
        await self._log_automation_state()
    
    async def _get_sensor_values(
        self,
        location: str,
        cluster: str,
        sensor_mapping: Dict[str, Any]
    ) -> Dict[str, Optional[float]]:
        """Get sensor values for a location/cluster.
        
        Args:
            location: Location name
            cluster: Cluster name
            sensor_mapping: Sensor mapping from config
        
        Returns:
            Dict mapping sensor names to values
        """
        sensor_values = {}
        
        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})
        
        for sensor_type, sensor_name in cluster_sensors.items():
            if sensor_name:
                value = await self.database.get_sensor_value(sensor_name)
                sensor_values[sensor_name] = value
        
        return sensor_values
    
    async def _process_device(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]],
        current_time: datetime
    ) -> None:
        """Process control for a single device.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
        """
        key = (location, cluster, device_name)
        
        # Initialize automation context
        if key not in self._automation_context:
            self._automation_context[key] = {
                'active_rule_ids': [],
                'active_schedule_ids': [],
                'pid_output': None,
                'duty_cycle_percent': None,
                'control_reason': None
            }
        
        context = self._automation_context[key]
        context['active_rule_ids'] = []
        context['active_schedule_ids'] = []
        context['control_reason'] = None
        
        # Log light intensity for dimmable lights (do this early so it happens even with early returns)
        if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
            if self.dfr0971_manager:
                board_id = device_info.get('dimming_board_id')
                channel = device_info.get('dimming_channel')
                if board_id is not None and channel is not None:
                    intensity = self.dfr0971_manager.get_intensity(board_id, channel)
                    if intensity is not None:
                        # Set duty_cycle_percent to intensity for logging
                        context['duty_cycle_percent'] = intensity
                        if not context.get('control_reason'):
                            context['control_reason'] = 'light'
        
        # Check if device is in manual mode
        current_mode = self.relay_manager.get_device_mode(location, cluster, device_name)
        if current_mode == 'manual':
            context['control_reason'] = 'manual'
            return  # Skip automatic control
        
        # Get current state
        current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
        
        # 1. Evaluate rules (if schedule active)
        rule_result = self.rules_engine.evaluate(location, cluster, sensor_values, current_time)
        
        if rule_result:
            device_name_from_rule, action_state, rule_id = rule_result
            if device_name_from_rule == device_name:
                # Rule applies to this device
                if rule_id is not None:
                    context['active_rule_ids'].append(rule_id)
                context['control_reason'] = 'rule'
                
                if action_state != current_state:
                    await self._set_device_state(
                        location, cluster, device_name, action_state,
                        'auto', 'rule', sensor_values
                    )
                return  # Rules execute first, skip schedules and PID
        
        # 2. Check schedules (only if no rule matched)
        schedule_state, schedule_id = self.scheduler.get_schedule_state(
            location, cluster, device_name, current_time
        )
        
        if schedule_state is not None:
            if schedule_id is not None:
                context['active_schedule_ids'].append(schedule_id)
            context['control_reason'] = 'schedule'
            
            # Get active schedule details (ramp durations, photoperiod) for logging
            schedule_details = self.scheduler.get_active_schedule_details(
                location, cluster, device_name, current_time
            )
            if schedule_details:
                context['schedule_ramp_up_duration'] = schedule_details.get('ramp_up_duration')
                context['schedule_ramp_down_duration'] = schedule_details.get('ramp_down_duration')
                context['schedule_photoperiod_hours'] = schedule_details.get('photoperiod_hours')
            
            # Check if this is a dimmable light with ramp schedule
            if device_info.get('dimming_enabled') and device_info.get('dimming_type') == 'dfr0971':
                if self.dfr0971_manager:
                    board_id = device_info.get('dimming_board_id')
                    channel = device_info.get('dimming_channel')
                    if board_id is not None and channel is not None:
                        # Get current intensity for ramp calculation
                        current_intensity = self.dfr0971_manager.get_intensity(board_id, channel) or 0.0
                        
                        # Get schedule intensity (with ramp calculation)
                        schedule_intensity = self.scheduler.get_schedule_intensity(
                            location, cluster, device_name, current_time, current_intensity
                        )
                        
                        if schedule_intensity is not None:
                            # Apply schedule intensity to light
                            success = self.dfr0971_manager.set_intensity(
                                board_id, channel, schedule_intensity, store_to_eeprom=False
                            )
                            if success:
                                # Set relay state: ON if intensity > 0, OFF if 0
                                relay_state = 1 if schedule_intensity > 0 else 0
                                if relay_state != current_state:
                                    await self._set_device_state(
                                        location, cluster, device_name, relay_state,
                                        'scheduled', 'schedule', sensor_values
                                    )
                                
                                # Store intensity in Redis for persistence
                                if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                                    voltage = (schedule_intensity / 100.0) * 10.0
                                    self.database._automation_redis.write_light_intensity(
                                        location, cluster, device_name,
                                        schedule_intensity, voltage, board_id, channel
                                    )
                                
                                logger.debug(
                                    f"Schedule intensity {schedule_intensity:.1f}% applied to "
                                    f"{location}/{cluster}/{device_name} (board {board_id}, channel {channel})"
                                )
                                return  # Schedule applied, skip PID
                        else:
                            # No intensity specified, use default ON/OFF behavior
                            if schedule_state != current_state:
                                await self._set_device_state(
                                    location, cluster, device_name, schedule_state,
                                    'scheduled', 'schedule', sensor_values
                                )
                            return  # Schedule applies, skip PID
                    else:
                        # Light device but missing board/channel config, use default ON/OFF
                        if schedule_state != current_state:
                            await self._set_device_state(
                                location, cluster, device_name, schedule_state,
                                'scheduled', 'schedule', sensor_values
                            )
                        return
                else:
                    # DFR0971 manager not available, use default ON/OFF
                    if schedule_state != current_state:
                        await self._set_device_state(
                            location, cluster, device_name, schedule_state,
                            'scheduled', 'schedule', sensor_values
                        )
                    return
            else:
                # Not a dimmable light, use default ON/OFF behavior
                if schedule_state != current_state:
                    await self._set_device_state(
                        location, cluster, device_name, schedule_state,
                        'scheduled', 'schedule', sensor_values
                    )
                return  # Schedule applies, skip PID
        
        # Check mode and failsafe before PID control
        if self.database._automation_redis and self.database._automation_redis.redis_enabled:
            mode = self.database._automation_redis.read_mode(location, cluster) or 'auto'
            failsafe = self.database._automation_redis.read_failsafe(location, cluster)
            
            # Skip PID if in failsafe or manual mode
            if failsafe or mode == 'failsafe':
                logger.debug(f"Skipping PID control for {location}/{cluster}/{device_name}: failsafe active")
                return
            if mode == 'manual':
                logger.debug(f"Skipping PID control for {location}/{cluster}/{device_name}: manual mode")
                return
        
        # 3. PID control (only if no rule/schedule applied and mode allows)
        if device_info.get('pid_enabled', False):
            await self._process_pid_control(
                location, cluster, device_name, device_info,
                sensor_values, current_time, context
            )
        
        # 4. VPD control for dehumidifying devices (fans, dehumidifiers)
        device_type = device_info.get('device_type', '')
        if device_type in ['fan', 'dehumidifier']:
            await self._process_vpd_control(
                location, cluster, device_name, device_info,
                sensor_values, current_time, context
            )
    
    def _get_sensor_for_setpoint_type(
        self,
        location: str,
        cluster: str,
        setpoint_type: str
    ) -> Optional[str]:
        """Get sensor name for a setpoint type.
        
        Args:
            location: Location name
            cluster: Cluster name
            setpoint_type: Setpoint type (e.g., 'heating_setpoint', 'cooling_setpoint', 'vpd_setpoint', 'co2')
        
        Returns:
            Sensor name or None if not found
        """
        sensor_mapping = self.config.get_sensor_mapping()
        location_sensors = sensor_mapping.get(location, {})
        cluster_sensors = location_sensors.get(cluster, {})
        
        # Map setpoint types to sensor names
        if setpoint_type in ['heating_setpoint', 'cooling_setpoint']:
            return cluster_sensors.get('temperature_sensor')
        elif setpoint_type == 'vpd' or setpoint_type == 'vpd_setpoint':
            return cluster_sensors.get('vpd_sensor')
        elif setpoint_type == 'co2':
            return cluster_sensors.get('co2_sensor')
        elif setpoint_type == 'humidity' or setpoint_type == 'humidity_setpoint':
            return cluster_sensors.get('humidity_sensor')
        else:
            logger.warning(f"Unknown setpoint_type: {setpoint_type}")
            return None
    
    async def _compute_effective_setpoints(
        self,
        location: str,
        cluster: str,
        current_time: datetime,
        current_mode: Optional[str],
        setpoint_data: Dict[str, Any],
        sensor_values: Optional[Dict[str, Optional[float]]] = None
    ) -> Dict[str, Any]:
        """Compute effective setpoints accounting for ramp transitions.
        
        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current timestamp
            current_mode: Current climate mode (DAY/NIGHT/PRE_DAY/PRE_NIGHT)
            setpoint_data: Setpoint data from database
            sensor_values: Optional sensor values for ramp start initialization
        
        Returns:
            Dict with effective/nominal setpoints and ramp progress values
        """
        result = {
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
        
        # Get nominal setpoints
        nominal_heating = setpoint_data.get('heating_setpoint')
        nominal_cooling = setpoint_data.get('cooling_setpoint')
        nominal_humidity = setpoint_data.get('humidity')
        nominal_co2 = setpoint_data.get('co2')
        nominal_vpd = setpoint_data.get('vpd')
        ramp_in_duration = setpoint_data.get('ramp_in_duration', 0) or 0
        
        result['nominal_heating_setpoint'] = nominal_heating
        result['nominal_cooling_setpoint'] = nominal_cooling
        result['nominal_humidity_setpoint'] = nominal_humidity
        result['nominal_co2_setpoint'] = nominal_co2
        result['nominal_vpd_setpoint'] = nominal_vpd
        
        # Check if mode changed (for ramp state initialization)
        climate_mode_key = (location, cluster)
        previous_mode = self._current_climate_mode.get(climate_mode_key)
        mode_changed = (previous_mode is not None and previous_mode != current_mode)
        
        # Store current mode
        if current_mode is not None:
            self._current_climate_mode[climate_mode_key] = current_mode
        
        # Compute effective setpoints independently for all setpoint types
        setpoint_types = [
            ('heating_setpoint', nominal_heating),
            ('cooling_setpoint', nominal_cooling),
            ('humidity', nominal_humidity),
            ('co2', nominal_co2),
            ('vpd', nominal_vpd)
        ]
        
        for setpoint_type, nominal_value in setpoint_types:
            if nominal_value is None:
                continue
            
            ramp_key = (location, cluster, setpoint_type)
            
            # Initialize or update ramp state if mode changed
            if mode_changed:
                # Mode changed: check if we need to restart ramp
                existing_ramp_state = self._ramp_state.get(ramp_key)
                existing_target = existing_ramp_state.get('target_setpoint') if existing_ramp_state else None
                
                # Only restart ramp if nominal value actually changed
                if existing_target is not None and existing_target == nominal_value:
                    # Nominal value unchanged: keep existing ramp state, just update ramp_duration if it changed
                    if existing_ramp_state.get('ramp_duration') != ramp_in_duration:
                        existing_ramp_state['ramp_duration'] = ramp_in_duration
                    # Don't restart ramp - continue with existing effective setpoint
                else:
                    # Nominal value changed or no existing ramp: restart ramp
                    current_effective = existing_ramp_state.get('current_effective_setpoint') if existing_ramp_state else None
                    if current_effective is None:
                        # No previous ramp state, use current sensor value as start
                        if sensor_values:
                            sensor_name = self._get_sensor_for_setpoint_type(location, cluster, setpoint_type)
                            if sensor_name:
                                current_effective = sensor_values.get(sensor_name)
                    if current_effective is None:
                        current_effective = nominal_value  # Fallback to target
                    
                    # Start new ramp
                    self._ramp_state[ramp_key] = {
                        'current_effective_setpoint': current_effective,
                        'ramp_start_timestamp': current_time,
                        'ramp_duration': ramp_in_duration,
                        'target_setpoint': nominal_value
                    }
            elif ramp_key not in self._ramp_state:
                # First time: initialize ramp state
                # If ramp_in_duration is 0, use nominal value directly (instant transition)
                # If ramp_in_duration > 0, start from nominal value to avoid unnecessary ramps
                # Only use sensor value if we're explicitly transitioning (which would be handled by mode change or target change)
                start_value = nominal_value
                
                self._ramp_state[ramp_key] = {
                    'current_effective_setpoint': start_value,
                    'ramp_start_timestamp': current_time,
                    'ramp_duration': ramp_in_duration,
                    'target_setpoint': nominal_value
                }
            else:
                # Update target or ramp_duration if they changed (but don't restart ramp unnecessarily)
                ramp_state = self._ramp_state[ramp_key]
                if ramp_state['target_setpoint'] != nominal_value:
                    # Target changed: restart ramp from current effective
                    ramp_state['current_effective_setpoint'] = ramp_state.get('current_effective_setpoint', nominal_value)
                    ramp_state['ramp_start_timestamp'] = current_time
                    ramp_state['ramp_duration'] = ramp_in_duration
                    ramp_state['target_setpoint'] = nominal_value
                elif ramp_state['ramp_duration'] != ramp_in_duration:
                    # Only ramp_duration changed: update it but don't restart ramp
                    ramp_state['ramp_duration'] = ramp_in_duration
            
            # Calculate effective setpoint (with ramp)
            ramp_state = self._ramp_state[ramp_key]
            start_setpoint = ramp_state['current_effective_setpoint']
            ramp_start = ramp_state['ramp_start_timestamp']
            ramp_duration_min = ramp_state['ramp_duration']
            target = ramp_state['target_setpoint']
            
            if ramp_duration_min > 0:
                # Calculate ramp progress
                elapsed_seconds = (current_time - ramp_start).total_seconds()
                elapsed_minutes = elapsed_seconds / 60.0
                progress = min(max(elapsed_minutes / ramp_duration_min, 0.0), 1.0)
                
                if progress >= 1.0:
                    # Ramp complete
                    effective = target
                    ramp_progress = None
                else:
                    # Still ramping
                    effective = start_setpoint + (target - start_setpoint) * progress
                    ramp_progress = progress
                    ramp_state['current_effective_setpoint'] = effective
            else:
                # No ramp or instant transition
                effective = target
                ramp_progress = None
                ramp_state['current_effective_setpoint'] = effective
            
            # Store results
            if setpoint_type == 'heating_setpoint':
                result['effective_heating_setpoint'] = effective
                result['ramp_progress_heating'] = ramp_progress
            elif setpoint_type == 'cooling_setpoint':
                result['effective_cooling_setpoint'] = effective
                result['ramp_progress_cooling'] = ramp_progress
            elif setpoint_type == 'humidity':
                result['effective_humidity_setpoint'] = effective
                result['ramp_progress_humidity'] = ramp_progress
            elif setpoint_type == 'co2':
                result['effective_co2_setpoint'] = effective
                result['ramp_progress_co2'] = ramp_progress
            elif setpoint_type == 'vpd':
                result['effective_vpd_setpoint'] = effective
                result['ramp_progress_vpd'] = ramp_progress
        
        return result
    
    async def _process_pid_control(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]],
        current_time: datetime,
        context: Dict[str, Any]
    ) -> None:
        """Process priority-based multi-setpoint PID control for a device.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
        """
        device_type = device_info.get('device_type', '')
        
        # Get climate mode (PRE_DAY, DAY, PRE_NIGHT, NIGHT)
        climate_mode_key = (location, cluster)
        current_mode = None
        mode_start_min = None
        mode_end_min = None
        
        # Get light schedule and climate schedule to determine mode
        light_schedule = await self.database.get_light_schedule(location, cluster)
        climate_schedule = await self.database.get_climate_schedule(location, cluster)
        
        if light_schedule and climate_schedule:
            mode_result = self.scheduler.get_climate_mode(
                location, cluster, current_time,
                light_schedule.get('day_start_time'),
                light_schedule.get('day_end_time'),
                climate_schedule.get('pre_day_duration'),
                climate_schedule.get('pre_night_duration')
            )
            if mode_result:
                current_mode, mode_start_min, mode_end_min = mode_result
                # Store current mode
                self._current_climate_mode[climate_mode_key] = current_mode
        else:
            # Fallback: use legacy mode=None setpoint
            current_mode = None
        
        # Check if mode changed (for PID integrator reset)
        previous_mode = self._current_climate_mode.get(climate_mode_key)
        mode_changed = (previous_mode is not None and previous_mode != current_mode)
        
        # Get setpoint data for current mode (needed for PID setpoint priority evaluation)
        setpoint_data = await self.database.get_setpoint(location, cluster, current_mode)
        if not setpoint_data:
            # Fallback to legacy mode=None if mode-based setpoint not found
            if current_mode:
                setpoint_data = await self.database.get_setpoint(location, cluster, None)
            if not setpoint_data:
                return  # No setpoint configured
        
        # Get priority-based setpoints for this device
        pid_setpoints = self.config.get_pid_setpoints_for_device(
            location, cluster, device_name, device_type
        )
        
        if not pid_setpoints:
            return  # No setpoints configured for this device
        
        # Use pre-computed effective setpoints from context (computed at location/cluster level)
        effective_data = self._effective_setpoints.get((location, cluster), {})
        
        # Evaluate setpoints in priority order (already sorted by priority)
        selected_pid_output = None
        selected_setpoint_type = None
        
        for setpoint_type, priority in pid_setpoints:
            # Get setpoint value - use effective if available, otherwise nominal
            if setpoint_type == 'heating_setpoint':
                setpoint_value = effective_data.get('effective_heating_setpoint')
                if setpoint_value is None:
                    setpoint_value = setpoint_data.get('heating_setpoint')
            elif setpoint_type == 'cooling_setpoint':
                setpoint_value = effective_data.get('effective_cooling_setpoint')
                if setpoint_value is None:
                    setpoint_value = setpoint_data.get('cooling_setpoint')
            else:
                # For other setpoint types (humidity, co2, vpd), use nominal value
                # (these don't have ramp transitions in the current implementation)
                setpoint_value = setpoint_data.get(setpoint_type)
            
            if setpoint_value is None:
                continue  # Skip if setpoint not configured
            
            # Get sensor name for this setpoint type
            sensor_name = self._get_sensor_for_setpoint_type(location, cluster, setpoint_type)
            if not sensor_name:
                continue  # Skip if sensor not configured
            
            # Get current sensor value
            current_value = sensor_values.get(sensor_name)
            
            # Use last good value if sensor value is None
            if current_value is None:
                if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                    last_good = self.database._automation_redis.read_last_good_value(cluster, sensor_name)
                    if last_good:
                        hold_period = self.config.get('control.last_good_hold_period', 30)
                        is_valid, age = self.database._automation_redis.check_last_good_age(cluster, sensor_name, hold_period)
                        if is_valid:
                            current_value = last_good['value']
                            logger.debug(f"Using last good value for {sensor_name}: {current_value} (age: {age:.1f}s)")
                        else:
                            # Last good value expired, skip this setpoint and try next
                            continue
                    else:
                        # No last good value, skip this setpoint and try next
                        continue
                else:
                    # Missing sensor value and no Redis, skip this setpoint
                    continue
            
            # Update last good value if sensor is valid
            if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                self.database._automation_redis.write_last_good_value(cluster, sensor_name, current_value)
            
            # Get or create PID controller for this setpoint type
            # Key includes setpoint_type for state isolation
            pid_key = (location, cluster, device_name, setpoint_type)
            if pid_key not in self._pid_controllers:
                pid_params = self.config.get_pid_params_for_device(device_type)
                pwm_period = device_info.get('pwm_period', 100)  # Default 100 seconds
                self._pid_controllers[pid_key] = PIDController(
                    kp=pid_params['kp'],
                    ki=pid_params['ki'],
                    kd=pid_params['kd'],
                    pwm_period=pwm_period,
                    database=self.database,
                    device_type=device_type
                )
            
            pid_controller = self._pid_controllers[pid_key]
            
            # Reset PID integrator on mode switch (prevents wind-up)
            if mode_changed:
                pid_controller.reset_integrator()
                logger.debug(f"Reset PID integrator for {location}/{cluster}/{device_name}/{setpoint_type} on mode change: {previous_mode} -> {current_mode}")
            
            # Reload PID parameters from Redis/DB if changed
            pid_controller.reload_parameters()
            
            # Compute PID output
            error = setpoint_value - current_value
            pid_output = pid_controller.compute(setpoint_value, current_value, dt=1.0)
            
            # Check if PID output indicates action is needed (threshold check)
            # For fans with cooling_setpoint: when error < 0 (temp > cooling_setpoint), fan should increase speed
            # For heaters with heating_setpoint: when error > 0 (temp < heating_setpoint), heater should increase
            # Use a small threshold to avoid unnecessary switching
            threshold = 0.5  # 0.5% minimum output to consider action needed
            
            if pid_output > threshold:
                # This setpoint needs action - use it and break (ignore lower priority setpoints)
                selected_pid_output = pid_output
                selected_setpoint_type = setpoint_type
                
                # Store PID values for logging
                context['pid_output'] = pid_output
                context['pid_kp'] = pid_controller.kp
                context['pid_ki'] = pid_controller.ki
                context['pid_kd'] = pid_controller.kd
                context['active_setpoint_type'] = setpoint_type
                context['active_setpoint_priority'] = priority
                break
        
        # If no setpoint needed action, exit
        if selected_pid_output is None:
            return
        
        # Get PWM state using selected PID output
        pid_controller = self._pid_controllers[(location, cluster, device_name, selected_setpoint_type)]
        pwm_state = pid_controller.get_pwm_state(selected_pid_output, current_time)
        duty_cycle = pid_controller.get_duty_cycle()
        context['duty_cycle_percent'] = duty_cycle
        context['control_reason'] = 'pid'
        
        # Apply PWM state
        new_state = 1 if pwm_state else 0
        current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
        
        if new_state != current_state:
            await self._set_device_state(
                location, cluster, device_name, new_state,
                'auto', 'pid', sensor_values, setpoint_data.get(selected_setpoint_type)
            )
    
    async def _process_vpd_control(
        self,
        location: str,
        cluster: str,
        device_name: str,
        device_info: Dict[str, Any],
        sensor_values: Dict[str, Optional[float]],
        current_time: datetime,
        context: Dict[str, Any]
    ) -> None:
        """Process VPD-based control for dehumidifying devices (fans, dehumidifiers).
        
        When VPD is below setpoint, turn ON dehumidifying devices to increase VPD.
        When VPD is at or above setpoint, turn OFF dehumidifying devices.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
        """
        try:
            # Get current mode to determine which setpoint to use
            current_mode_str = None
            if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                # Try to get current time-based mode from scheduler or Redis
                # For now, use default setpoint (mode=NULL)
                # TODO: Integrate with scheduler to get current DAY/NIGHT/TRANSITION mode
                pass
            
            # Get setpoint (use default/legacy for now, can be enhanced to use mode-based)
            setpoint_data = await self.database.get_setpoint(location, cluster, current_mode_str)
            if not setpoint_data:
                return  # No setpoint configured
            
            vpd_setpoint = setpoint_data.get('vpd')
            if vpd_setpoint is None:
                return  # No VPD setpoint configured
            
            # Get VPD sensor name from mapping
            sensor_mapping = self.config.get_sensor_mapping()
            location_sensors = sensor_mapping.get(location, {})
            cluster_sensors = location_sensors.get(cluster, {})
            vpd_sensor_name = cluster_sensors.get('vpd_sensor')
            
            if not vpd_sensor_name:
                logger.debug(f"No VPD sensor mapping for {location}/{cluster}")
                return
            
            # Get current VPD value
            current_vpd = sensor_values.get(vpd_sensor_name)
            
            if current_vpd is None:
                # Try to get from Redis last good value
                if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                    last_good = self.database._automation_redis.read_last_good_value(cluster, vpd_sensor_name)
                    if last_good:
                        hold_period = self.config.get('control.last_good_hold_period', 30)
                        is_valid, age = self.database._automation_redis.check_last_good_age(cluster, vpd_sensor_name, hold_period)
                        if is_valid:
                            current_vpd = last_good['value']
                        else:
                            # Last good value expired
                            if self.alarm_manager:
                                self.alarm_manager.raise_alarm(
                                    location, cluster, f"{vpd_sensor_name}_offline",
                                    'critical', f"VPD sensor {vpd_sensor_name} offline for {age:.1f}s"
                                )
                            return
                    else:
                        return  # No VPD sensor value available
                else:
                    return  # No VPD sensor value and no Redis
            
            # Update last good value if sensor is valid
            if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                self.database._automation_redis.write_last_good_value(cluster, vpd_sensor_name, current_vpd)
            
            # Control logic: If VPD < setpoint, turn ON dehumidifying device
            # If VPD >= setpoint, turn OFF dehumidifying device
            # Add small hysteresis to prevent rapid cycling (0.1 kPa)
            hysteresis = 0.1  # kPa
            
            current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
            target_state = 0
            
            if current_vpd < (vpd_setpoint - hysteresis):
                # VPD is below setpoint, need to increase VPD → turn ON dehumidifying device
                target_state = 1
                context['control_reason'] = 'vpd_control'
            elif current_vpd >= (vpd_setpoint + hysteresis):
                # VPD is at or above setpoint → turn OFF dehumidifying device
                target_state = 0
                context['control_reason'] = 'vpd_control'
            else:
                # VPD is within hysteresis band, maintain current state
                target_state = current_state
                context['control_reason'] = 'vpd_control_hysteresis'
            
            # Set device state if changed
            if target_state != current_state:
                await self._set_device_state(
                    location, cluster, device_name, target_state,
                    'auto', f'vpd_control (VPD: {current_vpd:.2f}kPa, setpoint: {vpd_setpoint:.2f}kPa)',
                    sensor_values
                )
                logger.info(
                    f"VPD control: {location}/{cluster}/{device_name} "
                    f"{'ON' if target_state == 1 else 'OFF'} "
                    f"(VPD: {current_vpd:.2f}kPa, setpoint: {vpd_setpoint:.2f}kPa)"
                )
        except Exception as e:
            logger.error(f"Error in VPD control for {location}/{cluster}/{device_name}: {e}")
            import traceback
            traceback.print_exc()
    
    async def _set_device_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        state: int,
        mode: str,
        reason: str,
        sensor_values: Dict[str, Optional[float]],
        setpoint: Optional[float] = None
    ) -> None:
        """Set device state and log action.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            state: New state (0/1)
            mode: Control mode
            reason: Control reason
            sensor_values: Sensor values for logging
            setpoint: Setpoint value for logging
        """
        current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
        
        # Set device state
        success, error_reason = self.relay_manager.set_device_state(
            location, cluster, device_name, state, mode
        )
        
        if not success:
            logger.warning(f"Failed to set device state: {error_reason}")
            if error_reason and 'interlock' in error_reason.lower():
                reason = 'interlock'
        
        # Get channel for logging
        channel = self.relay_manager.get_channel(location, cluster, device_name) or 0
        
        # Get sensor value for logging (use first available)
        sensor_value = None
        for value in sensor_values.values():
            if value is not None:
                sensor_value = value
                break
        
        # Log to database
        await self.database.set_device_state(location, cluster, device_name, channel, state, mode)
        await self.database.log_control_action(
            location, cluster, device_name, channel,
            current_state, state, mode, reason,
            sensor_value, setpoint
        )
    
    async def _log_automation_state(self) -> None:
        """Log automation state for all devices."""
        devices = self.config.get_devices()
        
        for location, clusters in devices.items():
            for cluster, cluster_devices in clusters.items():
                for device_name in cluster_devices.keys():
                    key = (location, cluster, device_name)
                    context = self._automation_context.get(key, {})
                    
                    current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
                    current_mode = self.relay_manager.get_device_mode(location, cluster, device_name) or 'auto'
                    
                    await self.database.log_automation_state(
                        location, cluster, device_name,
                        current_state, current_mode,
                        context.get('pid_output'),
                        context.get('duty_cycle_percent'),
                        context.get('active_rule_ids', []),
                        context.get('active_schedule_ids', []),
                        context.get('control_reason', 'unknown'),
                        context.get('schedule_ramp_up_duration'),
                        context.get('schedule_ramp_down_duration'),
                        context.get('schedule_photoperiod_hours'),
                        context.get('pid_kp'),
                        context.get('pid_ki'),
                        context.get('pid_kd')
                    )

