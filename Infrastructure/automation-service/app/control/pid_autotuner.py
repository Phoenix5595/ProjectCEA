"""PID Auto-Tuner using Relay Feedback Method (Åström-Hägglund).

Implements automatic PID tuning by inducing controlled oscillations
and calculating optimal Kp, Ki, Kd from the resulting waveform.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from shared.infra_logging import get_logger

logger = get_logger(__name__)


@dataclass
class TuningResult:
    kp: float
    ki: float
    kd: float
    ultimate_gain: float
    ultimate_period: float
    amplitude: float
    tuning_method: str


class RelayAutoTuner:
    """Relay feedback auto-tuner for PID controllers.

    Uses the Åström-Hägglund relay method:
    1. Apply relay output (+d when error > 0, -d when error < 0)
    2. Measure resulting oscillation amplitude (a) and period (Tu)
    3. Calculate ultimate gain Ku = 4d / (π * a)
    4. Apply Ziegler-Nichols or other tuning rules
    """

    # Tuning rules (Ku = ultimate gain, Tu = ultimate period)
    TUNING_RULES = {
        "ziegler_nichols": {"kp": 0.6, "ti": 0.5, "td": 0.125},
        "some_overshoot": {"kp": 0.33, "ti": 0.5, "td": 0.33},
        "no_overshoot": {"kp": 0.2, "ti": 0.5, "td": 0.33},
        "pessen_integral": {"kp": 0.7, "ti": 0.4, "td": 0.15},
    }

    def __init__(
        self,
        relay_amplitude: float = 50.0,
        hysteresis: float = 0.5,
        min_cycles: int = 3,
        max_cycles: int = 10,
        timeout_seconds: float = 600.0,
    ):
        """Initialize auto-tuner.

        Args:
            relay_amplitude: Output amplitude (0-100%), default 50%
            hysteresis: Deadband around setpoint to prevent chatter
            min_cycles: Minimum oscillation cycles before calculating
            max_cycles: Maximum cycles before stopping
            timeout_seconds: Timeout for tuning process
        """
        self.relay_amplitude = relay_amplitude
        self.hysteresis = hysteresis
        self.min_cycles = min_cycles
        self.max_cycles = max_cycles
        self.timeout = timeout_seconds

        # State
        self._active = False
        self._start_time: datetime | None = None
        self._setpoint: float = 0.0
        self._relay_state = False  # False = low output, True = high output

        # Oscillation tracking
        self._zero_crossings: list[datetime] = []
        self._peaks: list[float] = []
        self._troughs: list[float] = []
        self._last_value: float | None = None
        self._tracking_peak = True
        self._current_extreme: float = 0.0

    def start(self, setpoint: float) -> None:
        """Start auto-tuning process."""
        self._active = True
        self._start_time = datetime.now()
        self._setpoint = setpoint
        self._relay_state = False
        self._zero_crossings = []
        self._peaks = []
        self._troughs = []
        self._last_value = None
        self._tracking_peak = True
        self._current_extreme = setpoint
        logger.info(f"Auto-tuning started: setpoint={setpoint}, relay_amp={self.relay_amplitude}%")

    def stop(self) -> None:
        """Stop auto-tuning process."""
        self._active = False
        logger.info("Auto-tuning stopped")

    @property
    def is_active(self) -> bool:
        return self._active

    def update(
        self, current_value: float, current_time: datetime
    ) -> tuple[float, TuningResult | None]:
        """Update auto-tuner with new measurement.

        Args:
            current_value: Current process value
            current_time: Current timestamp

        Returns:
            Tuple of (relay_output, tuning_result)
            tuning_result is None until tuning is complete
        """
        if not self._active:
            return 0.0, None

        # Check timeout
        if (
            self._start_time is not None
            and (current_time - self._start_time).total_seconds() > self.timeout
        ):
            logger.warning("Auto-tuning timeout")
            self.stop()
            return 0.0, None

        error = current_value - self._setpoint

        # Relay logic with hysteresis
        if self._relay_state and error < -self.hysteresis:
            self._relay_state = False
            self._zero_crossings.append(current_time)
        elif not self._relay_state and error > self.hysteresis:
            self._relay_state = True
            self._zero_crossings.append(current_time)

        # Track peaks and troughs
        if self._last_value is not None:
            if self._tracking_peak:
                if current_value > self._current_extreme:
                    self._current_extreme = current_value
                elif current_value < self._current_extreme - 0.1:
                    self._peaks.append(self._current_extreme)
                    self._current_extreme = current_value
                    self._tracking_peak = False
            else:
                if current_value < self._current_extreme:
                    self._current_extreme = current_value
                elif current_value > self._current_extreme + 0.1:
                    self._troughs.append(self._current_extreme)
                    self._current_extreme = current_value
                    self._tracking_peak = True

        self._last_value = current_value

        # Calculate relay output
        output = self.relay_amplitude if self._relay_state else 0.0

        # Check if we have enough data
        n_cycles = min(len(self._peaks), len(self._troughs))

        if n_cycles >= self.min_cycles:
            result = self._calculate_tuning()
            if result:
                self.stop()
                return output, result

        if n_cycles >= self.max_cycles:
            result = self._calculate_tuning()
            self.stop()
            return output, result

        return output, None

    def _calculate_tuning(self, method: str = "some_overshoot") -> TuningResult | None:
        """Calculate PID parameters from oscillation data."""
        if len(self._peaks) < 2 or len(self._troughs) < 2:
            return None

        if len(self._zero_crossings) < 4:
            return None

        # Calculate amplitude (average of peak-to-trough)
        amplitudes = []
        for i in range(min(len(self._peaks), len(self._troughs))):
            amplitudes.append(self._peaks[i] - self._troughs[i])
        amplitude = sum(amplitudes) / len(amplitudes) / 2  # Half peak-to-peak

        if amplitude < 0.01:
            logger.warning("Oscillation amplitude too small")
            return None

        # Calculate period (average time between zero crossings)
        periods = []
        for i in range(2, len(self._zero_crossings)):
            dt = (self._zero_crossings[i] - self._zero_crossings[i - 2]).total_seconds()
            periods.append(dt)

        if not periods:
            return None

        Tu = sum(periods) / len(periods)  # Ultimate period

        # Calculate ultimate gain: Ku = 4d / (π * a)
        Ku = (4 * self.relay_amplitude) / (math.pi * amplitude)

        # Apply tuning rules
        rules = self.TUNING_RULES.get(method, self.TUNING_RULES["some_overshoot"])

        Kp = rules["kp"] * Ku
        Ti = rules["ti"] * Tu  # Integral time
        Td = rules["td"] * Tu  # Derivative time

        Ki = Kp / Ti if Ti > 0 else 0.0
        Kd = Kp * Td

        logger.info(
            f"Auto-tuning complete: Ku={Ku:.3f}, Tu={Tu:.1f}s, Kp={Kp:.3f}, Ki={Ki:.4f}, Kd={Kd:.3f}"
        )

        return TuningResult(
            kp=Kp,
            ki=Ki,
            kd=Kd,
            ultimate_gain=Ku,
            ultimate_period=Tu,
            amplitude=amplitude,
            tuning_method=method,
        )
