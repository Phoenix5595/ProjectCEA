"""Control engine that orchestrates rules, schedules, and PID control."""
import logging
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
        """Process PID control for a device.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            device_info: Device configuration
            sensor_values: Available sensor values
            current_time: Current time
            context: Automation context dict
        """
        key = (location, cluster, device_name)
        device_type = device_info.get('device_type', '')
        
        # Get setpoint
        setpoint_data = await self.database.get_setpoint(location, cluster)
        if not setpoint_data:
            return  # No setpoint configured
        
        # Determine which sensor to use based on device type
        sensor_name = None
        setpoint_value = None
        
        if device_type == 'heater':
            sensor_mapping = self.config.get_sensor_mapping()
            location_sensors = sensor_mapping.get(location, {})
            cluster_sensors = location_sensors.get(cluster, {})
            sensor_name = cluster_sensors.get('temperature_sensor')
            setpoint_value = setpoint_data.get('temperature')
        elif device_type == 'co2':
            sensor_mapping = self.config.get_sensor_mapping()
            location_sensors = sensor_mapping.get(location, {})
            cluster_sensors = location_sensors.get(cluster, {})
            sensor_name = cluster_sensors.get('co2_sensor')
            setpoint_value = setpoint_data.get('co2')
        
        if not sensor_name or setpoint_value is None:
            return  # Missing sensor or setpoint
        
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
                        # Last good value expired, trigger failsafe
                        if self.alarm_manager:
                            self.alarm_manager.raise_alarm(
                                location, cluster, f"{sensor_name}_offline",
                                'critical', f"Sensor {sensor_name} offline for {age:.1f}s"
                            )
                        return
                else:
                    # No last good value, trigger alarm
                    if self.alarm_manager:
                        self.alarm_manager.raise_alarm(
                            location, cluster, f"{sensor_name}_offline",
                            'critical', f"Sensor {sensor_name} offline, no last good value"
                        )
                    return
            else:
                return  # Missing sensor value and no Redis, skip control
        
        # Update last good value if sensor is valid
        if self.database._automation_redis and self.database._automation_redis.redis_enabled:
            self.database._automation_redis.write_last_good_value(cluster, sensor_name, current_value)
        
        # Get or create PID controller
        if key not in self._pid_controllers:
            pid_params = self.config.get_pid_params_for_device(device_type)
            pwm_period = device_info.get('pwm_period', 100)  # Default 100 seconds
            self._pid_controllers[key] = PIDController(
                kp=pid_params['kp'],
                ki=pid_params['ki'],
                kd=pid_params['kd'],
                pwm_period=pwm_period,
                database=self.database,
                device_type=device_type
            )
        
        pid_controller = self._pid_controllers[key]
        
        # Reload PID parameters from Redis/DB if changed
        pid_controller.reload_parameters()
        
        # Compute PID output
        pid_output = pid_controller.compute(setpoint_value, current_value, dt=1.0)
        context['pid_output'] = pid_output
        # Store PID K values for logging
        context['pid_kp'] = pid_controller.kp
        context['pid_ki'] = pid_controller.ki
        context['pid_kd'] = pid_controller.kd
        
        # Get PWM state
        pwm_state = pid_controller.get_pwm_state(pid_output, current_time)
        duty_cycle = pid_controller.get_duty_cycle()
        context['duty_cycle_percent'] = duty_cycle
        context['control_reason'] = 'pid'
        
        # Apply PWM state
        new_state = 1 if pwm_state else 0
        current_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
        
        if new_state != current_state:
            await self._set_device_state(
                location, cluster, device_name, new_state,
                'auto', 'pid', sensor_values, setpoint_value
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

