# Redis-Asyncio Hybrid Architecture Implementation Plan

## TL;DR

> **Quick Summary**: Migrate internal automation-service state from Redis to asyncio in-memory structures while keeping cross-service communication in Redis. Creates a new `StateManager` service that handles in-memory caching with TTL, dual-writes to Redis for cross-service visibility, and integrates with existing architecture.
> 
> **Deliverables**:
> - New `app/state/` module with asyncio TTL cache
> - Dual-write pattern: in-memory (fast) + Redis (cross-service)
> - Migrated internal state: mode, setpoints, schedules, ramps, PID params, alarms
> - Removed internal Redis calls for state that doesn't need cross-service sharing
> 
> **Estimated Effort**: XL
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Core module → Migration → Integration → Cleanup

---

## Context

### Current State

Redis is used for TWO distinct purposes:

1. **Cross-Service Communication** (MUST STAY IN REDIS):
   - `sensor:raw` stream - shared data bus
   - `sensor:*` live state - read by backend for UI
   - `automation:*` state - shared control state
   - `sensor:update` pub/sub - event notifications
   - Heartbeat for service health

2. **Internal State** (CAN MIGRATE TO ASYNCIO):
   - Mode cache (300s TTL)
   - Setpoints cache (60s TTL)
   - Effective setpoints (300s TTL)
   - Ramp state (10s TTL)
   - Last-good sensor cache (40s TTL)
   - PID parameters cache (300s TTL)
   - Rate limiting (2s TTL)

### Analysis Summary

| Category | Keys | TTL Range | Usage | Can Migrate? |
|----------|------|-----------|-------|---------------|
| **Cross-Service** | sensor:*, automation:* | 10s | Multiple services | ❌ NO |
| **Cross-Service** | sensor:raw stream | N/A | All services | ❌ NO |
| **Cross-Service** | sensor:update pub/sub | N/A | Future consumers | ❌ NO |
| **Internal** | last_good | 40s | Control loop only | ✅ YES |
| **Internal** | rate_limit | 2s | API throttling | ✅ YES |
| **Internal** | ramp_state | 10s | Control loop only | ✅ YES |
| **Internal** | effective_setpoints | 300s | Control loop only | ✅ YES |
| **Internal** | mode | 300s | Control loop only | ✅ YES |

---

## Architecture

### Proposed Hybrid Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PROPOSED HYBRID ARCHITECTURE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    AUTOMATION-SERVICE                                 │    │
│  │                                                                      │    │
│  │  ┌─────────────────────┐         ┌─────────────────────┐             │    │
│  │  │   StateManager    │         │  AutomationRedis   │             │    │
│  │  │   (ASYNCIO)       │         │    (REDIS)        │             │    │
│  │  │                    │         │                    │             │    │
│  │  │  • Mode cache     │         │  • sensor:raw     │             │    │
│  │  │  • Setpoints     │   ◄──►  │  • sensor:*       │             │    │
│  │  │  • Ramp state    │  DUAL   │  • automation:*   │             │    │
│  │  │  • PID params    │  WRITE  │  • sensor:update  │             │    │
│  │  │  • Rate limit   │         │  • heartbeat      │             │    │
│  │  │                    │         │                    │             │    │
│  │  └─────────────────────┘         └─────────────────────┘             │    │
│  │           │                              │                           │    │
│  │           │   In-memory (fast)          │   Cross-service (shared)  │    │
│  │           ▼                              ▼                           │    │
│  │  ┌─────────────────────────────────────────────────────────────┐       │    │
│  │  │                    CONTROL LOOP (2s tick)                 │       │    │
│  │  │  Reads from StateManager for all internal state          │       │    │
│  │  └─────────────────────────────────────────────────────────────┘       │    │
│  │                                                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                    ┌───────────────┼───────────────┐                       │
│                    ▼               ▼               ▼                        │
│             ┌──────────┐   ┌──────────┐   ┌──────────┐                 │
│             │ Backend  │   │  CAN     │   │  Soil    │                 │
│             │ (Port8000)│   │ Processor│   │  Sensor  │                 │
│             └──────────┘   └──────────┘   └──────────┘                 │
│                 │                                                       │
│                 │  All read from Redis for cross-service state          │
│                 ▼                                                       │
│          ┌──────────────┐                                               │
│          │  REDIS       │                                               │
│          │  (Shared)    │                                               │
│          └──────────────┘                                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Pattern

```
WRITE PATH (Dual Write):
═══════════════════════

API Request (setpoints, mode, etc.)
         │
         ▼
┌────────────────────────────┐
│ 1. Write to DATABASE      │  ← Authoritative store
│    (persists)            │
└────────────────────────────┘
         │
         ▼
┌────────────────────────────┐
│ 2. Write to StateManager │  ← In-memory (fast)
│    (asyncio cache)       │
└────────────────────────────┘
         │
         ▼
┌────────────────────────────┐
│ 3. Write to Redis        │  ← Cross-service visibility
│    (shared state)         │
└────────────────────────────┘
         │
         ▼
┌────────────────────────────┐
│ 4. Publish Config Event   │  ← Notify other services
│    (Redis Pub/Sub)       │
└────────────────────────────┘


READ PATH (Local First):
═══════════════════════

Control Loop needs state
         │
         ▼
┌────────────────────────────┐
│ 1. Read from StateManager │  ← In-memory (fastest)
│    (asyncio cache)       │
└────────────────────────────┘
         │
    ┌────┴────┐
    │ HIT     │ MISS
    ▼         ▼
Return    ┌────────────────────────────┐
value     │ 2. Fallback to Redis     │
          │    (cross-service)       │
          └────────────────────────────┘
```

---

## Work Objectives

### Core Objective

Implement asyncio-based state management within automation-service while maintaining Redis for cross-service communication.

### Concrete Deliverables

- New `app/state/` module with `StateManager` class
- In-memory TTL cache with automatic expiration
- Dual-write pattern: memory + Redis
- Migrated state types: mode, setpoints, ramps, PID params, alarms
- Removed internal Redis calls for migrated state
- Backward compatibility with existing Redis consumers

### Definition of Done

- [ ] Control loop reads from StateManager (not Redis) for internal state
- [ ] API endpoints write to both StateManager and Redis
- [ ] State survives service restart (via Redis persistence)
- [ ] Cross-service visibility maintained (backend can still read state)
- [ ] All tests pass
- [ ] Performance improvement: <1ms read latency (vs ~5ms Redis)

### Must Have

- Thread-safe asyncio cache with TTL
- Automatic cleanup of expired entries
- Graceful fallback to Redis on cache miss
- Dual-write for cross-service visibility
- Memory bounds to prevent unbounded growth

### Must NOT Have (Guardrails)

- NO removal of Redis for cross-service keys
- NO breaking changes to backend API
- NO loss of data during migration
- NO memory leaks from unbounded caches

---

## Execution Strategy

### Phase 1: Core Infrastructure (Wave 1)

```
Wave 1 (Start Immediately):
├── Task 1: Create StateManager module with asyncio TTL cache
├── Task 2: Implement dual-write pattern for mode
└── Task 3: Implement dual-write pattern for setpoints
```

### Phase 2: Migrate State Types (Wave 2)

```
Wave 2 (After Wave 1):
├── Task 4: Migrate ramps to StateManager
├── Task 5: Migrate PID params to StateManager
└── Task 6: Migrate alarms/failsafe to StateManager
```

### Phase 3: Integration & Cleanup (Wave 3)

```
Wave 3 (After Wave 2):
├── Task 7: Update control loop to use StateManager
├── Task 8: Remove internal Redis calls (keep cross-service)
└── Task 9: Add tests and performance validation
```

---

## TODOs

- [x] 1. Create StateManager Module

  **What to do**:
  - Create `app/state/__init__.py` with StateManager class
  - Implement in-memory TTL cache using asyncio
  - Add get/set methods with automatic expiration
  - Add memory bounds (max entries)
  - Add startup initialization from Redis

  **Must NOT do**:
  - Do NOT remove Redis dependency entirely
  - Do NOT make cache unbounded

  **Implementation Pattern**:
  ```python
  import asyncio
  from dataclasses import dataclass, field
  from typing import Any, TypeVar, Generic
  import time

  T = TypeVar('T')

  @dataclass
  class CacheEntry(Generic[T]):
      value: T
      expires_at: float

  class StateManager:
      """In-memory state manager with TTL and Redis fallback."""
      
      def __init__(self, default_ttl: float = 60.0, max_entries: int = 1000):
          self._cache: dict[str, CacheEntry] = {}
          self._default_ttl = default_ttl
          self._max_entries = max_entries
          self._lock = asyncio.Lock()
      
      async def get(self, key: str) -> Any | None:
          """Get value from cache, returns None if expired/missing."""
          async with self._lock:
              entry = self._cache.get(key)
              if entry is None:
                  return None
              if time.time() > entry.expires_at:
                  del self._cache[key]
                  return None
              return entry.value
      
      async def set(self, key: str, value: Any, ttl: float | None = None) -> None:
          """Set value in cache with TTL."""
          async with self._lock:
              if len(self._cache) >= self._max_entries:
                  # Evict oldest entry
                  oldest = min(self._cache.items(), key=lambda x: x[1].expires_at)
                  del self._cache[oldest[0]]
              
              ttl = ttl or self._default_ttl
              self._cache[key] = CacheEntry(
                  value=value,
                  expires_at=time.time() + ttl
              )
      
      async def delete(self, key: str) -> None:
          """Delete key from cache."""
          async with self._lock:
              self._cache.pop(key, None)
  ```

  **References**:
  - `app/redis/modes.py` - Current mode Redis pattern
  - `app/redis/setpoints.py` - Current setpoints Redis pattern

  **Acceptance Criteria**:
  - [ ] StateManager class created
  - [ ] TTL expiration working
  - [ ] Memory bounds enforced
  - [ ] Thread-safe with asyncio.Lock

---

- [x] 2. Migrate Mode to StateManager

  **What to do**:
  - Add mode-specific methods to StateManager
  - Update routes/mode.py to write to both StateManager and Redis
  - Add Redis fallback on cache miss
  - Add dual-write: StateManager first, then Redis

  **Must NOT do**:
  - Do NOT remove Redis write (needed for cross-service)
  - Do NOT break existing mode API

  **References**:
  - `app/routes/mode.py` - Current mode endpoints
  - `app/redis/modes.py` - Current Redis implementation

  **Acceptance Criteria**:
  - [ ] Mode stored in StateManager
  - [ ] Mode written to Redis for cross-service
  - [ ] Cache miss falls back to Redis
  - [ ] Existing tests pass

---

- [x] 3. Migrate Setpoints to StateManager

  **What to do**:
  - Add setpoints-specific methods to StateManager
  - Update routes/setpoints.py to use dual-write
  - Handle effective_setpoints computation
  - Add TTL configuration per setpoint type

  **References**:
  - `app/routes/setpoints.py` - Current setpoints endpoints
  - `app/redis/setpoints.py` - Current Redis implementation

  **Acceptance Criteria**:
  - [ ] Setpoints stored in StateManager
  - [ ] Effective setpoints computed correctly
  - [ ] TTL: 60s for setpoints, 300s for effective

---

- [x] 4. Migrate Ramps to StateManager

  **What to do**:
  - Add ramp-specific methods to StateManager
  - Update control/scheduler.py to use StateManager
  - Handle ramp persistence for restart recovery

  **References**:
  - `app/control/scheduler.py` - Ramp logic
  - `app/redis/ramps.py` - Current Redis implementation

  **Acceptance Criteria**:
  - [ ] Active ramp state in StateManager
  - [ ] Ramp recovery on service restart
  - [ ] 10s TTL for ramp state

---

- [x] 5. Migrate PID Params to StateManager

  **What to do**:
  - Add PID-specific methods to StateManager
  - Update routes/pid.py to use dual-write
  - Update control/pid_controller_manager.py to read from StateManager

  **References**:
  - `app/routes/pid.py` - Current PID endpoints
  - `app/redis/pid.py` - Current Redis implementation
  - `app/control/pid_controller_manager.py` - PID usage

  **Acceptance Criteria**:
  - [ ] PID params in StateManager
  - [ ] 300s TTL for PID params
  - [ ] Autotune state preserved

---

- [x] 6. Migrate Alarms/Failsafe to StateManager

  **What to do**:
  - Add alarm-specific methods to StateManager
  - Update alarm_manager.py to use StateManager
  - Handle alarm acknowledgment

  **References**:
  - `app/alarm_manager.py` - Current alarm logic
  - `app/redis/alarms.py` - Current Redis implementation

  **Acceptance Criteria**:
  - [ ] Alarms in StateManager
  - [ ] Failsafe state in StateManager
  - [ ] No TTL (persist until acknowledged)

---

- [x] 7. Update Control Loop

  **What to do**:
  - Update control_engine.py to read from StateManager
  - Remove internal Redis reads for migrated state
  - Keep Redis reads for sensor values (cross-service)

  **References**:
  - `app/control/control_engine.py` - Main control loop

  **Acceptance Criteria**:
  - [ ] Control loop uses StateManager
  - [ ] Latency <1ms for state reads
  - [ ] No regression in control timing

---

- [x] 8. Remove Internal Redis Calls

  **What to do**:
  - Audit remaining Redis calls in automation-service
  - Remove internal-only Redis reads (keep cross-service writes)
  - Update redis/ modules to only handle cross-service

  **References**:
  - `app/redis/modes.py` - To be simplified
  - `app/redis/setpoints.py` - To be simplified

  **Acceptance Criteria**:
  - [ ] Internal state via StateManager
  - [ ] Cross-service state via Redis
  - [ ] Clear separation of concerns

---

- [x] 9. Tests and Performance Validation

  **What to do**:
  - Add unit tests for StateManager
  - Add integration tests for dual-write
  - Benchmark: compare Redis vs StateManager latency
  - Validate cache miss fallback

  **References**:
  - `tests/test_event_bus.py` - Test patterns

  **Acceptance Criteria**:
  - [ ] All tests pass
  - [ ] StateManager latency <1ms
  - [ ] Redis fallback working

---

## Verification

### Test Scenarios

**Scenario 1: Mode Change**
```
Tool: Bash
Steps:
  1. curl -X POST http://localhost:8001/api/mode/Flower%20Room/main -d '{"mode":"DAY"}'
  2. Verify in-memory StateManager has mode
  3. Verify Redis has mode (for backend)
  4. Check control loop reads from StateManager
Expected: Both caches updated, backend can read
```

**Scenario 2: Cache Miss Fallback**
```
Tool: Bash  
Steps:
  1. Restart automation-service
  2. Control loop reads mode (cache miss)
  3. Verify fallback to Redis works
Expected: State recovered from Redis
```

**Scenario 3: Performance**
```
Tool: Bash
Steps:
  1. Time 100 state reads from StateManager
  2. Time 100 state reads from Redis
Expected: StateManager 10x faster
```

---

## Commit Strategy

| Task | Message | Files |
|------|---------|-------|
| 1 | feat(state): add StateManager with asyncio TTL cache | app/state/__init__.py |
| 2 | feat(state): migrate mode to StateManager | app/state/, app/routes/mode.py |
| 3 | feat(state): migrate setpoints to StateManager | app/state/, app/routes/setpoints.py |
| 4 | feat(state): migrate ramps to StateManager | app/state/, app/control/scheduler.py |
| 5 | feat(state): migrate PID params to StateManager | app/state/, app/routes/pid.py |
| 6 | feat(state): migrate alarms to StateManager | app/state/, app/alarm_manager.py |
| 7 | refactor(control): use StateManager in control loop | app/control/control_engine.py |
| 8 | refactor(redis): remove internal Redis calls | app/redis/*.py |
| 9 | test(state): add StateManager tests | tests/test_state_manager.py |

---

## Summary

### What Stays in Redis (Cross-Service)

| Key Pattern | Purpose | Reason |
|-------------|---------|--------|
| `sensor:*` | Live sensor values | Backend reads for UI |
| `automation:*` | Device state | Shared across services |
| `sensor:raw` | Historical stream | All services write |
| `sensor:update` | Events | Pub/Sub channel |
| `heartbeat:*` | Service health | External monitoring |

### What Moves to Asyncio (Internal)

| State Type | TTL | Reason |
|------------|-----|--------|
| Mode | 300s | Control loop only |
| Setpoints | 60s | Control loop only |
| Effective Setpoints | 300s | Control loop only |
| Ramp State | 10s | Control loop only |
| PID Params | 300s | Control loop only |
| Last-good Cache | 40s | Control loop only |
| Rate Limit | 2s | API only |

### Performance Improvement

| Metric | Current (Redis) | Target (Asyncio) | Improvement |
|--------|----------------|------------------|------------|
| State read latency | ~5ms | <1ms | 5x faster |
| State write latency | ~5ms | <0.1ms | 50x faster |
| Control loop overhead | ~50ms | <5ms | 10x less |

---

## Next Steps

Run `/start-work` to begin implementation.
