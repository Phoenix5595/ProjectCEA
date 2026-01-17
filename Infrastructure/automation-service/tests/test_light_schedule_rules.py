from __future__ import annotations

from datetime import datetime, timedelta
import pathlib
import sys

import pytest

# Ensure the automation-service package is importable when running tests directly
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.control.scheduler import Scheduler
from app.routes.schedules import _ensure_light_schedules_are_daily, HTTPException


def _build_schedule(
    target: float,
    ramp_up: int,
    ramp_down: int,
    start: str = "06:00",
    end: str = "18:00",
    day_of_week=None,
) -> dict:
    return {
        "id": 1,
        "name": "Light Day",
        "location": "Room",
        "cluster": "main",
        "device_name": "light_1",
        "day_of_week": day_of_week,
        "start_time": start,
        "end_time": end,
        "enabled": True,
        "mode": "DAY",
        "target_intensity": target,
        "ramp_up_duration": ramp_up,
        "ramp_down_duration": ramp_down,
        "pre_day_duration": None,
        "pre_night_duration": None,
        "created_at": None,
    }


def test_light_schedule_must_be_daily():
    with pytest.raises(HTTPException):
        _ensure_light_schedules_are_daily("DAY", 50, 1)
    # No exception when day_of_week is null
    _ensure_light_schedules_are_daily("DAY", 50, None)


def test_ramp_up_recalculates_within_remaining_time():
    """Mid-ramp target increase finishes within original ramp window."""
    schedule = _build_schedule(target=50, ramp_up=5, ramp_down=5, end="06:30")
    scheduler = Scheduler([schedule])

    start_time = datetime(2024, 1, 1, 6, 0, 0)

    # 3 minutes into a 5-minute ramp from 10% to 50%: expect ~34% (10 + (50-10) * 3/5)
    t1 = start_time + timedelta(minutes=3)
    intensity_t1 = scheduler.get_schedule_intensity("Room", "main", "light_1", current_time=t1)
    assert 33.5 <= intensity_t1 <= 34.5

    # Change target to 70% mid-ramp and continue within remaining 2 minutes
    updated = _build_schedule(target=70, ramp_up=5, ramp_down=5, end="06:30")
    scheduler.update_schedules([updated])

    # Immediately after target change (4 minutes into ramp from 10% to 50%): should be ~42% (10 + (50-10) * 4/5)
    t2 = start_time + timedelta(minutes=4)
    intensity_t2 = scheduler.get_schedule_intensity("Room", "main", "light_1", current_time=t2)
    assert 41.5 <= intensity_t2 <= 42.5

    # By end of original ramp window (5 minutes), should reach new target 70%
    t3 = start_time + timedelta(minutes=5)
    intensity_t3 = scheduler.get_schedule_intensity("Room", "main", "light_1", current_time=t3)
    assert 69.5 <= intensity_t3 <= 70.5


def test_ramp_down_continues_to_minimum_even_if_target_increases():
    """Ramp down keeps going to 10% (minimum) even if target is raised mid-ramp."""
    schedule = _build_schedule(target=80, ramp_up=0, ramp_down=5, end="06:10")
    scheduler = Scheduler([schedule])

    start_time = datetime(2024, 1, 1, 6, 0, 0)

    # Two minutes before end (ramp-down running for 3 minutes of 5): 80 + (10-80) * 3/5 = 80 - 42 = 38%
    t1 = start_time + timedelta(minutes=8)
    intensity_t1 = scheduler.get_schedule_intensity("Room", "main", "light_1", current_time=t1)
    assert 37 <= intensity_t1 <= 39  # ~38%

    # Raise target mid-ramp; ramp should still head toward 10% (minimum)
    updated = _build_schedule(target=100, ramp_up=0, ramp_down=5, end="06:10")
    scheduler.update_schedules([updated])

    # One minute before end (ramp-down running for 4 minutes of 5): 80 + (10-80) * 4/5 = 80 - 56 = 24%
    t2 = start_time + timedelta(minutes=9)
    intensity_t2 = scheduler.get_schedule_intensity("Room", "main", "light_1", current_time=t2)
    assert 23 <= intensity_t2 <= 25  # continue downward trend toward 10%

    # Just before schedule end (ramp down complete): should be at 10% (minimum)
    t3 = start_time + timedelta(minutes=9, seconds=59)  # just before schedule end
    intensity_t3 = scheduler.get_schedule_intensity("Room", "main", "light_1", current_time=t3)
    assert 9.5 <= intensity_t3 <= 10.5  # At or very close to 10% minimum
