"""Control loop timing instrumentation and statistics.

Provides thread-safe timing collection for control loop phases
with configurable history limits and percentile calculations.
"""

from __future__ import annotations

from collections.abc import Callable
from statistics import mean
import threading
import time
from typing import Any, TypeVar, cast

T = TypeVar("T", bound=Callable[..., Any])


class TimingStats:
    """Utility class for timing statistics calculations."""

    @staticmethod
    def calculate_percentile(data: list[float], percentile: float) -> float:
        """Calculate percentile of sorted data.

        Args:
            data: List of float values
            percentile: Percentile to calculate (0-100)

        Returns:
            Percentile value (0.0 if data is empty)
        """
        if not data:
            return 0.0

        sorted_data = sorted(data)
        if not sorted_data:
            return 0.0

        n = len(sorted_data)
        if percentile <= 0:
            return sorted_data[0]
        elif percentile >= 100:
            return sorted_data[-1]

        # Linear interpolation between ranks
        rank = (percentile / 100) * (n - 1)
        lower_index = int(rank)
        upper_index = min(lower_index + 1, n - 1)

        if lower_index == upper_index:
            return sorted_data[lower_index]

        # Linear interpolation
        weight = rank - lower_index
        return sorted_data[lower_index] * (1 - weight) + sorted_data[upper_index] * weight

    @staticmethod
    def calculate_average(data: list[float]) -> float:
        """Calculate average of data.

        Args:
            data: List of float values

        Returns:
            Average value (0.0 if data is empty)
        """
        if not data:
            return 0.0
        return mean(data)


class TimingCollector:
    """Thread-safe timing collector for control loop instrumentation.

    Tracks timing data for:
    - Total loop execution time
    - Individual phase timing (sensor_read_ms, pid_calc_ms, hardware_ops_ms, state_update_ms)
    - Historical data with configurable limits
    - Percentile calculations
    """

    def __init__(self, max_history: int = 1000, alert_threshold_ms: float = 900.0) -> None:
        self.max_history = max_history
        self.loop_count = 0
        self.alert_threshold_ms = alert_threshold_ms
        self.slow_loop_count = 0
        self._slow_loop_count = 0
        self._alert_threshold_ms = alert_threshold_ms
        self._alert_callback: Callable[[float], None] | None = None

        # Timing storage with thread locks
        self._loop_times: list[float] = []
        self._phase_times: dict[str, list[float]] = {}
        self._active_phases: dict[str, float] = {}

        # Thread safety
        self._lock = threading.RLock()

        logger = None  # Avoid circular import with shared logging
        try:
            from shared.infra_logging import get_logger

            logger = get_logger(__name__)
        except ImportError:
            pass

        if logger:
            logger.debug(f"TimingCollector initialized with max_history={max_history}")

    def start_phase(self, phase_name: str) -> None:
        """Start timing a phase.

        Args:
            phase_name: Name of the phase (e.g., 'sensor_read_ms')
        """
        with self._lock:
            timestamp = time.perf_counter() * 1000  # Convert to milliseconds
            self._active_phases[phase_name] = timestamp

    def end_phase(self, phase_name: str) -> float:
        """End timing a phase and return duration.

        Args:
            phase_name: Name of the phase to end

        Returns:
            Duration in milliseconds

        Raises:
            ValueError: If phase was not started
        """
        with self._lock:
            if phase_name not in self._active_phases:
                raise ValueError(f"Phase '{phase_name}' not started")

            start_time = self._active_phases.pop(phase_name)
            end_time = time.perf_counter() * 1000  # Convert to milliseconds
            duration = end_time - start_time

            # Store in phase history
            if phase_name not in self._phase_times:
                self._phase_times[phase_name] = []

            phase_list = self._phase_times[phase_name]
            phase_list.append(duration)

            # Respect history limit
            if len(phase_list) > self.max_history:
                phase_list.pop(0)

            return duration

    def record_loop_time(self, total_time_ms: float) -> None:
        with self._lock:
            self.loop_count += 1
            self._loop_times.append(total_time_ms)

            if total_time_ms > self.alert_threshold_ms:
                self.slow_loop_count += 1
                if self._alert_callback:
                    self._alert_callback(total_time_ms)

            # Respect history limit
            if len(self._loop_times) > self.max_history:
                self._loop_times.pop(0)

    def get_timing_stats(self) -> dict[str, float | int | dict[str, float]]:
        """Get current timing statistics.

        Returns:
            Dictionary with timing breakdown and statistics
        """
        with self._lock:
            phases: dict[str, float] = {}

            # Calculate phase averages
            for phase_name, times in self._phase_times.items():
                if times:
                    phases[phase_name] = TimingStats.calculate_average(times)
                else:
                    phases[phase_name] = 0.0

            result = {
                "last_loop_ms": self._loop_times[-1] if self._loop_times else 0.0,
                "avg_loop_ms": TimingStats.calculate_average(self._loop_times),
                "loop_count": self.loop_count,
                "phases": phases,
            }

            return result

    def get_histogram(self) -> dict[str, Any]:
        """Get histogram statistics for loop times.

        Returns:
            Dictionary with percentile statistics
        """
        with self._lock:
            if not self._loop_times:
                return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "count": 0}

            sorted_times = sorted(self._loop_times)

            return {
                "p50": TimingStats.calculate_percentile(sorted_times, 50),
                "p95": TimingStats.calculate_percentile(sorted_times, 95),
                "p99": TimingStats.calculate_percentile(sorted_times, 99),
                "min": sorted_times[0] if sorted_times else 0.0,
                "max": sorted_times[-1] if sorted_times else 0.0,
                "count": len(self._loop_times),
            }

    @property
    def loop_times(self) -> list[float]:
        """Get copy of loop times for external processing."""
        with self._lock:
            return self._loop_times.copy()

    @property
    def phase_times(self) -> dict[str, list[float]]:
        """Get copy of phase times for external processing."""
        with self._lock:
            return {phase: times.copy() for phase, times in self._phase_times.items()}

    def clear_history(self) -> None:
        """Clear all timing history (useful for testing or reset)."""
        with self._lock:
            self._loop_times.clear()
            self._phase_times.clear()
            self._active_phases.clear()
            self.loop_count = 0
            self._slow_loop_count = 0

    def set_alert_callback(self, callback: Callable[[float], None]) -> None:
        self._alert_callback = callback

    def set_alert_threshold(self, threshold_ms: float) -> None:
        self._alert_threshold_ms = threshold_ms

    def get_slow_loop_count(self) -> int:
        return self._slow_loop_count


# Global timing collector instance
_global_timing_collector: TimingCollector | None = None
_collector_lock = threading.Lock()


def get_timing_collector() -> TimingCollector:
    """Get global timing collector instance.

    Returns:
        Global TimingCollector instance (creates if needed)
    """
    global _global_timing_collector

    with _collector_lock:
        if _global_timing_collector is None:
            _global_timing_collector = TimingCollector(max_history=1000)
        return _global_timing_collector


def reset_timing_collector() -> None:
    """Reset global timing collector (useful for testing)."""
    global _global_timing_collector

    with _collector_lock:
        if _global_timing_collector:
            _global_timing_collector.clear_history()


# Context manager for timing phases
class PhaseTimer:
    """Context manager for timing a phase."""

    def __init__(self, phase_name: str, collector: TimingCollector | None = None):
        """Initialize phase timer.

        Args:
            phase_name: Name of the phase
            collector: TimingCollector instance (uses global if None)
        """
        self.phase_name = phase_name
        self.collector = collector or get_timing_collector()
        self.duration: float | None = None

    def __enter__(self) -> PhaseTimer:
        """Start timing when entering context."""
        self.collector.start_phase(self.phase_name)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """End timing when exiting context."""
        self.duration = self.collector.end_phase(self.phase_name)


# Convenience functions for common phases
def time_sensor_read(func: T) -> T:
    """Decorator to time sensor reading operations."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with PhaseTimer("sensor_read_ms"):
            return func(*args, **kwargs)

    return cast(T, wrapper)


def time_pid_calculation(func: T) -> T:
    """Decorator to time PID calculation operations."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with PhaseTimer("pid_calc_ms"):
            return func(*args, **kwargs)

    return cast(T, wrapper)


def time_hardware_operations(func: T) -> T:
    """Decorator to time hardware operations."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with PhaseTimer("hardware_ops_ms"):
            return func(*args, **kwargs)

    return cast(T, wrapper)


def time_state_update(func: T) -> T:
    """Decorator to time state update operations."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with PhaseTimer("state_update_ms"):
            return func(*args, **kwargs)

    return cast(T, wrapper)
