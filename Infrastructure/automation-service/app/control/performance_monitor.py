"""Performance monitoring for control loop."""

from collections import deque
from datetime import datetime
from typing import Any

from shared.infra_logging import get_logger

logger = get_logger(__name__)


class PerformanceMonitor:
    """Monitor performance metrics for control loop operations."""

    def __init__(self, max_history: int = 100):
        """Initialize performance monitor.

        Args:
            max_history: Maximum number of measurements to keep
        """
        self.max_history = max_history
        self.metrics: dict[str, deque] = {
            "total_loop_time": deque(maxlen=max_history),
            "sensor_reading_time": deque(maxlen=max_history),
            "setpoint_calculation_time": deque(maxlen=max_history),
            "device_processing_time": deque(maxlen=max_history),
        }
        self.slow_operations: list[dict[str, Any]] = []
        self.max_slow_operations = 50

    def record_operation(self, operation_name: str, duration: float) -> None:
        """Record an operation duration.

        Args:
            operation_name: Name of the operation
            duration: Duration in seconds
        """
        if operation_name in self.metrics:
            self.metrics[operation_name].append(duration)

        # Track slow operations (>1 second)
        if duration > 1.0:
            self.slow_operations.append(
                {
                    "operation": operation_name,
                    "duration": duration,
                    "timestamp": datetime.now().isoformat(),
                }
            )

            # Keep only recent slow operations
            if len(self.slow_operations) > self.max_slow_operations:
                self.slow_operations = self.slow_operations[-self.max_slow_operations :]

            logger.warning(f"Slow operation detected: {operation_name} took {duration:.3f}s")

    def get_statistics(self) -> dict[str, Any]:
        """Get performance statistics.

        Returns:
            Dictionary with performance statistics for each metric
        """
        stats = {}

        for metric_name, values in self.metrics.items():
            if not values:
                stats[metric_name] = {
                    "count": 0,
                    "average": 0.0,
                    "min": 0.0,
                    "max": 0.0,
                    "p95": 0.0,
                    "p99": 0.0,
                }
                continue

            sorted_values = sorted(values)
            n = len(sorted_values)

            stats[metric_name] = {
                "count": n,
                "average": sum(values) / n,
                "min": min(values),
                "max": max(values),
                "p95": sorted_values[int(n * 0.95)] if n > 0 else 0.0,
                "p99": sorted_values[int(n * 0.99)] if n > 0 else 0.0,
            }

        stats["slow_operations"] = self.slow_operations[-10:]  # Last 10 slow operations

        return stats

    def reset(self) -> None:
        """Reset all metrics."""
        for metric in self.metrics.values():
            metric.clear()
        self.slow_operations.clear()


# Global performance monitor instance
_performance_monitor: PerformanceMonitor | None = None


def get_performance_monitor() -> PerformanceMonitor:
    """Get or create global performance monitor instance.

    Returns:
        PerformanceMonitor instance
    """
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = PerformanceMonitor()
    return _performance_monitor
