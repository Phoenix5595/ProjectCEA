# Event Bus Consolidation & Schedule Caching Plan

## TL;DR

> **Quick Summary**: Consolidate all schedule updates to use event bus, add Redis caching for schedules, and complete StateManager integration.
> 
> **Deliverables**:
> - Consolidate update_schedules() to event bus only
> - Add Redis caching for schedules (60s TTL)
> - Complete StateManager integration
> - Add cache invalidation on schedule changes
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 2 waves

---

## Work Objectives

### Core Objectives

1. **Consolidate update_schedules()** - Remove direct calls, use event bus only
2. **Add Redis cache for schedules** - Cache with 60s TTL, invalidate on changes
3. **Complete StateManager integration** - All internal state uses StateManager

---

## TODOs

- [ ] 1. Create schedule cache in Redis

  **What to do**:
  - Add cache methods to `app/redis/schedules.py`
  - Cache key: `cache:schedules:{location}:{cluster}` or `cache:schedules:all`
  - TTL: 60 seconds
  - Methods: `get_cached_schedules()`, `set_cached_schedules()`, `invalidate_schedule_cache()`

  **References**:
  - `app/redis/schedules.py` - Current Redis implementation
  - `app/repositories/schedules.py` - DB access

---

- [ ] 2. Update schedule repository to use cache

  **What to do**:
  - Modify `get_schedules()` in `repositories/schedules.py` to check Redis first
  - On cache miss → query DB → write to Redis
  - Add cache invalidation in `create/update/delete` methods

---

- [ ] 3. Consolidate update_schedules() calls to event bus

  **What to do**:
  - Remove direct `scheduler.update_schedules()` calls from:
    - `routes/lights.py` (2 places)
    - `routes/schedules/base.py` (1 place)
    - `services/mode_transition_service.py` (1 place)
  - Instead: publish `SCHEDULE_CHANGED` event
  - Background task already handles event → updates scheduler

  **Keep**:
  - `background_tasks.py` - the event consumer (already good)

---

- [ ] 4. Add SCHEDULE_CHANGED event type

  **What to do**:
  - Add `SCHEDULE_CHANGED = "schedule_changed"` to `ConfigEventType` enum in `app/events/__init__.py`
  - Handle in background_tasks.py event consumer

---

- [ ] 5. Complete StateManager integration

  **What to do**:
  - Ensure all internal state (mode, setpoints, ramps, PID) uses StateManager
  - Remove remaining direct Redis calls for internal-only state
  - Keep Redis for cross-service state (sensor:*, automation:*)

---

## Verification

```bash
# Check Redis has cached schedules
redis-cli KEYS "cache:schedules:*"

# Check cache is working (should be fast)
time curl http://localhost:8001/api/schedules?location=Flower%20Room

# Check DB queries reduced
# (monitor with pg_stat statements)
```
