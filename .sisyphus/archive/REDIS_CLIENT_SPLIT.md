# Plan: Split redis_client.py into Modules

**Created:** 2026-01-17
**Status:** Planning
**Priority:** Low (deferred - working production code)

## Overview

Split the monolithic `redis_client.py` (1,413 lines, 35 methods) into focused modules for maintainability.

## Current Structure

```
Infrastructure/automation-service/app/redis_client.py
└── AutomationRedisClient (1 class, 35 methods)
```

## Proposed Structure

```
Infrastructure/automation-service/app/redis/
├── __init__.py          # Re-exports AutomationRedisClient (backward compat)
├── base.py              # Core class, connection management (~100 lines)
├── streams.py           # write_to_stream, write_to_state (~120 lines)
├── setpoints.py         # read/write setpoints, effective setpoints (~400 lines)
├── modes.py             # read_mode, write_mode (~55 lines)
├── failsafe.py          # read/write/clear failsafe (~90 lines)
├── alarms.py            # write/acknowledge/read/clear alarms (~160 lines)
├── heartbeat.py         # write/check heartbeat (~60 lines)
├── sensors.py           # last_good_value operations (~100 lines)
├── pid.py               # read/write PID parameters (~70 lines)
├── lighting.py          # write/read light intensity (~80 lines)
├── ramps.py             # write/read/clear ramp state (~70 lines)
└── schedules.py         # write/read schedule state (~100 lines)
```

## Method Distribution

| Module | Methods | Lines |
|--------|---------|-------|
| base.py | `__init__`, `connect`, `close` | ~100 |
| streams.py | `write_to_stream`, `write_to_state` | ~120 |
| setpoints.py | `read_setpoint`, `write_setpoint`, `write_effective_setpoints`, `read_setpoint_source`, `check_rate_limit` | ~400 |
| modes.py | `read_mode`, `write_mode` | ~55 |
| failsafe.py | `read_failsafe`, `write_failsafe`, `clear_failsafe` | ~90 |
| alarms.py | `write_alarm`, `acknowledge_alarm`, `read_alarms`, `clear_alarm` | ~160 |
| heartbeat.py | `write_heartbeat`, `check_heartbeat` | ~60 |
| sensors.py | `write_last_good_value`, `read_last_good_value`, `check_last_good_age` | ~100 |
| pid.py | `read_pid_parameters`, `write_pid_parameters` | ~70 |
| lighting.py | `write_light_intensity`, `read_light_intensity` | ~80 |
| ramps.py | `write_ramp_state`, `read_ramp_state`, `clear_ramp_state` | ~70 |
| schedules.py | `write_schedule_state`, `read_schedule_state` | ~100 |

## Implementation Strategy

### Option A: Mixin Classes (Recommended)
Use mixin classes that the main `AutomationRedisClient` inherits from.

```python
# base.py
class RedisConnectionMixin:
    redis_client: Redis
    stream_client: Redis
    # connection methods...

# setpoints.py
class SetpointsMixin:
    redis_client: Redis  # type hint for mixin
    
    def read_setpoint(self, location: str, cluster: str) -> dict[str, Any] | None:
        ...

# __init__.py
class AutomationRedisClient(
    RedisConnectionMixin,
    SetpointsMixin,
    ModesMixin,
    FailsafeMixin,
    AlarmsMixin,
    HeartbeatMixin,
    SensorsMixin,
    PIDMixin,
    LightingMixin,
    RampsMixin,
    SchedulesMixin,
):
    """Combined Redis client for automation service."""
    pass
```

**Pros:**
- Zero breaking changes - same class, same methods
- Easy to test individual mixins
- Clear separation of concerns

**Cons:**
- Mixin pattern can be confusing
- IDE autocomplete may be affected

### Option B: Composition with Delegation
Keep the main class but delegate to sub-clients.

```python
# __init__.py
class AutomationRedisClient:
    def __init__(self, redis_url: str, redis_ttl: int = 3600):
        self._connection = RedisConnection(redis_url, redis_ttl)
        self.setpoints = SetpointsClient(self._connection)
        self.alarms = AlarmsClient(self._connection)
        # ...
    
    # Backward compat - delegate to sub-clients
    def read_setpoint(self, *args, **kwargs):
        return self.setpoints.read(*args, **kwargs)
```

**Pros:**
- Clear ownership of functionality
- Sub-clients can be used independently

**Cons:**
- Requires updating all callers OR maintaining delegation methods
- More complex initialization

### Option C: Keep Single Class, Split File Only
Use `# region` comments and partial class loading.

```python
# redis_client.py (main file)
from .redis._setpoints import _setpoint_methods
from .redis._alarms import _alarm_methods

class AutomationRedisClient:
    __init__ = _connection_init
    read_setpoint = _setpoint_methods['read']
    # ...
```

**Pros:**
- Minimal code changes
- Single class maintained

**Cons:**
- Unusual pattern
- Harder to understand

## Recommended Approach: Option A (Mixins)

1. **Phase 1: Create module structure**
   - Create `app/redis/` directory
   - Create empty module files
   - Create `__init__.py` with imports

2. **Phase 2: Extract mixins (one at a time)**
   - Start with smallest: `heartbeat.py` (60 lines)
   - Then `modes.py`, `pid.py`, `ramps.py`
   - Progress to larger: `alarms.py`, `setpoints.py`
   - Keep `base.py` for last

3. **Phase 3: Update main class**
   - Change `AutomationRedisClient` to inherit from mixins
   - Verify all tests pass
   - Verify LSP diagnostics

4. **Phase 4: Cleanup**
   - Remove old `redis_client.py`
   - Update any direct imports
   - Deploy

## Backward Compatibility

The `__init__.py` will re-export `AutomationRedisClient` so existing imports work:

```python
# Old import (still works)
from app.redis_client import AutomationRedisClient

# New import (also works)  
from app.redis import AutomationRedisClient
```

## Testing Strategy

1. Run existing tests after each mixin extraction
2. Add unit tests for each mixin independently
3. Integration test the combined class
4. Verify production behavior matches

## Rollback Plan

If issues arise:
1. `git checkout HEAD~1 -- Infrastructure/automation-service/app/redis_client.py`
2. Remove `app/redis/` directory
3. Redeploy

## Estimated Effort

| Phase | Time |
|-------|------|
| Phase 1: Structure | 15 min |
| Phase 2: Extract mixins | 2-3 hours |
| Phase 3: Update main class | 30 min |
| Phase 4: Cleanup & test | 1 hour |
| **Total** | **4-5 hours** |

## Prerequisites

- [ ] All current LSP errors resolved
- [ ] Tests passing
- [ ] Production stable
- [ ] Dedicated refactoring session (not during other work)

## When to Execute

Execute this plan when:
1. User explicitly requests the refactor
2. Adding new Redis functionality that would benefit from modular structure
3. During a dedicated code health sprint

**Do NOT execute during:**
- Bug fixes
- Feature development
- Production issues
