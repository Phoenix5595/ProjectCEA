"""Time-based scheduler for device control."""
import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)


def is_time_in_range(t: int, start: int, end: int) -> bool:
    """Check if time t (in minutes since midnight) is in range [start, end).
    Handles overnight wrap-around correctly.
    
    Args:
        t: Time in minutes since midnight (0-1439)
        start: Start time in minutes since midnight (0-1439)
        end: End time in minutes since midnight (0-1439)
    
    Returns:
        True if t is in range [start, end), False otherwise
    """
    if start <= end:
        return start <= t < end
    else:
        return t >= start or t < end


class Scheduler:
    """Manages time-based device schedules."""
    
    def __init__(self, schedules: List[Dict[str, any]]):
        """Initialize scheduler.
        
        Args:
            schedules: List of schedule dictionaries from database or config
        """
        self.schedules = schedules
        logger.info(f"Initialized scheduler with {len(schedules)} schedules")
    
    def is_schedule_active(
        self, 
        location: str, 
        cluster: str, 
        device_name: str,
        current_time: Optional[datetime] = None
    ) -> Tuple[bool, Optional[int]]:
        """Check if a schedule is active for a device.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
        
        Returns:
            Tuple of (is_active, schedule_id)
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday
        
        for schedule in self.schedules:
            if not schedule.get('enabled', True):
                continue
            
            if (schedule.get('location') == location and
                schedule.get('cluster') == cluster and
                schedule.get('device_name') == device_name):
                
                # Check day of week
                day_of_week = schedule.get('day_of_week')
                if day_of_week is not None and day_of_week != current_weekday:
                    continue
                
                # Check time range
                start_time = self._parse_time(schedule.get('start_time'))
                end_time = self._parse_time(schedule.get('end_time'))
                
                if start_time and end_time:
                    # Handle overnight schedules (e.g., 22:00 to 06:00)
                    if start_time > end_time:
                        # Overnight schedule
                        if current_time_obj >= start_time or current_time_obj < end_time:
                            schedule_id = schedule.get('id')  # May be None if from config
                            return (True, schedule_id)
                    else:
                        # Normal schedule
                        if start_time <= current_time_obj < end_time:
                            schedule_id = schedule.get('id')  # May be None if from config
                            return (True, schedule_id)
        
        return (False, None)
    
    def get_schedule_state(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: Optional[datetime] = None
    ) -> Optional[int]:
        """Get schedule state for a device (1 = ON, 0 = OFF, None = no schedule).
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
        
        Returns:
            1 if schedule wants device ON, 0 if OFF, None if no active schedule
        """
        is_active, schedule_id = self.is_schedule_active(location, cluster, device_name, current_time)
        if is_active:
            # Check if this is a NIGHT mode schedule - those should turn devices OFF
            for schedule in self.schedules:
                if schedule.get('id') == schedule_id:
                    mode = schedule.get('mode', '').upper()
                    if mode == 'NIGHT':
                        return 0  # NIGHT mode schedules turn devices OFF
                    # For DAY mode or no mode, return ON
                    return 1
            # If schedule not found by ID, default to ON (backward compatibility)
            return 1
        return None
    
    def get_active_schedule_details(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """Get details of active schedule including ramp durations and photoperiod.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
        
        Returns:
            Dict with schedule details (ramp_up_duration, ramp_down_duration, start_time, end_time, photoperiod_hours)
            or None if no active schedule
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()
        
        for schedule in self.schedules:
            if not schedule.get('enabled', True):
                continue
            
            if (schedule.get('location') == location and
                schedule.get('cluster') == cluster and
                schedule.get('device_name') == device_name):
                
                day_of_week = schedule.get('day_of_week')
                if day_of_week is not None and day_of_week != current_weekday:
                    continue
                
                start_time = self._parse_time(schedule.get('start_time'))
                end_time = self._parse_time(schedule.get('end_time'))
                
                if not start_time or not end_time:
                    continue
                
                is_in_range = False
                if start_time > end_time:
                    is_in_range = current_time_obj >= start_time or current_time_obj < end_time
                else:
                    is_in_range = start_time <= current_time_obj < end_time
                
                if is_in_range:
                    # Calculate photoperiod (duration of schedule in hours)
                    start_minutes = start_time.hour * 60 + start_time.minute
                    end_minutes = end_time.hour * 60 + end_time.minute
                    if end_minutes < start_minutes:
                        photoperiod_hours = (end_minutes + 1440 - start_minutes) / 60.0
                    else:
                        photoperiod_hours = (end_minutes - start_minutes) / 60.0
                    
                    return {
                        'ramp_up_duration': schedule.get('ramp_up_duration'),
                        'ramp_down_duration': schedule.get('ramp_down_duration'),
                        'start_time': schedule.get('start_time'),
                        'end_time': schedule.get('end_time'),
                        'photoperiod_hours': photoperiod_hours
                    }
        
        return None
    
    def get_schedule_intensity(
        self,
        location: str,
        cluster: str,
        device_name: str,
        current_time: Optional[datetime] = None,
        current_intensity: Optional[float] = None
    ) -> Optional[float]:
        """Get target intensity for a device from active schedule, with ramp calculation.
        
        Args:
            location: Location name
            cluster: Cluster name
            device_name: Device name
            current_time: Current time (default: now)
            current_intensity: Current light intensity (0-100%), used as ramp start point
        
        Returns:
            Target intensity (0-100%) if schedule is active and has target_intensity,
            None if no active schedule or no target_intensity set
        """
        if current_time is None:
            current_time = datetime.now()
        
        current_time_obj = current_time.time()
        current_weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday
        
        for schedule in self.schedules:
            if not schedule.get('enabled', True):
                continue
            
            if (schedule.get('location') == location and
                schedule.get('cluster') == cluster and
                schedule.get('device_name') == device_name):
                
                # Check day of week
                day_of_week = schedule.get('day_of_week')
                if day_of_week is not None and day_of_week != current_weekday:
                    continue
                
                # Check time range
                start_time = self._parse_time(schedule.get('start_time'))
                end_time = self._parse_time(schedule.get('end_time'))
                
                if not start_time or not end_time:
                    continue
                
                is_in_range = False
                if start_time > end_time:
                    # Overnight schedule
                    is_in_range = current_time_obj >= start_time or current_time_obj < end_time
                else:
                    # Normal schedule
                    is_in_range = start_time <= current_time_obj < end_time
                
                if not is_in_range:
                    continue
                
                # Check if schedule has target_intensity (ramp schedule)
                target_intensity = schedule.get('target_intensity')
                if target_intensity is None:
                    # No intensity specified, return None (use default ON/OFF behavior)
                    return None
                
                # Calculate ramp intensity
                ramp_up_duration = schedule.get('ramp_up_duration', 0) or 0
                ramp_down_duration = schedule.get('ramp_down_duration', 0) or 0
                
                # Calculate time since schedule start/end
                start_datetime = datetime.combine(current_time.date(), start_time)
                end_datetime = datetime.combine(current_time.date(), end_time)
                
                # Handle overnight schedules
                if start_time > end_time:
                    if current_time_obj >= start_time:
                        # Before midnight
                        start_datetime = datetime.combine(current_time.date(), start_time)
                        end_datetime = datetime.combine(current_time.date() + timedelta(days=1), end_time)
                    else:
                        # After midnight
                        start_datetime = datetime.combine(current_time.date() - timedelta(days=1), start_time)
                        end_datetime = datetime.combine(current_time.date(), end_time)
                
                time_since_start = (current_time - start_datetime).total_seconds() / 60.0  # minutes
                time_until_end = (end_datetime - current_time).total_seconds() / 60.0  # minutes
                
                # Determine if we're in ramp up, steady state, or ramp down
                if ramp_up_duration > 0 and time_since_start < ramp_up_duration:
                    # Ramp up period
                    if current_intensity is None:
                        current_intensity = 0.0
                    progress = min(time_since_start / ramp_up_duration, 1.0)
                    intensity = current_intensity + (target_intensity - current_intensity) * progress
                    return max(0.0, min(100.0, intensity))
                
                elif ramp_down_duration > 0 and time_until_end < ramp_down_duration:
                    # Ramp down period
                    if current_intensity is None:
                        current_intensity = target_intensity
                    progress = min(time_until_end / ramp_down_duration, 1.0)
                    intensity = current_intensity * progress  # Ramp down to 0
                    return max(0.0, min(100.0, intensity))
                
                else:
                    # Steady state - return target intensity
                    return max(0.0, min(100.0, target_intensity))
        
        return None
    
    def _parse_time(self, time_str: Optional[str]) -> Optional[time]:
        """Parse time string to time object.
        
        Args:
            time_str: Time string in format "HH:MM" or "HH:MM:SS"
        
        Returns:
            time object or None
        """
        if time_str is None:
            return None
        
        try:
            if isinstance(time_str, time):
                return time_str
            
            parts = str(time_str).split(':')
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                second = int(parts[2]) if len(parts) > 2 else 0
                return time(hour, minute, second)
        except (ValueError, IndexError) as e:
            logger.error(f"Error parsing time '{time_str}': {e}")
        
        return None
    
    def update_schedules(self, schedules: List[Dict[str, any]]):
        """Update schedules list."""
        self.schedules = schedules
        logger.info(f"Updated schedules: {len(schedules)} schedules")
    
    def get_climate_mode(
        self,
        location: str,
        cluster: str,
        current_time: Optional[datetime] = None,
        day_start_time: Optional[str] = None,
        day_end_time: Optional[str] = None,
        pre_day_duration: Optional[int] = None,
        pre_night_duration: Optional[int] = None
    ) -> Optional[Tuple[str, int, int]]:
        """Get current climate mode (PRE_DAY, DAY, PRE_NIGHT, NIGHT) for a location/cluster.
        
        CRITICAL: Returns DISCRETE mode only (mode, mode_start_time, mode_end_time).
        Does NOT calculate ramp progress - that belongs in control engine.
        
        Args:
            location: Location name
            cluster: Cluster name
            current_time: Current time (default: now)
            day_start_time: Day start time in HH:MM format (from light schedule)
            day_end_time: Day end time in HH:MM format (from light schedule)
            pre_day_duration: Pre-day duration in minutes (from climate schedule)
            pre_night_duration: Pre-night duration in minutes (from climate schedule)
        
        Returns:
            Tuple of (mode, mode_start_minutes, mode_end_minutes) or None if insufficient data
            mode: 'PRE_DAY', 'DAY', 'PRE_NIGHT', or 'NIGHT'
            mode_start_minutes: Start time in minutes since midnight (0-1439)
            mode_end_minutes: End time in minutes since midnight (0-1439)
        """
        if current_time is None:
            current_time = datetime.now()
        
        # Need day/night times to calculate periods
        if not day_start_time or not day_end_time:
            return None
        
        # Convert times to minutes since midnight
        day_start_min = self._time_to_minutes(day_start_time)
        day_end_min = self._time_to_minutes(day_end_time)
        current_min = current_time.hour * 60 + current_time.minute
        
        # Default durations to 0 if not provided
        pre_day_dur = pre_day_duration or 0
        pre_night_dur = pre_night_duration or 0
        
        # Calculate pre-day period: starts at (day_start_time - pre_day_duration) % 1440, ends at day_start_time
        pre_day_start_min = (day_start_min - pre_day_dur) % 1440
        pre_day_end_min = day_start_min
        
        # Calculate pre-night period: starts at night_start_time, ends at (night_start_time + pre_night_duration) % 1440
        night_start_min = day_end_min
        pre_night_start_min = night_start_min
        pre_night_end_min = (night_start_min + pre_night_dur) % 1440
        
        # Calculate night period: starts at pre_night_end, ends at pre_day_start
        night_start_actual_min = pre_night_end_min
        night_end_min = pre_day_start_min
        
        # Check which period we're in (priority: PRE_DAY > DAY > PRE_NIGHT > NIGHT)
        # PRE_DAY period
        if is_time_in_range(current_min, pre_day_start_min, pre_day_end_min):
            return ('PRE_DAY', pre_day_start_min, pre_day_end_min)
        
        # DAY period
        if is_time_in_range(current_min, day_start_min, day_end_min):
            return ('DAY', day_start_min, day_end_min)
        
        # PRE_NIGHT period
        if is_time_in_range(current_min, pre_night_start_min, pre_night_end_min):
            return ('PRE_NIGHT', pre_night_start_min, pre_night_end_min)
        
        # NIGHT period (everything else)
        if is_time_in_range(current_min, night_start_actual_min, night_end_min):
            return ('NIGHT', night_start_actual_min, night_end_min)
        
        # Fallback: if we somehow don't match any period, return NIGHT
        return ('NIGHT', night_start_actual_min, night_end_min)
    
    def _time_to_minutes(self, time_str: Optional[str]) -> int:
        """Convert time string (HH:MM) to minutes since midnight.
        
        Args:
            time_str: Time string in format "HH:MM"
        
        Returns:
            Minutes since midnight (0-1439)
        """
        if time_str is None:
            return 0
        
        try:
            if isinstance(time_str, time):
                return time_str.hour * 60 + time_str.minute
            
            parts = str(time_str).split(':')
            if len(parts) >= 2:
                hour = int(parts[0])
                minute = int(parts[1])
                return (hour * 60 + minute) % 1440
        except (ValueError, IndexError) as e:
            logger.error(f"Error converting time '{time_str}' to minutes: {e}")
        
        return 0
    
    def validate_climate_schedule_conflicts(
        self,
        day_start_time: str,
        day_end_time: str,
        pre_day_duration: int,
        pre_night_duration: int
    ) -> Tuple[bool, Optional[str]]:
        """Validate climate schedule for conflicts.
        
        Args:
            day_start_time: Day start time in HH:MM format
            day_end_time: Day end time in HH:MM format
            pre_day_duration: Pre-day duration in minutes
            pre_night_duration: Pre-night duration in minutes
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Convert to minutes
        day_start_min = self._time_to_minutes(day_start_time)
        day_end_min = self._time_to_minutes(day_end_time)
        
        # Calculate night length
        if day_end_min > day_start_min:
            night_length = 1440 - (day_end_min - day_start_min)
        else:
            night_length = day_start_min - day_end_min
        
        # Validate: pre_day_duration + pre_night_duration < night_length
        if pre_day_duration + pre_night_duration >= night_length:
            return (False, f"pre_day_duration ({pre_day_duration} min) + pre_night_duration ({pre_night_duration} min) must be less than night_length ({night_length} min)")
        
        # Validate max durations
        max_duration = 240
        if pre_day_duration > max_duration:
            return (False, f"pre_day_duration ({pre_day_duration} min) exceeds maximum ({max_duration} min)")
        
        if pre_night_duration > max_duration:
            return (False, f"pre_night_duration ({pre_night_duration} min) exceeds maximum ({max_duration} min)")
        
        # Validate non-negative
        if pre_day_duration < 0:
            return (False, f"pre_day_duration must be >= 0")
        
        if pre_night_duration < 0:
            return (False, f"pre_night_duration must be >= 0")
        
        return (True, None)

