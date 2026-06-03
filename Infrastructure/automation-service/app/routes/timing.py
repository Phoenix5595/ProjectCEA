"""Timing instrumentation API endpoints.

Provides detailed control loop timing breakdown and histogram statistics.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.control.timing import get_timing_collector

router = APIRouter()


@router.get("/api/timing")
async def get_timing_breakdown() -> dict[str, Any]:
    """Get detailed control loop timing breakdown.

    Returns:
        Timing statistics with phase breakdown:
        - last_loop_ms: Duration of most recent loop
        - avg_loop_ms: Average loop time over history
        - phases: Dict with average timing for each phase:
            - sensor_read_ms: Sensor reading phase
            - pid_calc_ms: PID calculation phase
            - hardware_ops_ms: Hardware operations phase
            - state_update_ms: State update phase
        - loop_count: Total number of loops recorded
    """
    collector = get_timing_collector()
    return collector.get_timing_stats()


@router.get("/api/timing/histogram")
async def get_timing_histogram() -> dict[str, Any]:
    """Get control loop timing histogram with percentiles.

    Returns:
        Histogram statistics:
        - p50: 50th percentile (median)
        - p95: 95th percentile
        - p99: 99th percentile
        - min: Minimum loop time
        - max: Maximum loop time
        - count: Total number of measurements
    """
    collector = get_timing_collector()
    return collector.get_histogram()


@router.post("/api/timing/reset")
async def reset_timing_data() -> dict[str, str]:
    """Reset all timing data and clear history.

    Returns:
        Confirmation message.
    """
    from app.control.timing import reset_timing_collector

    reset_timing_collector()
    return {"message": "Timing data reset successfully"}
