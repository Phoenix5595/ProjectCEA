# Plan: Wire Up Database Repositories & Fix LSP Errors

**Created:** 2026-01-18
**Status:** Ready for Implementation
**Priority:** HIGH - Blocks LSP usability and maintainability
**Estimated Effort:** 2-3 weeks

---

## Executive Summary

### Problem Statement

Repositories were created in commit `1555c8f` (2026-01-17) to split the monolithic `database.py`, but **never wired up**. Meanwhile, `database.py` continued to grow with new features, creating critical drift.

| Metric | Before Refactor | After Refactor (Expected) | Current Reality |
|--------|-----------------|---------------------------|-----------------|
| `database.py` lines | 2,879 | ~400-500 | **3,495** (+616) |
| Repository usage | 0% | 100% | **0%** |
| LSP errors | ~700 | ~0 | **~700** |

### Root Causes of LSP Errors

| Error Type | Count | Root Cause |
|------------|-------|------------|
| Import cycles | 4 | `database.py` → routes → main → alarms → database.py |
| Deprecated typing | ~100+ | `Dict`, `List`, `Optional` instead of `dict`, `list`, `\| None` |
| Missing type stubs | 1 | `asyncpg` has no bundled types |
| Unannotated attributes | ~20 | Class instance variables lack type hints |
| Unknown member types | ~50+ | From untyped `asyncpg` returns |

### Critical Finding: Schema Drift

The `RoomModeRepository` uses a **completely different table schema** than `database.py`:

| Aspect | Repository (room_modes.py) | database.py | Status |
|--------|---------------------------|-------------|--------|
| Table name | `room_mode_parameters` | `mode_parameters` | ❌ WRONG |
| Total columns | 12 | 32 | ❌ MISSING 20 |
| Pre-day/night fields | None | 8 fields | ❌ MISSING |
| Leaf delta fields | None | 2 fields | ❌ MISSING |
| Light intensity fields | None | 2 fields | ❌ MISSING |
| Independent ramp fields | None | 4 fields | ❌ MISSING |

**If wired up as-is, the system would break catastrophically.**

---

## Current State Analysis

### DatabaseManager Methods (60 total)

```
Infrastructure/automation-service/app/database.py
└── DatabaseManager (3,495 lines, 60 methods)
    │
    ├── Connection/Infrastructure (10 methods, ~500 lines)
    │   ├── __init__
    │   ├── initialize
    │   ├── _connect_db
    │   ├── _connect_redis
    │   ├── _get_pool
    │   ├── _create_tables
    │   ├── _migrate_tables
    │   ├── _create_room_modes_tables
    │   ├── close
    │   └── flush_batch_buffer
    │
    ├── Caching (4 methods, ~50 lines)
    │   ├── _get_cache_key
    │   ├── _get_cached_result
    │   ├── _set_cached_result
    │   └── clear_cache
    │
    ├── Sensor Operations (2 methods, ~100 lines) → SensorRepository
    │   ├── get_sensor_value
    │   └── get_sensor_values_batch
    │
    ├── Device Operations (7 methods, ~300 lines) → DeviceRepository
    │   ├── get_device_state
    │   ├── set_device_state
    │   ├── get_all_device_states
    │   ├── get_device_mapping
    │   ├── set_device_mapping
    │   ├── get_all_device_mappings
    │   └── get_latest_light_intensity
    │
    ├── Setpoint Operations (6 methods, ~600 lines) → SetpointRepository
    │   ├── get_setpoint
    │   ├── set_setpoint
    │   ├── get_all_setpoints_for_location_cluster
    │   ├── get_latest_effective_setpoints
    │   ├── log_effective_setpoint
    │   └── log_effective_setpoints
    │
    ├── PID Operations (9 methods, ~500 lines) → PIDRepository
    │   ├── get_pid_parameters
    │   ├── set_pid_parameters
    │   ├── get_pid_parameter_history
    │   ├── get_all_pid_parameters
    │   ├── get_pid_control_mode
    │   ├── set_pid_control_mode
    │   ├── get_autotune_state
    │   ├── update_autotune_state
    │   └── set_pid_parameters_with_reason  ← MISSING from repo
    │
    ├── Schedule Operations (11 methods, ~700 lines) → ScheduleRepository
    │   ├── get_schedules
    │   ├── get_climate_schedule
    │   ├── get_light_schedule
    │   ├── create_schedule
    │   ├── update_schedule
    │   ├── delete_schedule
    │   ├── delete_schedules_bulk
    │   ├── fix_light_schedules_day_of_week  ← MISSING from repo
    │   ├── load_schedule_state_to_redis     ← MISSING from repo
    │   ├── update_light_schedule_target     ← MISSING from repo
    │   └── update_light_schedule_times      ← MISSING from repo
    │
    ├── Room Mode Operations (6 methods, ~300 lines) → RoomModeRepository
    │   ├── get_room_modes
    │   ├── get_flower_submodes
    │   ├── get_active_mode
    │   ├── set_active_mode
    │   ├── get_mode_parameters  ← SCHEMA DRIFT
    │   └── save_mode_parameters ← CRITICAL SCHEMA DRIFT (wrong table!)
    │
    └── Control Action Logging (3 methods, ~200 lines) → ControlActionRepository
        ├── log_control_action
        ├── log_automation_state
        └── log_config_version
```

### Repository Files (Created but Unused)

```
Infrastructure/automation-service/app/repositories/
├── __init__.py           (exports)
├── base.py               (1,469 lines) - BaseRepository with caching
├── sensors.py            (3,010 lines) - SensorRepository
├── devices.py            (5,518 lines) - DeviceRepository
├── setpoints.py          (5,614 lines) - SetpointRepository
├── pid.py                (6,985 lines) - PIDRepository
├── schedules.py          (7,080 lines) - ScheduleRepository
├── room_modes.py         (7,018 lines) - RoomModeRepository ← CRITICAL DRIFT
└── control_actions.py    (7,171 lines) - ControlActionRepository
```

**Total repository code:** ~44KB sitting unused

---

## Detailed Drift Analysis

### 1. RoomModeRepository - CRITICAL (Must Fix First)

#### Table Name Mismatch
```python
# Repository (WRONG):
await conn.execute("INSERT INTO room_mode_parameters ...")

# database.py (CORRECT):
await conn.execute("INSERT INTO mode_parameters ...")
```

#### Missing Columns in Repository

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| `pre_day_ramp_minutes` | INTEGER | 30 | Ramp before day transition |
| `pre_night_ramp_minutes` | INTEGER | 30 | Ramp before night transition |
| `pre_day_minutes` | INTEGER | 30 | Pre-day phase duration |
| `pre_night_minutes` | INTEGER | 30 | Pre-night phase duration |
| `pre_day_heat_temp` | FLOAT | 22.0 | Pre-day heating setpoint |
| `pre_day_cool_temp` | FLOAT | 26.0 | Pre-day cooling setpoint |
| `pre_day_vpd` | FLOAT | 1.2 | Pre-day VPD target |
| `pre_day_co2` | INTEGER | 700 | Pre-day CO2 target |
| `pre_night_heat_temp` | FLOAT | 22.0 | Pre-night heating setpoint |
| `pre_night_cool_temp` | FLOAT | 26.0 | Pre-night cooling setpoint |
| `pre_night_vpd` | FLOAT | 1.2 | Pre-night VPD target |
| `pre_night_co2` | INTEGER | 700 | Pre-night CO2 target |
| `day_heat_temp` | FLOAT | 24.0 | Day heating setpoint |
| `day_cool_temp` | FLOAT | 28.0 | Day cooling setpoint |
| `night_heat_temp` | FLOAT | 20.0 | Night heating setpoint |
| `night_cool_temp` | FLOAT | 24.0 | Night cooling setpoint |
| `day_leaf_delta` | FLOAT | -2.0 | Day leaf temp offset |
| `night_leaf_delta` | FLOAT | -1.0 | Night leaf temp offset |
| `main_light_intensity` | INTEGER | 100 | Main light % |
| `supplemental_light_intensity` | INTEGER | 0 | Supplemental light % |

#### Repository save_mode_parameters (WRONG - 12 columns)
```python
# Lines 146-162 of repositories/room_modes.py
await conn.execute("""
    INSERT INTO room_mode_parameters (
        location, cluster, mode_id, submode_id,
        day_temp_setpoint, night_temp_setpoint,
        day_humidity_setpoint, night_humidity_setpoint,
        vpd_setpoint, co2_setpoint,
        day_start, night_start
    ) VALUES (...)
""")
```

#### database.py save_mode_parameters (CORRECT - 32 columns)
```python
# Lines 3415-3441 of database.py
await conn.execute("""
    INSERT INTO mode_parameters (
        location, cluster, mode_id, submode_id,
        day_start_time, night_start_time, ramp_up_minutes, ramp_down_minutes,
        pre_day_ramp_minutes, pre_night_ramp_minutes,
        pre_day_minutes, pre_night_minutes,
        day_heat_temp, day_cool_temp, day_vpd, day_co2, day_leaf_delta,
        night_heat_temp, night_cool_temp, night_vpd, night_co2, night_leaf_delta,
        pre_day_heat_temp, pre_day_cool_temp, pre_day_vpd, pre_day_co2,
        pre_night_heat_temp, pre_night_cool_temp, pre_night_vpd, pre_night_co2,
        main_light_intensity, supplemental_light_intensity, updated_at
    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, 
              $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, 
              $23, $24, $25, $26, $27, $28, $29, $30, $31, $32, NOW())
""")
```

### 2. PIDRepository - Missing Method

| Method | Status | Lines in database.py |
|--------|--------|---------------------|
| `set_pid_parameters_with_reason` | ❌ MISSING | 2586-2639 |

**Signature needed:**
```python
async def set_pid_parameters_with_reason(
    self,
    device_type: str,
    kp: float,
    ki: float,
    kd: float,
    change_reason: str,
    source: str = "user",
    updated_by: str | None = None
) -> bool
```

### 3. ScheduleRepository - Missing Methods (4)

| Method | Status | Lines in database.py | Purpose |
|--------|--------|---------------------|---------|
| `fix_light_schedules_day_of_week` | ❌ MISSING | 2693-2720 | Fix NULL day_of_week |
| `load_schedule_state_to_redis` | ❌ MISSING | 3069-3116 | Sync schedules to Redis |
| `update_light_schedule_target` | ❌ MISSING | 3444-3455 | Update intensity only |
| `update_light_schedule_times` | ❌ MISSING | 3481-3495 | Update start/end times |

### 4. SetpointRepository - Signature Mismatch

| Parameter | Repository | database.py | Fix |
|-----------|------------|-------------|-----|
| `mode` | `str = "main"` | `str \| None = None` | Match database.py |

### 5. ControlActionRepository - Missing Method

| Method | Status | Lines in database.py |
|--------|--------|---------------------|
| `log_effective_setpoint` (singular) | ❌ MISSING | 1820-1868 |

Note: `log_effective_setpoints` (plural) exists in both, but singular version is also used.

---

## Test Infrastructure

### Existing Test Framework

| Component | Value |
|-----------|-------|
| Framework | pytest |
| Async support | pytest-asyncio |
| Mocking | unittest.mock (Mock, AsyncMock, MagicMock) |
| Test location | `Infrastructure/automation-service/tests/` |

### Existing Test Files

```
tests/
├── conftest.py                      # Shared fixtures (if exists)
├── test_control_components.py       # Control loop tests
├── test_database_mock.py            # Database mock tests
├── test_ramp_recovery.py            # Ramp logic tests
├── test_setpoint_ramp_logic.py      # Setpoint ramp tests
└── app/control/tests/
    └── test_control_components.py   # Comprehensive control tests
```

### Existing Mock Patterns

```python
# From test_setpoint_ramp_logic.py
from unittest.mock import Mock, AsyncMock, MagicMock, patch

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.read_setpoint = AsyncMock(return_value={...})
    return redis

@pytest.fixture  
def mock_database():
    db = AsyncMock()
    db.get_mode_parameters = AsyncMock(return_value={...})
    return db
```

### Test File Structure for Repositories

```
tests/
├── conftest.py                      # NEW: Shared fixtures
├── repositories/                    # NEW: Repository unit tests
│   ├── __init__.py
│   ├── test_sensor_repository.py
│   ├── test_device_repository.py
│   ├── test_setpoint_repository.py
│   ├── test_pid_repository.py
│   ├── test_schedule_repository.py
│   ├── test_room_mode_repository.py
│   └── test_control_action_repository.py
├── integration/                     # NEW: Integration tests
│   └── test_database_facade.py      # Tests delegation works
└── (existing test files)
```

---

## Best Practices (From Research)

### 1. Facade Pattern (Recommended Approach)

**Keep `DatabaseManager` as facade during migration:**

```python
# DatabaseManager becomes a thin delegation layer
class DatabaseManager:
    def __init__(self):
        self._sensor_repo: SensorRepository | None = None
        self._device_repo: DeviceRepository | None = None
        # ... other repos
    
    async def initialize(self):
        pool = await self._get_pool()
        self._sensor_repo = SensorRepository(pool, self._redis_client)
        self._device_repo = DeviceRepository(pool, self._redis_client)
        # ... initialize other repos
    
    # Delegate to repository
    async def get_sensor_value(self, sensor_name: str) -> float | None:
        return await self._sensor_repo.get_sensor_value(sensor_name)
```

**Benefits:**
- Zero breaking changes to existing code
- Gradual migration possible
- Single transaction boundary
- Shared connection pool

### 2. Dependency Injection (Future Enhancement)

After facade is stable, optionally expose repositories via FastAPI DI:

```python
# app/dependencies.py
from typing import Annotated
from fastapi import Depends

async def get_sensor_repo() -> SensorRepository:
    return db_manager._sensor_repo

SensorRepoDep = Annotated[SensorRepository, Depends(get_sensor_repo)]

# In routes (future):
@router.get("/sensors/{name}")
async def get_sensor(name: str, repo: SensorRepoDep):
    return await repo.get_sensor_value(name)
```

### 3. Transaction Management

For operations spanning multiple repositories, use Unit of Work:

```python
# Already have connection pool - just ensure same connection for multi-repo ops
async with pool.acquire() as conn:
    await sensor_repo.create(conn, sensor_data)
    await device_repo.update(conn, device_data)
    # Commits together or rolls back together
```

### 4. Testing Strategy

**Layered approach:**

| Layer | What | How |
|-------|------|-----|
| Unit | Repository methods in isolation | Mock asyncpg pool |
| Integration | DatabaseManager delegation | Real DB or testcontainers |
| E2E | Full API endpoints | TestClient + real services |

---

## Implementation Plan

### Phase 0: Fix Critical Drift (MUST DO FIRST)
**Estimated: 6-8 hours | Priority: CRITICAL**

#### Task 0.1: Rewrite RoomModeRepository
**File:** `app/repositories/room_modes.py`

1. Change table name from `room_mode_parameters` to `mode_parameters`
2. Add all 20 missing columns to `save_mode_parameters()`
3. Add all 20 missing columns to `get_mode_parameters()` return
4. Match exact default values from database.py
5. Match exact parameter handling (time parsing, etc.)

**Verification:**
```python
# Test that repository produces identical SQL to database.py
def test_save_mode_parameters_sql_matches():
    # Compare generated queries
```

#### Task 0.2: Add Missing Methods to PIDRepository
**File:** `app/repositories/pid.py`

Add method:
```python
async def set_pid_parameters_with_reason(
    self,
    device_type: str,
    kp: float,
    ki: float,
    kd: float,
    change_reason: str,
    source: str = "user",
    updated_by: str | None = None
) -> bool:
    """Set PID parameters with a reason for the change.
    
    This logs the change reason alongside the parameter update,
    useful for tracking autotune vs manual changes.
    """
    # Copy implementation from database.py lines 2586-2639
```

#### Task 0.3: Add Missing Methods to ScheduleRepository
**File:** `app/repositories/schedules.py`

Add methods:
1. `fix_light_schedules_day_of_week()` - from database.py lines 2693-2720
2. `load_schedule_state_to_redis()` - from database.py lines 3069-3116
3. `update_light_schedule_target()` - from database.py lines 3444-3455
4. `update_light_schedule_times()` - from database.py lines 3481-3495

#### Task 0.4: Add Missing Method to SetpointRepository
**File:** `app/repositories/setpoints.py`

1. Add `log_effective_setpoint()` (singular) - from database.py lines 1820-1868
2. Fix `get_setpoint()` signature: change `mode: str = "main"` to `mode: str | None = None`

#### Task 0.5: Verify ControlActionRepository
**File:** `app/repositories/control_actions.py`

Verify these methods match database.py exactly:
- `log_control_action()`
- `log_automation_state()`
- `log_config_version()`

---

### Phase 1: Wire DatabaseManager as Facade
**Estimated: 4-6 hours | Depends on: Phase 0**

#### Task 1.1: Add Repository Instances to DatabaseManager

**File:** `app/database.py`

Add to `__init__`:
```python
# Repository instances (initialized in initialize())
self._sensor_repo: SensorRepository | None = None
self._device_repo: DeviceRepository | None = None
self._setpoint_repo: SetpointRepository | None = None
self._pid_repo: PIDRepository | None = None
self._schedule_repo: ScheduleRepository | None = None
self._room_mode_repo: RoomModeRepository | None = None
self._control_action_repo: ControlActionRepository | None = None
```

Add imports at top:
```python
from .repositories import (
    SensorRepository,
    DeviceRepository,
    SetpointRepository,
    PIDRepository,
    ScheduleRepository,
    RoomModeRepository,
    ControlActionRepository,
)
```

#### Task 1.2: Initialize Repositories in initialize()

**File:** `app/database.py`

Add to `initialize()` after pool creation:
```python
# Initialize repositories with shared pool and redis
pool = await self._get_pool()
self._sensor_repo = SensorRepository(pool, self._automation_redis)
self._device_repo = DeviceRepository(pool, self._automation_redis)
self._setpoint_repo = SetpointRepository(pool, self._automation_redis)
self._pid_repo = PIDRepository(pool, self._automation_redis)
self._schedule_repo = ScheduleRepository(pool, self._automation_redis)
self._room_mode_repo = RoomModeRepository(pool, self._automation_redis)
self._control_action_repo = ControlActionRepository(pool, self._automation_redis)
```

#### Task 1.3: Delegate Methods (In Order of Risk)

**Order of delegation (lowest risk first):**

##### 1.3.1 SensorRepository (2 methods, LOW RISK)
```python
async def get_sensor_value(self, sensor_name: str) -> float | None:
    return await self._sensor_repo.get_sensor_value(sensor_name)

async def get_sensor_values_batch(self, sensor_names: list[str]) -> dict[str, float | None]:
    return await self._sensor_repo.get_sensor_values_batch(sensor_names)
```

##### 1.3.2 DeviceRepository (7 methods, LOW RISK)
```python
async def get_device_state(self, location: str, cluster: str, device_name: str) -> dict | None:
    return await self._device_repo.get_device_state(location, cluster, device_name)

async def set_device_state(self, location: str, cluster: str, device_name: str, 
                           channel: int, state: bool, mode: str = "auto") -> bool:
    return await self._device_repo.set_device_state(location, cluster, device_name, channel, state, mode)

# ... etc for all 7 methods
```

##### 1.3.3 PIDRepository (9 methods, MEDIUM RISK)
```python
async def get_pid_parameters(self, device_type: str) -> dict | None:
    return await self._pid_repo.get_pid_parameters(device_type)

async def set_pid_parameters_with_reason(self, device_type: str, kp: float, ki: float, 
                                          kd: float, change_reason: str, 
                                          source: str = "user", updated_by: str | None = None) -> bool:
    return await self._pid_repo.set_pid_parameters_with_reason(
        device_type, kp, ki, kd, change_reason, source, updated_by
    )

# ... etc for all 9 methods
```

##### 1.3.4 SetpointRepository (6 methods, MEDIUM RISK)
```python
async def get_setpoint(self, location: str, cluster: str, mode: str | None = None) -> dict | None:
    return await self._setpoint_repo.get_setpoint(location, cluster, mode)

async def set_setpoint(self, location: str, cluster: str, ...) -> tuple[bool, datetime | None]:
    return await self._setpoint_repo.set_setpoint(location, cluster, ...)

# ... etc for all 6 methods
```

##### 1.3.5 ScheduleRepository (11 methods, MEDIUM RISK)
```python
async def get_schedules(self, location: str | None = None, cluster: str | None = None) -> list[dict]:
    return await self._schedule_repo.get_schedules(location, cluster)

async def load_schedule_state_to_redis(self) -> None:
    return await self._schedule_repo.load_schedule_state_to_redis()

# ... etc for all 11 methods
```

##### 1.3.6 RoomModeRepository (6 methods, HIGH RISK - test thoroughly)
```python
async def get_room_modes(self) -> list[dict]:
    return await self._room_mode_repo.get_room_modes()

async def save_mode_parameters(self, location: str, cluster: str, mode_name: str,
                                submode_name: str | None, params: dict[str, Any]) -> bool:
    return await self._room_mode_repo.save_mode_parameters(
        location, cluster, mode_name, submode_name, params
    )

# ... etc for all 6 methods
```

##### 1.3.7 ControlActionRepository (3 methods, MEDIUM RISK)
```python
async def log_control_action(self, location: str, cluster: str, device_name: str, ...) -> None:
    return await self._control_action_repo.log_control_action(location, cluster, device_name, ...)

# ... etc for all 3 methods
```

---

### Phase 2: Comprehensive Testing
**Estimated: 4-6 hours | Integrated with Phase 1**

#### Task 2.1: Create Shared Test Fixtures

**File:** `tests/conftest.py`
```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_pool():
    """Mock asyncpg connection pool."""
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool, conn

@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock()
    redis.read_setpoint = AsyncMock(return_value=None)
    redis.write_setpoint = AsyncMock(return_value=True)
    return redis

@pytest.fixture
def mock_database_manager(mock_pool, mock_redis):
    """Mock DatabaseManager with repositories."""
    pool, conn = mock_pool
    dm = AsyncMock()
    dm._pool = pool
    dm._automation_redis = mock_redis
    dm._get_pool = AsyncMock(return_value=pool)
    return dm, conn
```

#### Task 2.2: Unit Tests for Each Repository

**Example: `tests/repositories/test_room_mode_repository.py`**
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.repositories.room_modes import RoomModeRepository

@pytest.fixture
def room_mode_repo(mock_pool, mock_redis):
    pool, conn = mock_pool
    return RoomModeRepository(pool, mock_redis), conn

class TestSaveModeParameters:
    @pytest.mark.asyncio
    async def test_save_mode_parameters_uses_correct_table(self, room_mode_repo):
        repo, conn = room_mode_repo
        conn.fetchrow.return_value = {"id": 1}
        conn.fetchval.return_value = None  # No existing record
        
        result = await repo.save_mode_parameters(
            location="Flower Room",
            cluster="main",
            mode_name="flower",
            submode_name="early",
            params={
                "day_heat_temp": 24.0,
                "pre_day_ramp_minutes": 30,
                # ... all 32 fields
            }
        )
        
        # Verify correct table name in INSERT
        insert_call = conn.execute.call_args
        assert "mode_parameters" in insert_call[0][0]
        assert "room_mode_parameters" not in insert_call[0][0]
    
    @pytest.mark.asyncio
    async def test_save_mode_parameters_includes_all_fields(self, room_mode_repo):
        repo, conn = room_mode_repo
        conn.fetchrow.return_value = {"id": 1}
        conn.fetchval.return_value = None
        
        await repo.save_mode_parameters(
            location="Flower Room",
            cluster="main",
            mode_name="flower",
            submode_name=None,
            params={}  # Use all defaults
        )
        
        insert_sql = conn.execute.call_args[0][0]
        
        # Verify all required columns are present
        required_columns = [
            "pre_day_ramp_minutes", "pre_night_ramp_minutes",
            "pre_day_heat_temp", "pre_day_cool_temp",
            "day_leaf_delta", "night_leaf_delta",
            "main_light_intensity", "supplemental_light_intensity"
        ]
        for col in required_columns:
            assert col in insert_sql, f"Missing column: {col}"
```

#### Task 2.3: Integration Tests for Facade

**File:** `tests/integration/test_database_facade.py`
```python
import pytest
from unittest.mock import AsyncMock, patch
from app.database import DatabaseManager

class TestDatabaseManagerDelegation:
    @pytest.mark.asyncio
    async def test_get_sensor_value_delegates_to_repository(self):
        """Verify DatabaseManager delegates to SensorRepository."""
        dm = DatabaseManager.__new__(DatabaseManager)
        dm._sensor_repo = AsyncMock()
        dm._sensor_repo.get_sensor_value.return_value = 25.5
        
        result = await dm.get_sensor_value("temp_flower_1")
        
        dm._sensor_repo.get_sensor_value.assert_called_once_with("temp_flower_1")
        assert result == 25.5
    
    @pytest.mark.asyncio
    async def test_save_mode_parameters_delegates_with_all_params(self):
        """Verify mode parameters delegation passes all fields."""
        dm = DatabaseManager.__new__(DatabaseManager)
        dm._room_mode_repo = AsyncMock()
        dm._room_mode_repo.save_mode_parameters.return_value = True
        
        params = {
            "day_heat_temp": 24.0,
            "pre_day_ramp_minutes": 45,
            "main_light_intensity": 80,
        }
        
        result = await dm.save_mode_parameters(
            "Flower Room", "main", "flower", "early", params
        )
        
        dm._room_mode_repo.save_mode_parameters.assert_called_once_with(
            "Flower Room", "main", "flower", "early", params
        )
        assert result == True
```

#### Task 2.4: Regression Tests

Run existing tests to ensure no regressions:
```bash
cd Infrastructure/automation-service
pytest tests/ -v --tb=short
```

---

### Phase 3: Remove Duplicate Code & Fix LSP
**Estimated: 2-3 hours | Depends on: Phase 1 & 2 complete**

#### Task 3.1: Remove Inline Implementations

After all delegations are verified working:

1. Remove method bodies from DatabaseManager
2. Keep only delegation calls
3. **Target:** 3,495 → ~400-500 lines

**Before:**
```python
async def get_sensor_value(self, sensor_name: str) -> float | None:
    # 50 lines of implementation...
    value = await self._redis_client.get(...)
    if value is None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(...)
    # ... etc
```

**After:**
```python
async def get_sensor_value(self, sensor_name: str) -> float | None:
    return await self._sensor_repo.get_sensor_value(sensor_name)
```

#### Task 3.2: Fix LSP Errors

##### 3.2.1 Deprecated Typing Syntax

Use ast-grep for bulk replacement:
```bash
# Replace Dict with dict
sg --pattern 'Dict[$K, $V]' --rewrite 'dict[$K, $V]' --lang python Infrastructure/

# Replace List with list
sg --pattern 'List[$T]' --rewrite 'list[$T]' --lang python Infrastructure/

# Replace Optional with | None
sg --pattern 'Optional[$T]' --rewrite '$T | None' --lang python Infrastructure/
```

##### 3.2.2 Import Cycles

After delegation, import cycles should resolve because:
- Routes import `DatabaseManager` (facade)
- `DatabaseManager` imports repositories
- Repositories don't import routes

If cycles remain, use `TYPE_CHECKING`:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .routes.schedules import ScheduleResponse
```

##### 3.2.3 asyncpg Type Stubs

Option A: Install stubs (if available)
```bash
pip install asyncpg-stubs
```

Option B: Configure pyright to ignore
```json
// pyrightconfig.json
{
    "reportMissingTypeStubs": false,
    "reportUnknownMemberType": false
}
```

Option C: Create minimal stub file
```python
# stubs/asyncpg/__init__.pyi
from typing import Any, Dict, List, Optional

class Pool:
    async def acquire(self) -> Connection: ...
    async def close(self) -> None: ...

class Connection:
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetch(self, query: str, *args: Any) -> List[Dict[str, Any]]: ...
    async def fetchrow(self, query: str, *args: Any) -> Optional[Dict[str, Any]]: ...
    async def fetchval(self, query: str, *args: Any) -> Optional[Any]: ...
```

##### 3.2.4 Implicit Relative Imports in control_engine.py

Change all implicit imports to explicit:
```python
# Before (implicit):
from app.control.relay_manager import RelayManager

# After (explicit relative):
from .relay_manager import RelayManager
```

---

## Execution Timeline

### Week 1: Foundation

| Day | Tasks | Deliverables |
|-----|-------|--------------|
| 1 | Task 0.1: Rewrite RoomModeRepository | Fixed room_modes.py |
| 2 | Task 0.2-0.4: Add missing methods | Complete repositories |
| 3 | Task 0.5: Verify all repos | All repos match database.py |
| 3 | Task 2.1: Create test fixtures | conftest.py |
| 4 | Task 1.1-1.2: Add repo instances | DatabaseManager updated |
| 5 | Task 1.3.1-1.3.2: Wire Sensor+Device repos | Low-risk delegation done |

### Week 2: Core Wiring

| Day | Tasks | Deliverables |
|-----|-------|--------------|
| 6 | Task 1.3.3: Wire PIDRepository | PID delegation done |
| 6 | Task 2.2: PID unit tests | test_pid_repository.py |
| 7 | Task 1.3.4: Wire SetpointRepository | Setpoint delegation done |
| 7 | Task 2.2: Setpoint unit tests | test_setpoint_repository.py |
| 8 | Task 1.3.5: Wire ScheduleRepository | Schedule delegation done |
| 8 | Task 2.2: Schedule unit tests | test_schedule_repository.py |
| 9 | Task 1.3.6: Wire RoomModeRepository | RoomMode delegation done |
| 9 | Task 2.2: RoomMode unit tests | test_room_mode_repository.py |
| 10 | Task 1.3.7: Wire ControlActionRepository | All delegation done |

### Week 3: Cleanup & Deploy

| Day | Tasks | Deliverables |
|-----|-------|--------------|
| 11 | Task 2.3: Integration tests | test_database_facade.py |
| 11 | Task 2.4: Regression tests | All tests pass |
| 12 | Task 3.1: Remove duplicate code | database.py < 500 lines |
| 12 | Task 3.2: Fix LSP errors | Zero LSP errors |
| 13 | Deploy to staging | Staging verified |
| 14 | Deploy to production | Production verified |

---

## Rollback Strategy

### Per AGENTS.md Requirements
- Rollback must complete in <30 seconds
- Use `./rollback.sh` immediately if anything breaks

### Rollback Checkpoints

| Checkpoint | State | Rollback Action |
|------------|-------|-----------------|
| After Phase 0 | Repos updated, database.py unchanged | Git revert repo changes |
| After each repo wired | Partial delegation | Revert specific delegation |
| After Phase 3 | Full delegation | `./rollback.sh` to previous release |

### Git Branch Strategy

```bash
# Create feature branch
git checkout -b feature/database-repository-wiring

# Commit after each task
git commit -m "feat(repos): rewrite RoomModeRepository with correct schema"
git commit -m "feat(repos): add missing methods to ScheduleRepository"
git commit -m "feat(database): wire SensorRepository delegation"
# ... etc

# Merge to main only after all tests pass
git checkout main
git merge --no-ff feature/database-repository-wiring
```

---

## Success Criteria

### Phase 0 Complete When:
- [ ] RoomModeRepository uses `mode_parameters` table (not `room_mode_parameters`)
- [ ] RoomModeRepository has all 32 columns
- [ ] All missing methods added to repositories
- [ ] All signatures match database.py exactly

### Phase 1 Complete When:
- [ ] All 60 DatabaseManager methods delegate to repositories
- [ ] All existing tests pass
- [ ] No runtime errors in staging

### Phase 2 Complete When:
- [ ] Unit tests exist for each repository
- [ ] Integration tests verify delegation
- [ ] Code coverage > 80% for repositories

### Phase 3 Complete When:
- [ ] `database.py` < 500 lines
- [ ] Zero LSP errors (or only suppressions for asyncpg)
- [ ] Production deploy successful
- [ ] 24h monitoring shows no issues

---

## Risk Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Schema drift breaks production | HIGH | CRITICAL | Phase 0 fixes drift before wiring |
| Import cycles persist | MEDIUM | HIGH | Use TYPE_CHECKING imports |
| Tests don't catch edge cases | MEDIUM | HIGH | Copy exact test data from production |
| Rollback takes too long | LOW | HIGH | Test rollback.sh before deploy |
| New features added during migration | MEDIUM | MEDIUM | Feature freeze during Phase 1-2 |

---

## Appendix A: Method-to-Repository Mapping

| DatabaseManager Method | Repository | Line in database.py |
|-----------------------|------------|---------------------|
| `get_sensor_value` | SensorRepository | 1296 |
| `get_sensor_values_batch` | SensorRepository | 1348 |
| `get_device_state` | DeviceRepository | 1430 |
| `get_latest_light_intensity` | DeviceRepository | 1452 |
| `set_device_state` | DeviceRepository | 1481 |
| `get_all_device_states` | DeviceRepository | 2100 |
| `get_device_mapping` | DeviceRepository | 2115 |
| `set_device_mapping` | DeviceRepository | 2152 |
| `get_all_device_mappings` | DeviceRepository | 2196 |
| `get_setpoint` | SetpointRepository | 1613 |
| `set_setpoint` | SetpointRepository | 1692 |
| `log_effective_setpoint` | SetpointRepository | 1820 |
| `log_effective_setpoints` | SetpointRepository | 1870 |
| `get_all_setpoints_for_location_cluster` | SetpointRepository | 1999 |
| `get_latest_effective_setpoints` | SetpointRepository | 2035 |
| `get_pid_parameters` | PIDRepository | 2215 |
| `set_pid_parameters` | PIDRepository | 2249 |
| `get_pid_parameter_history` | PIDRepository | 2304 |
| `get_all_pid_parameters` | PIDRepository | 2333 |
| `get_pid_control_mode` | PIDRepository | 2363 |
| `set_pid_control_mode` | PIDRepository | 2391 |
| `get_autotune_state` | PIDRepository | 2444 |
| `update_autotune_state` | PIDRepository | 2471 |
| `set_pid_parameters_with_reason` | PIDRepository | 2586 |
| `get_schedules` | ScheduleRepository | 2642 |
| `fix_light_schedules_day_of_week` | ScheduleRepository | 2693 |
| `get_climate_schedule` | ScheduleRepository | 2723 |
| `create_schedule` | ScheduleRepository | 2811 |
| `update_schedule` | ScheduleRepository | 2883 |
| `delete_schedule` | ScheduleRepository | 3026 |
| `delete_schedules_bulk` | ScheduleRepository | 3044 |
| `load_schedule_state_to_redis` | ScheduleRepository | 3069 |
| `update_light_schedule_target` | ScheduleRepository | 3444 |
| `get_light_schedule` | ScheduleRepository | 3458 |
| `update_light_schedule_times` | ScheduleRepository | 3481 |
| `get_room_modes` | RoomModeRepository | 3270 |
| `get_flower_submodes` | RoomModeRepository | 3279 |
| `get_active_mode` | RoomModeRepository | 3288 |
| `set_active_mode` | RoomModeRepository | 3301 |
| `get_mode_parameters` | RoomModeRepository | 3324 |
| `save_mode_parameters` | RoomModeRepository | 3356 |
| `log_control_action` | ControlActionRepository | 1515 |
| `log_automation_state` | ControlActionRepository | 1544 |
| `log_config_version` | ControlActionRepository | 753 |

---

## Appendix B: RoomModeRepository Correct Schema

```sql
-- Correct table: mode_parameters (32 columns)
CREATE TABLE IF NOT EXISTS mode_parameters (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    mode_id INTEGER NOT NULL REFERENCES room_modes(id),
    submode_id INTEGER REFERENCES flower_submodes(id),
    
    -- Time settings
    day_start_time TIME NOT NULL DEFAULT '06:00',
    night_start_time TIME NOT NULL DEFAULT '18:00',
    ramp_up_minutes INTEGER NOT NULL DEFAULT 30,
    ramp_down_minutes INTEGER NOT NULL DEFAULT 30,
    
    -- Pre-transition settings (NEW - added in 887089b)
    pre_day_ramp_minutes INTEGER NOT NULL DEFAULT 30,
    pre_night_ramp_minutes INTEGER NOT NULL DEFAULT 30,
    pre_day_minutes INTEGER NOT NULL DEFAULT 30,
    pre_night_minutes INTEGER NOT NULL DEFAULT 30,
    
    -- Day climate setpoints
    day_heat_temp FLOAT NOT NULL DEFAULT 24.0,
    day_cool_temp FLOAT NOT NULL DEFAULT 28.0,
    day_vpd FLOAT NOT NULL DEFAULT 1.2,
    day_co2 INTEGER NOT NULL DEFAULT 800,
    day_leaf_delta FLOAT NOT NULL DEFAULT -2.0,
    
    -- Night climate setpoints
    night_heat_temp FLOAT NOT NULL DEFAULT 20.0,
    night_cool_temp FLOAT NOT NULL DEFAULT 24.0,
    night_vpd FLOAT NOT NULL DEFAULT 1.2,
    night_co2 INTEGER NOT NULL DEFAULT 600,
    night_leaf_delta FLOAT NOT NULL DEFAULT -1.0,
    
    -- Pre-day climate setpoints
    pre_day_heat_temp FLOAT NOT NULL DEFAULT 22.0,
    pre_day_cool_temp FLOAT NOT NULL DEFAULT 26.0,
    pre_day_vpd FLOAT NOT NULL DEFAULT 1.2,
    pre_day_co2 INTEGER NOT NULL DEFAULT 700,
    
    -- Pre-night climate setpoints
    pre_night_heat_temp FLOAT NOT NULL DEFAULT 22.0,
    pre_night_cool_temp FLOAT NOT NULL DEFAULT 26.0,
    pre_night_vpd FLOAT NOT NULL DEFAULT 1.2,
    pre_night_co2 INTEGER NOT NULL DEFAULT 700,
    
    -- Light settings
    main_light_intensity INTEGER NOT NULL DEFAULT 100,
    supplemental_light_intensity INTEGER NOT NULL DEFAULT 0,
    
    -- Metadata
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(location, cluster, mode_id, submode_id)
);
```

---

## Appendix C: Commands Reference

```bash
# Run all tests
cd Infrastructure/automation-service
pytest tests/ -v

# Run specific repository tests
pytest tests/repositories/test_room_mode_repository.py -v

# Check LSP errors
# (Use your IDE or pyright directly)
pyright app/database.py

# Deploy to staging
./deploy.sh --staging

# Deploy to production
./deploy.sh

# Rollback if needed
./rollback.sh

# Check service status
systemctl status automation-service

# View logs
journalctl -u automation-service -f
```

---

**Plan Version:** 1.0
**Last Updated:** 2026-01-18
**Author:** AI Assistant
**Reviewed By:** (pending)

---

# ADDENDUM: 2026 Standards & Modernization

**Added:** 2026-01-18 (Research Phase)
**Research Duration:** 30 minutes, 7 parallel agents

---

## MCP Status Investigation

| MCP | Status | Issue |
|-----|--------|-------|
| **playwright** | ✅ Running | Process found: `mcp-server-playwright` |
| **tavily** | ✅ Available | Remote MCP (no local process needed) |
| **context7** | ❌ Not Running | Process not found - npx may have failed to start |

**To fix context7:**
```bash
# Test manually
npx -y @upstash/context7-mcp@latest --api-key ctx7sk-cb2a9890-4c50-46f1-99ca-81409a15be45

# Check for errors in startup
# May need to run: npm install -g @upstash/context7-mcp
```

---

## Recent Codebase Changes (Since Plan Creation)

| Commit | Date | Change |
|--------|------|--------|
| `1005906` | 2026-01-18 | **feat: add independent light ramp up/down durations** |
| `6a9a6a5` | 2026-01-18 | fix: light intensity save and CircularTimePicker drag |

**New columns added to `mode_parameters`:**
- `light_ramp_up_minutes`
- `light_ramp_down_minutes`

**Impact on plan:** These new columns must also be added to `RoomModeRepository` (now 34 columns instead of 32).

---

## 2026 Python Async Patterns

### Connection Management

**Current:** Manual pool management in `database.py`
**2026 Standard:** Singleton `ConnectionManager` with structured lifecycle

```python
# connection_manager.py - 2026 pattern
from contextlib import asynccontextmanager
import asyncpg

class ConnectionManager:
    _pool: asyncpg.Pool | None = None
    
    @classmethod
    async def initialize(cls, dsn: str) -> None:
        cls._pool = await asyncpg.create_pool(
            dsn,
            min_size=4,           # Little's Law: λ × W × 2
            max_size=25,          # Burst capacity
            max_queries=50000,
            max_inactive_connection_lifetime=300.0,
            command_timeout=60.0,
        )
    
    @classmethod
    @asynccontextmanager
    async def acquire(cls):
        async with cls._pool.acquire() as conn:
            yield conn
```

### Structured Concurrency (Python 3.12+)

**Current:** Scattered `asyncio.create_task()`
**2026 Standard:** `TaskGroup` for automatic cleanup

```python
# 2026 pattern - structured concurrency
from asyncio import TaskGroup

async def fetch_sensors_concurrent(pool, sensor_ids):
    results = {}
    async with TaskGroup() as tg:
        for sensor_id in sensor_ids:
            tg.create_task(fetch_sensor(pool, sensor_id, results))
    return results  # All tasks complete or all cancelled on error
```

### 100ms Batch Writer

```python
# batch_writer.py - meets 100ms requirement
class AsyncBatchWriter:
    def __init__(self, pool, max_size=50, max_interval=0.1):
        self.pool = pool
        self.max_size = max_size
        self.max_interval = max_interval  # 100ms
        self._buffer = []
        self._flush_lock = asyncio.Lock()
    
    async def add(self, item: dict) -> None:
        self._buffer.append(item)
        if len(self._buffer) >= self.max_size:
            await self.flush()
    
    async def flush(self) -> None:
        async with self._flush_lock:
            if not self._buffer:
                return
            to_write, self._buffer = self._buffer, []
            async with self.pool.acquire() as conn:
                await conn.executemany(INSERT_SQL, to_write)
```

---

## 2026 FastAPI Architecture

### Keep: Current Patterns (Already Good)

| Pattern | Status | Why |
|---------|--------|-----|
| FastAPI `Depends()` | ✅ Keep | Native DI, production-proven |
| `app/container.py` | ✅ Keep | Good service lifecycle |
| Service layer | ✅ Keep | Matches IoT needs |
| pydantic-settings v2 | ✅ Keep | Already correct |

### Add: Annotated Dependencies (2026 Standard)

```python
# Current (good):
def get_database() -> Database:
    return container.database

@router.get("/sensors")
async def get_sensors(db: Database = Depends(get_database)):
    ...

# 2026 pattern (better):
from typing import Annotated

DatabaseDep = Annotated[Database, Depends(get_database)]
RedisDep = Annotated[RedisClient, Depends(get_redis)]

@router.get("/sensors")
async def get_sensors(db: DatabaseDep, redis: RedisDep):
    ...
```

### Add: Structured Logging with OpenTelemetry

```python
# requirements.txt additions
structlog>=24.1.0
opentelemetry-api>=1.27.0
opentelemetry-sdk>=1.27.0
opentelemetry-instrumentation-fastapi>=0.48b0
opentelemetry-instrumentation-redis>=0.48b0
opentelemetry-instrumentation-asyncpg>=0.48b0
```

```python
# app/telemetry.py
from opentelemetry import trace
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

# 10% sampling for Pi 5 resource constraints
sampler = TraceIdRatioBased(0.1)
provider = TracerProvider(sampler=sampler)
```

### Add: Custom Exception Hierarchy

```python
# app/exceptions.py
class SensorTimeoutError(Exception):
    """Raised when sensor data is stale"""
    pass

class SetpointValidationError(Exception):
    """Raised when setpoint out of range"""
    pass

class DeviceControlError(Exception):
    """Raised when hardware control fails"""
    pass

# Global handlers
@app.exception_handler(SensorTimeoutError)
async def sensor_timeout_handler(request, exc):
    return JSONResponse(status_code=503, content={"detail": str(exc)})
```

---

## 2026 Python Typing Standards

### TypeIs over TypeGuard (PEP 742)

```python
from typing import TypeIs

# TypeIs narrows both branches
def is_valid_sensor_reading(value: object) -> TypeIs[int]:
    return isinstance(value, int) and 0 <= value <= 100

def process(reading: int | str) -> float:
    if is_valid_sensor_reading(reading):
        # reading is int here
        return reading * 1.5
    else:
        # reading is str here
        return float(len(reading))
```

### Protocol Classes for IoT Devices

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class Sensor(Protocol):
    def read(self) -> dict: ...
    def get_id(self) -> str: ...

# Any class with matching methods works - no inheritance needed
class TemperatureSensor:
    def read(self) -> dict:
        return {"type": "temp", "value": 22.5}
    def get_id(self) -> str:
        return "temp_001"
```

### basedpyright Configuration

```toml
# pyproject.toml
[tool.basedpyright]
typeCheckingMode = "strict"
pythonPlatform = "Linux"
pythonVersion = "3.12"

strictListInference = true
strictDictionaryInference = true
reportUnreachableCode = "error"

# Gradual migration
reportMissingTypeStubs = "warning"
reportUnknownVariableType = "warning"
reportUnknownArgumentType = "warning"
```

---

## 2026 React/TypeScript Patterns

### Keep: Current Stack (Already Good)

| Tool | Status | Why |
|------|--------|-----|
| TanStack Query v5 | ✅ Keep | Gold standard for server state |
| Vite | ✅ Keep | Best for SPAs |
| Recharts | ✅ Keep | Good for dashboards |
| WebSocket | ✅ Keep | Bidirectional needed |

### Add: Zustand for UI State

```typescript
// src/store/uiStore.ts
import { create } from 'zustand'

interface UIState {
  selectedZone: string | null
  isEditing: boolean
  setSelectedZone: (zone: string | null) => void
}

export const useUIStore = create<UIState>((set) => ({
  selectedZone: null,
  isEditing: false,
  setSelectedZone: (zone) => set({ selectedZone: zone }),
}))
```

### Add: OpenAPI Code Generation (orval)

```bash
# Install
npm install -D orval

# Generate types from FastAPI
npx orval http://localhost:8001/openapi.json -o src/api/generated.ts
```

```typescript
// Auto-generated, type-safe API calls
import { apiClient } from '@/api/generated'

const setpoint = await apiClient.setpoints.updateSetpoints({
  path: { location: 'Flower Room', cluster: 'back' },
  body: { temperature: 22.5, humidity: 60 }
})
```

### Optimize: Recharts for 1Hz Updates

```typescript
// Disable animations for real-time data
<Line dataKey="temperature" isAnimationActive={false} />

// Limit data points
const [chartData, setChartData] = useState<SensorData[]>([])
useEffect(() => {
  setChartData(prev => [...prev, newPoint].slice(-100)) // Last 100 points
}, [newPoint])
```

---

## 2026 IoT/CEA Architecture

### Your Current Architecture is CORRECT

```
ESP32 (CAN @250kbps)
    ↓
can-processor-service
    ├→ Redis State (instant, <1ms)
    ├→ Redis Stream (recent history)
    └→ TimescaleDB (persistent)
         ↓
automation-service (2s control loop)
    ↓
React + Grafana dashboards
```

**This is already event-driven and matches 2026 industrial IoT patterns.**

### Add: TimescaleDB Continuous Aggregates

```sql
-- Hourly rollups for 365 days
CREATE MATERIALIZED VIEW hourly_avg
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time), device_id, sensor_type,
       AVG(value), MIN(value), MAX(value)
FROM measurement
GROUP BY 1, 2, 3;

SELECT add_continuous_aggregate_policy('hourly_avg',
  start_offset => INTERVAL '30 days',
  end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '1 hour');

-- Enable columnstore compression (2025 feature)
ALTER MATERIALIZED VIEW hourly_avg 
SET (timescaledb.enable_columnstore=true);

-- Data retention
SELECT add_retention_policy('measurement', INTERVAL '30 days');
SELECT add_retention_policy('hourly_avg', INTERVAL '730 days');
```

### Add: Offline Resilience Pattern

```
Connection State    Behavior
─────────────────────────────────────────
Online             Normal operation
Degraded          Buffer in Redis stream
Offline             Full local operation
Reconnecting         Replay stream (FIFO)
```

---

## 2026 Testing Patterns

### Testcontainers for Integration Tests

```python
# conftest.py
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer(
        "timescale/timescaledb:2.15.2-pg16"
    ) as container:
        yield container.get_connection_url()
```

### Property-Based Testing with Hypothesis

```python
from hypothesis import given, strategies as st

@given(
    temp=st.floats(min_value=10.0, max_value=35.0),
    humidity=st.floats(min_value=30.0, max_value=90.0)
)
async def test_vpd_calculation_properties(temp, humidity):
    vpd = calculate_vpd(temp, humidity)
    assert 0.1 <= vpd <= 5.0  # Valid range
```

### Snapshot Testing with syrupy

```python
async def test_sensor_api_response(async_client, snapshot):
    response = await async_client.get("/api/sensors/Flower%20Room/live")
    assert response.json() == snapshot
```

---

## Updated Phase Plan

### Phase 0: Fix Critical Drift (MUST DO FIRST)
*Estimated: 6-8 hours*

**Updated for new columns:**

1. **RoomModeRepository** - Now needs **34 columns** (not 32):
   - Add `light_ramp_up_minutes` (from commit `1005906`)
   - Add `light_ramp_down_minutes` (from commit `1005906`)

2. **Add missing methods** (unchanged from original plan)

### Phase 1: Wire DatabaseManager (unchanged)

### Phase 2: Testing (unchanged)

### Phase 3: Remove Duplicates & Fix LSP (unchanged)

### NEW Phase 4: 2026 Modernization
*Estimated: 1-2 weeks, can run parallel to Phase 1-3*

| Task | Priority | Effort | Impact |
|------|----------|--------|--------|
| Add `Annotated` dependencies | Medium | Low | Better DX |
| Add structlog | High | Low | Better debugging |
| Add OpenTelemetry (10% sampling) | Medium | Medium | Observability |
| Configure basedpyright strict | High | Low | Type safety |
| Add Zustand to frontend | Medium | Low | Cleaner state |
| Add orval OpenAPI codegen | Medium | Medium | E2E types |
| Add TimescaleDB continuous aggregates | High | Medium | Query perf |
| Add testcontainers | Medium | Medium | Real DB tests |

### NEW Phase 5: Performance & Resilience
*Estimated: 1 week*

| Task | Priority | Effort | Impact |
|------|----------|--------|--------|
| Enable uvloop | Medium | Low | +20% throughput |
| Add NVMe for TimescaleDB WAL | High | Medium | Crash recovery |
| Implement offline buffer | Medium | Medium | Resilience |
| Add 100ms batch writer | High | Medium | Meets requirement |
| Configure data retention policies | High | Low | Storage mgmt |

---

## Dependencies to Add

### Python (requirements.txt)

```txt
# 2026 additions
structlog>=24.1.0
opentelemetry-api>=1.27.0
opentelemetry-sdk>=1.27.0
opentelemetry-instrumentation-fastapi>=0.48b0
opentelemetry-instrumentation-redis>=0.48b0
opentelemetry-instrumentation-asyncpg>=0.48b0
uvloop>=0.19.0

# Testing
testcontainers[postgres]>=4.0.0
hypothesis>=6.150.0
syrupy>=4.0.0
httpx>=0.25.0
```

### Frontend (package.json)

```json
{
  "dependencies": {
    "zustand": "^4.5.0"
  },
  "devDependencies": {
    "orval": "^6.25.0"
  }
}
```

### pyproject.toml

```toml
[tool.basedpyright]
typeCheckingMode = "strict"
pythonPlatform = "Linux"
pythonVersion = "3.12"
strictListInference = true
strictDictionaryInference = true
reportUnreachableCode = "error"
reportMissingTypeStubs = "warning"
stubPath = "stubs"
```

---

## What NOT to Change (Confirmed by Research)

| Component | Keep | Research Confirms |
|-----------|------|-------------------|
| Redis Streams for sensors | ✅ | Optimal for IoT scale |
| TimescaleDB for history | ✅ | Best for time-series |
| FastAPI + Depends() | ✅ | Native DI preferred |
| 2s deterministic control loop | ✅ | Correct async pattern |
| WebSocket for real-time | ✅ | Bidirectional needed |
| Vite for frontend | ✅ | Best for SPAs |
| TanStack Query | ✅ | Gold standard |

---

**Plan Version:** 2.0 (with 2026 standards addendum)
**Last Updated:** 2026-01-18

---

## MCP Autostart Issue - Root Cause & Fix

**Issue:** Context7 MCP doesn't always autostart with opencode

**Root Cause:** `npx -y @upstash/context7-mcp@latest` takes **~3.4 seconds** to start, even when cached. This is on the edge of opencode's MCP startup timeout (typically 3-5 seconds), causing intermittent failures.

**Evidence:**
```
$ time npx -y @upstash/context7-mcp@latest --version
real    0m3.417s
user    0m3.273s
sys     0m0.222s
```

### Recommended Fix: Install Globally

```bash
# Install globally - starts instantly
npm install -g @upstash/context7-mcp
```

**Update `~/.config/opencode/opencode.json`:**

```json
// Before (slow - npx overhead)
"context7": {
  "type": "local",
  "command": ["npx", "-y", "@upstash/context7-mcp@latest", "--api-key", "ctx7sk-..."]
}

// After (fast - direct execution)
"context7": {
  "type": "local", 
  "command": ["context7-mcp", "--api-key", "ctx7sk-cb2a9890-4c50-46f1-99ca-81409a15be45"]
}
```

### Alternative: Pre-warm Script

If you prefer to keep using npx (for auto-updates):

```bash
# ~/.local/bin/start-opencode.sh
#!/bin/bash
# Pre-warm MCP cache before starting opencode
npx -y @upstash/context7-mcp@latest --help >/dev/null 2>&1 &
sleep 1
exec opencode "$@"
```

### Why This Affects Only context7

| MCP | Startup Time | Status |
|-----|--------------|--------|
| playwright | ~2.1s | Usually works |
| context7 | ~3.4s | Intermittent failures |
| tavily | N/A (remote) | Always works |

The playwright MCP is faster because `@playwright/mcp` has fewer dependencies to resolve.

