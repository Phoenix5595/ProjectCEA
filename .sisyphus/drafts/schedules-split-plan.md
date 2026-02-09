# Schedules.py Split Plan

## Current Structure

- **Total Lines**: 1,415
- **Pydantic Models**: 5 (ScheduleCreate, ScheduleUpdate, RoomScheduleCreate, ClimateScheduleSetpoint, ClimateScheduleCreate)
- **Endpoint Functions**: 15+
- **Largest Function**: `save_room_schedule()` - 350 lines (703-1055)

## Target Modules

### routes/schedules/models.py
Shared Pydantic models (keep separate to avoid circular imports):
- `ScheduleCreate` - Base schedule creation model
- `ScheduleUpdate` - Base schedule update model
- `RoomScheduleCreate` - Room schedule request model
- `ClimateScheduleSetpoint` - Climate setpoint model
- `ClimateScheduleCreate` - Climate schedule request model

### routes/schedules/base.py
Base CRUD operations (~250 lines):
- `get_schedules()` (lines ~187-250) - List all schedules
- `create_schedule()` (lines ~250-320) - Create single schedule
- `update_schedule()` (lines ~320-380) - Update schedule
- `delete_schedule()` (lines ~380-420) - Delete schedule
- `delete_schedules_bulk()` (lines ~420-450) - Bulk delete

### routes/schedules/room.py
Room schedule operations (~400 lines):
- `get_room_schedule()` (lines 520-700) - Get room schedule
- `save_room_schedule()` (lines 703-1055) - Save room schedule (350 lines - NEEDS SERVICE EXTRACTION)
- `sync_room_schedule()` (lines 1058-1095) - Sync to Redis
- `sync_all_room_schedules()` (lines 470-517) - Sync all rooms

### routes/schedules/climate.py
Climate schedule operations (~300 lines):
- `get_climate_schedule()` (lines 1119-1170) - Get climate schedule
- `save_climate_schedule()` (lines 1175-1415) - Save climate schedule

### routes/schedules/__init__.py
Router aggregation:
```python
from fastapi import APIRouter
from .base import router as base_router
from .room import router as room_router
from .climate import router as climate_router

router = APIRouter(prefix="/schedules", tags=["schedules"])
router.include_router(base_router)
router.include_router(room_router)
router.include_router(climate_router)
```

## Shared Dependencies

All modules need:
- `from fastapi import APIRouter, HTTPException, Depends`
- `from app.database import DatabaseManager`
- `from app.main import get_database`
- `from .models import ScheduleCreate, ScheduleUpdate, ...`

## Business Logic to Extract (Task 3)

The `save_room_schedule()` function (350 lines) should be split:
1. **Validation logic** → Keep in route (thin)
2. **Schedule calculation** → `services/schedule_service.py`
3. **Database operations** → Already in ScheduleRepository
4. **Redis sync** → `services/schedule_service.py`

Target: Route function < 50 lines, delegates to service.

## Migration Order

1. **First**: Create `models.py` with all Pydantic models
2. **Second**: Create `base.py` with CRUD endpoints (least dependencies)
3. **Third**: Create `climate.py` (simpler than room)
4. **Fourth**: Create `room.py` (most complex, depends on others)
5. **Fifth**: Create `__init__.py` to aggregate routers
6. **Last**: Update `routes/routes.py` to use new package, remove old file

## Risk Mitigation

- Keep old `schedules.py` until new modules verified
- Test each endpoint after migration
- Run `ruff check` after each file creation
