#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
import os

sys.path.append(".")

# Get password from environment variable (do not hardcode)
if not os.getenv("POSTGRES_PASSWORD"):
    raise ValueError("POSTGRES_PASSWORD environment variable is required")

if not os.getenv("REDIS_URL"):
    os.environ["REDIS_URL"] = "redis://localhost:6379"

from app.database import DatabaseManager
from app.control.scheduler import Scheduler


async def test_mode_transition():
    db = DatabaseManager()
    await db.initialize()
    scheduler = Scheduler([])

    # Test the exact time when PRE_DAY starts
    from datetime import datetime

    current_time = datetime(2023, 1, 1, 17, 15)  # 17:15
    print(f"Testing at time {current_time} (17:15)")

    result = scheduler.get_climate_mode("Flower Room", "main", current_time, "17:15", "06:00")
    if result is None:
        print("ERROR: get_climate_mode returned None")
        return
    mode, start_min, end_min = result
    print(f"Result: mode={mode}, start={start_min}, end={end_min}")

    # Also test what happens at 17:14 (should still be DAY)
    current_time_2 = datetime(2023, 1, 1, 17, 14)  # 17:14
    print(f"\nTesting at time {current_time_2} (17:14)")
    result2 = scheduler.get_climate_mode("Flower Room", "main", current_time_2, "06:00", "17:15")
    if result2 is None:
        print("ERROR: get_climate_mode returned None at 17:14")
        return
    mode2, start_min2, end_min2 = result2
    print(f"Result: mode={mode2}, start={start_min2}, end={end_min2}")

    # Test what happens at 15:50 (should be PRE_DAY)
    current_time_3 = datetime(2023, 1, 1, 15, 50)  # 15:50
    print(f"\nTesting at time {current_time_3} (15:50)")
    result3 = scheduler.get_climate_mode(
        "Flower Room",
        "main",
        current_time_3,
        day_start_time="06:00",
        day_end_time="17:15",
        pre_day_duration=90,
    )
    if result3 is None:
        print("ERROR: get_climate_mode returned None at 15:50")
        return
    mode3, start_min3, end_min3 = result3
    print(f"Result: mode={mode3}, start={start_min3}, end={end_min3}")

    # Also test what happens at 17:14 (should still be DAY)
    current_time_4 = datetime(2023, 1, 1, 17, 14)  # 17:14
    print(f"\nTesting at time {current_time_4} (17:14)")
    result4 = scheduler.get_climate_mode(
        "Flower Room", "main", current_time_4, day_start_time="06:00", day_end_time="17:15"
    )
    if result4 is None:
        print("ERROR: get_climate_mode returned None at 17:14")
        return
    mode4, start_min4, end_min4 = result4
    print(f"Result: mode={mode4}, start={start_min4}, end={end_min4}")

    # Test what happens at 15:50 (should be PRE_DAY)
    current_time_5 = datetime(2023, 1, 1, 15, 50)  # 15:50
    print(f"\nTesting at time {current_time_5} (15:50)")
    result5 = scheduler.get_climate_mode(
        "Flower Room",
        "main",
        current_time_5,
        day_start_time="06:00",
        day_end_time="17:15",
        pre_day_duration=90,
    )
    if result5 is None:
        print("ERROR: get_climate_mode returned None at 15:50")
        return
    mode5, start_min5, end_min5 = result5
    print(f"Result: mode={mode5}, start={start_min5}, end={end_min5}")


if __name__ == "__main__":
    asyncio.run(test_mode_transition())
