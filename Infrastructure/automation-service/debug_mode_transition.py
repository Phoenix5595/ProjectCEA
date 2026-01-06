#!/usr/bin/env python3

import asyncio
import sys
import os
sys.path.append('.')
os.environ['POSTGRES_PASSWORD'] = 'Lenin1917'
os.environ['REDIS_URL'] = 'redis://localhost:6379'

from app.database import DatabaseManager
from app.control.scheduler import Scheduler

async def test_mode_transition():
    db = DatabaseManager()
    await db.initialize()
    scheduler = Scheduler([])
    
    # Test the exact time when PRE_DAY starts
    current_time = 1035  # 17:15
    print(f"Testing at time {current_time} (17:15)")
    
    result = scheduler.get_climate_mode('Flower Room', 'main', db, current_time)
    if result is None:
        print("ERROR: get_climate_mode returned None")
        return
    mode, start_min, end_min = result
    print(f"Result: mode={mode}, start={start_min}, end={end_min}")
    
    # Also test what happens at 17:14 (should still be DAY)
    current_time_2 = 1034  # 17:14
    print(f"\nTesting at time {current_time_2} (17:14)")
    result2 = scheduler.get_climate_mode('Flower Room', 'main', db, current_time_2)
    if result2 is None:
        print("ERROR: get_climate_mode returned None at 17:14")
        return
    mode2, start_min2, end_min2 = result2
    print(f"Result: mode={mode2}, start={start_min2}, end={end_min2}")
    
    # Test what happens at 15:50 (should be PRE_DAY)
    current_time_3 = 950  # 15:50
    print(f"\nTesting at time {current_time_3} (15:50)")
    result3 = scheduler.get_climate_mode('Flower Room', 'main', db, current_time_3)
    if result3 is None:
        print("ERROR: get_climate_mode returned None at 15:50")
        return
    mode3, start_min3, end_min3 = result3
    print(f"Result: mode={mode3}, start={start_min3}, end={end_min3}")

if __name__ == '__main__':
    asyncio.run(test_mode_transition())