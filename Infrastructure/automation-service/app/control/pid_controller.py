"""PID controller for temperature and CO2 control."""
import logging
from typing import Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PIDController:
    """PID controller with time-based PWM output."""
    
    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        pwm_period: int = 100,
        database: Optional[object] = None,
        device_type: Optional[str] = None
    ):
        """Initialize PID controller.
        
        Args:
            kp: Proportional gain
            ki: Integral gain
            kd: Derivative gain
            pwm_period: PWM control period in seconds (default: 100)
            database: Optional DatabaseManager for dynamic parameter reload
            device_type: Optional device type for parameter reload
        """
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.pwm_period = pwm_period
        self.database = database
        self.device_type = device_type
        self._last_reload_time: Optional[datetime] = None
        
        # PID state
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time: Optional[datetime] = None
        
        # PWM state
        self._pwm_start_time: Optional[datetime] = None
        self._pwm_duty_cycle = 0.0  # 0-100%
        self._pwm_current_state = False  # Current ON/OFF state within period
        
        # Anti-windup
        self._integral_max = 100.0
        self._integral_min = -100.0
    
    def compute(self, setpoint: float, current_value: float, dt: float = 1.0) -> float:
        """Compute PID output.
        
        Args:
            setpoint: Target value
            current_value: Current measured value
            dt: Time step in seconds (default: 1.0 for 1-second control loop)
        
        Returns:
            PID output (0-100%)
        """
        error = setpoint - current_value
        
        # Proportional term
        p_term = self.kp * error
        
        # Integral term (with anti-windup)
        self._integral += error * dt
        # Clamp integral to prevent windup
        self._integral = max(self._integral_min, min(self._integral_max, self._integral))
        i_term = self.ki * self._integral
        
        # Derivative term
        d_term = 0.0
        if self._last_time is not None and dt > 0:
            d_error = (error - self._last_error) / dt
            d_term = self.kd * d_error
        
        # PID output
        output = p_term + i_term + d_term
        
        # Clamp output to 0-100%
        output = max(0.0, min(100.0, output))
        
        # Update state
        self._last_error = error
        self._last_time = datetime.now()
        
        return output
    
    def get_pwm_state(self, pid_output: float, current_time: datetime) -> bool:
        """Get current PWM state (ON/OFF) based on PID output and time.
        
        Args:
            pid_output: PID output percentage (0-100%)
            current_time: Current time
        
        Returns:
            True if device should be ON, False if OFF
        """
        # Update duty cycle if PID output changed
        if abs(self._pwm_duty_cycle - pid_output) > 0.1:  # Threshold to avoid jitter
            self._pwm_duty_cycle = pid_output
            self._pwm_start_time = current_time
            self._pwm_current_state = False  # Start with OFF
        
        if self._pwm_start_time is None:
            self._pwm_start_time = current_time
        
        # Calculate elapsed time in current period
        elapsed = (current_time - self._pwm_start_time).total_seconds()
        elapsed = elapsed % self.pwm_period  # Wrap around if period exceeded
        
        # Calculate ON and OFF durations
        on_duration = (self._pwm_duty_cycle / 100.0) * self.pwm_period
        off_duration = self.pwm_period - on_duration
        
        # Determine current state
        if elapsed < on_duration:
            self._pwm_current_state = True
        else:
            self._pwm_current_state = False
        
        return self._pwm_current_state
    
    def get_duty_cycle(self) -> float:
        """Get current duty cycle percentage."""
        return self._pwm_duty_cycle
    
    def reset(self):
        """Reset PID controller state."""
        self._integral = 0.0
        self._last_error = 0.0
        self._last_time = None
        self._pwm_start_time = None
        self._pwm_duty_cycle = 0.0
        self._pwm_current_state = False
    
    def reload_parameters(self) -> None:
        """Reload PID parameters from Redis/DB if changed.
        
        Checks Redis first (fast), falls back to database if Redis unavailable.
        Only reloads if parameters have changed.
        """
        if not self.database or not self.device_type:
            return
        
        # Rate limit reloads (check at most once per second)
        now = datetime.now()
        if self._last_reload_time and (now - self._last_reload_time).total_seconds() < 1.0:
            return
        
        try:
            # Try Redis first
            if self.database._automation_redis and self.database._automation_redis.redis_enabled:
                redis_params = self.database._automation_redis.read_pid_parameters(self.device_type)
                if redis_params:
                    new_kp = redis_params.get('kp')
                    new_ki = redis_params.get('ki')
                    new_kd = redis_params.get('kd')
                    
                    if (new_kp is not None and new_kp != self.kp) or \
                       (new_ki is not None and new_ki != self.ki) or \
                       (new_kd is not None and new_kd != self.kd):
                        old_kp, old_ki, old_kd = self.kp, self.ki, self.kd
                        self.kp = new_kp if new_kp is not None else self.kp
                        self.ki = new_ki if new_ki is not None else self.ki
                        self.kd = new_kd if new_kd is not None else self.kd
                        logger.info(f"PID parameters reloaded for {self.device_type}: Kp={old_kp}->{self.kp}, Ki={old_ki}->{self.ki}, Kd={old_kd}->{self.kd}")
                        self._last_reload_time = now
                    return
            
            # Fallback to database (async, but we'll do it synchronously here)
            # In practice, this would be done in the control loop
            # For now, we'll just check Redis
        except Exception as e:
            logger.debug(f"Error reloading PID parameters: {e}")
        
        self._last_reload_time = now

