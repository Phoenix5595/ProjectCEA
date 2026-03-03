# Dashboard Performance Optimization

## TL;DR

> **Quick Summary**: Fix the slow dashboard loading (2-3 seconds) by optimizing the `/api/status` endpoint which takes 2.2-2.5s per request.
> 
> **Root Causes**:
> 1. Service health checks (~1.5-2s) - 5 HTTP calls with 2s timeout each
> 2. Sequential database queries (~200-500ms) - sensor values fetched one-by-one
> 3. Thread switching overhead (~50-100ms) - Redis operations in async context
> 
> **Deliverables**:
> - Add `?health=false` query parameter to skip slow health checks
> - Cache service health checks for 5-10 seconds
> - Batch sensor queries into single DB query
> - Add response caching for status endpoint
> 
> **Estimated Effort**: 2-3 hours
> **Parallel Execution**: NO - sequential (dependencies)

---

## Context

### Current Performance
- Dashboard load time: **2-3 seconds**
- `/api/status` endpoint: **2.2-2.5 seconds** per request
- Frontend polls this endpoint frequently, compounding the problem

### Root Causes

**1. Service Health Checks (~1.5-2s)**
- Location: `Infrastructure/automation-service/app/routes/status.py:214`
- Makes 5 HTTP health check calls to different services
- Each has 2-second timeout
- Services: backend, automation-service, soil-sensor, weather-service, onewire-worker

**2. Sequential Database Queries (~200-500ms)**
- Location: `status.py:196-204`
- Nested loops fetching sensor values one-by-one
- No batching or caching

**3. Thread Switching Overhead (~50-100ms)**
- Redis operations wrapped in `asyncio.to_thread()`

---

## Work Objectives

### Core Objective
Reduce `/api/status` response time from 2.2s to <200ms

### Concrete Deliverables
1. Add `?health=false` query parameter to skip slow health checks
2. Implement health check caching (5-10 second TTL)
3. Batch sensor queries into single database query
4. Add response caching for status endpoint
5. Update frontend to use optimized parameters

### Definition of Done
- [ ] `curl "http://localhost:8001/api/status?health=false"` returns in <500ms
- [ ] `curl http://localhost:8001/api/status` returns in <1s (with caching)
- [ ] Frontend dashboard loads in <2 seconds

### Must Have
- No breaking changes to API contract
- Backward compatible - old calls still work

### Must NOT Have
- Do NOT break existing functionality
- Do NOT remove health checks entirely - just make optional

---

## Execution Strategy

### Sequential Steps

```
Step 1: Add ?health=false query parameter to skip slow health checks
Step 2: Implement health check caching (5-10 second TTL)
Step 3: Batch sensor queries into single DB query
Step 4: Add response caching for status endpoint
Step 5: Update frontend to use health=false for polling
Step 6: Verify performance improvement
```

---

## TODOs

- [ ] 1. Add `?health=false` query parameter to skip slow health checks

  **What to do**:
  - In `Infrastructure/automation-service/app/routes/status.py`:
    - Modify `get_status()` function to accept optional `health` query parameter
    - If `health=false`, skip `_check_service_health()` call
    - Default `health=true` for backward compatibility
  - Test: `curl "http://localhost:8001/api/status?health=false"`

  **Must NOT do**:
  - Do NOT remove health checks - just make optional

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Small code change

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 2
  - **Blocked By**: None

  **Acceptance Criteria**:
  - [ ] `?health=false` skips health checks
  - [ ] Default behavior unchanged

  **Commit**: YES

---

- [ ] 2. Implement health check caching (5-10 second TTL)

  **What to do**:
  - In `Infrastructure/automation-service/app/routes/status.py`:
    - Add module-level cache for health check results
    - Store timestamp and results
    - Return cached results if <10 seconds old
    - Refresh in background if stale
  - This prevents blocking every request on health checks

  **Must NOT do**:
  - Do NOT block the response on cache refresh

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple caching logic

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 3
  - **Blocked By**: Task 1

  **Acceptance Criteria**:
  - [ ] First call fetches fresh health data
  - [ ] Subsequent calls within 10s return cached data
  - [ ] No blocking on cache refresh

  **Commit**: YES

---

- [ ] 3. Batch sensor queries into single DB query

  **What to do**:
  - In `Infrastructure/automation-service/app/routes/status.py`:
    - Replace nested loop sensor fetching with single batch query
    - Use `WHERE sensor_name IN (...)` to fetch all at once
    - Build dictionary for O(1) lookup
  - In `Infrastructure/automation-service/app/repositories/sensor.py`:
    - Add `get_sensor_values_batch(sensor_names: list[str])` method
    - Returns dict of {sensor_name: value}

  **Must NOT do**:
  - Do NOT change the response format

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Database query optimization

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 4
  - **Blocked By**: Task 2

  **Acceptance Criteria**:
  - [ ] Single DB query instead of N queries
  - [ ] Response format unchanged

  **Commit**: YES

---

- [ ] 4. Add response caching for status endpoint

  **What to do**:
  - In `Infrastructure/automation-service/app/routes/status.py`:
    - Add in-memory cache for full status response
    - Cache TTL: 1-2 seconds
    - Use `lru_cache` or custom cache
  - Only cache if not explicitly requesting fresh data

  **Must NOT do**:
  - Do NOT cache user-specific data

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple caching

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 5
  - **Blocked By**: Task 3

  **Acceptance Criteria**:
  - [ ] Cached responses return in <100ms
  - [ ] Cache invalidates after TTL

  **Commit**: YES

---

- [ ] 5. Update frontend to use health=false for polling

  **What to do**:
  - In `Infrastructure/frontend/src/services/api.ts`:
    - Add `health=false` to status API calls
  - In `Infrastructure/frontend/src/pages/Dashboard.tsx`:
    - Optionally reduce polling frequency

  **Must NOT do**:
  - Do NOT break existing functionality

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Frontend update

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: Task 6
  - **Blocked By**: Task 4

  **Acceptance Criteria**:
  - [ ] Frontend uses optimized API calls

  **Commit**: YES

---

- [ ] 6. Verify performance improvement

  **What to do**:
  - Run verification commands:
    ```bash
    curl -s -w "Time: %{time_total}s\n" "http://localhost:8001/api/status?health=false"
    curl -s -w "Time: %{time_total}s\n" http://localhost:8001/api/status
    ```
  - Check dashboard loads in browser

  **Must NOT do**:
  - Do NOT skip verification

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Verification

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Blocks**: None
  - **Blocked By**: Task 5

  **Acceptance Criteria**:
  - [ ] `?health=false` returns in <500ms
  - [ ] Default returns in <1s
  - [ ] Dashboard loads in <2s

  **Commit**: NO

---

## Success Criteria

### Verification Commands
```bash
# Before optimization
curl -s -w "Time: %{time_total}s\n" http://localhost:8001/api/status
# Expected: ~2.2s

# After Phase 1 (health=false)
curl -s -w "Time: %{time_total}s\n" "http://localhost:8001/api/status?health=false"
# Expected: ~0.3s

# After all optimizations
curl -s -w "Time: %{time_total}s\n" http://localhost:8001/api/status
# Expected: ~0.1-0.5s
```

### Final Checklist
- [ ] Health parameter added and working
- [ ] Health check caching implemented
- [ ] Sensor queries batched
- [ ] Response caching implemented
- [ ] Frontend updated
- [ ] Performance verified (<2s dashboard load)
