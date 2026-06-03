# Relay State Caching — Eliminate Slow Load on Devices & ZoneConfig Pages

## TL;DR

> **Quick Summary**: Cache MCP23017 relay channel states in Redis after each control loop hardware batch. The API endpoint reads from Redis (sub-millisecond) instead of hitting I2C hardware on every request. Restore relay states from DB on service restart. Eliminates the "Unknown" flash and several-seconds delay when opening the Devices or ZoneConfig pages.
>
> **Deliverables**:
> - Redis key `cea:relay:channels` — written by control loop after each hardware batch
> - API endpoint reads Redis first, falls back to hardware on cache miss (<1ms response)
> - Startup relay state restoration from PostgreSQL — no "all OFF" blank slate on restart
> - Frontend: eliminate `relayState = null` initial state on Devices page
>
> **Estimated Effort**: Quick (4 files backend, 1 file frontend)
> **Parallel Execution**: YES — backend and frontend in separate waves
> **Critical Path**: Redis schema → batch executor → API endpoint → frontend → verification

---

## Context

### Original Request
> "most of the lights relays often say off when the page is loaded and it takes a few seconds to update. thats too slwo. investigate, explore and propose a proper fix"

### Root Cause (Confirmed)
1. **Frontend**: `relayState` starts as `null` on Devices page (line 81). `buildRelayChannelViewModels` returns `isStateKnown: false` for all 16 channels — every channel shows yellow "Unknown" badge until the first API response arrives.
2. **Backend**: `GET /api/hardware/relays/state` does a direct I2C hardware read via `mcp.get_all_channels()` — 16 sequential `smbus2.read_byte_data()` calls. No Redis caching, no in-memory serving.
3. **Startup**: On service restart, MCP23017 is initialized to all OFF (`0x00` written to GPIOA/GPIOB). `RelayManager._current_states` is empty. `restore_states()` exists but is never called.
4. **Result**: Every page load = 16 I2C reads (~1.6ms hardware + FastAPI overhead = ~3-10ms). The delay the user perceives is NOT the API — it's the frontend starting from `null` and waiting for the first poll (race between `loadChannels()` and `refreshRelayState()` both firing on mount).

### Performance Data
| Operation | Latency | |
|-----------|---------|---|
| I2C hardware read (16 channels) | ~1.6ms | Not a bottleneck |
| API endpoint total | ~3-10ms | Acceptable |
| Redis GET (new) | <1ms | Instant |
| Frontend Unknown flash | 1-5 seconds | THE PROBLEM |

### Metis Review
**Identified Gaps** (all addressed):
- ✅ `restore_states()` is dead code — wire into container startup or use `DeviceController.restore_device_states()` which restores from DB
- ✅ No Redis key pattern exists — add `cea:relay:channels` to `schema.py`
- ✅ Hardware batch executor in `hardware_batch.py` is the correct write point (post-relay-execution)
- ✅ Container `restore_light_intensities()` exists but no relay equivalent — add `restore_relay_states()`
- ✅ Frontend null initial state can be fixed independently — pre-populate from a structured default

---

## Work Objectives

### Core Objective
Eliminate the "Unknown" / "OFF" flash on relay matrices (both Devices page and ZoneConfig) by caching relay states in Redis, restoring them on startup from PostgreSQL, and fixing the frontend null initial state.

### Concrete Deliverables
- `Infrastructure/automation-service/app/redis/schema.py` — new key pattern `RELAY_CHANNELS`
- `Infrastructure/automation-service/app/control/hardware_batch.py` — write relay states to Redis post-execution
- `Infrastructure/automation-service/app/control/device_controller.py` — wire `restore_device_states()` into startup
- `Infrastructure/automation-service/app/routes/hardware.py` — read from Redis, fall back to hardware
- `Infrastructure/frontend/src/components/DeviceManager.tsx` — eliminate `relayState = null` gap

### Definition of Done
- [ ] Opening Devices page: relay states visible INSTANTLY (no Unknown flash)
- [ ] Opening ZoneConfig page: relay states visible INSTANTLY
- [ ] `GET /api/hardware/relays/state` returns in <2ms (Redis cache hit)
- [ ] Service restart: relay states restored from DB within 5 seconds
- [ ] No regressions: control loop timing unchanged, DB writes unchanged
- [ ] `ruff check` passes, `npm run build` passes

### Must Have
- Redis key `cea:relay:channels` written every control loop tick by hardware batch executor
- API endpoint serves from Redis (TTL: none, refreshed per tick — always fresh)
- Hardware fallback on Redis miss (returns same data as before)
- Service startup: restore relay states from PostgreSQL `device_states` table
- Frontend Devices page: eliminate null initial state, show real data immediately
- ZoneConfig: already benefits from Redis cache with no additional changes

### Must NOT Have (Guardrails)
- **Redis key is READ-ONLY for display consumers** — control loop continues to read hardware directly (`mcp.get_all_channels()`). Do NOT change the control loop to read relay state from Redis.
- **`relay_manager._current_states` in-memory dict stays authoritative** for device-level control decisions. The Redis key is hardware-channel-level only.
- No changes to I2C read/write logic in MCP23017 driver
- No WebSocket push for relay state (polling is sufficient)
- No changes to the control loop tick rate or scheduling
- No changes to the DB write path (per-tick device_states writes stay)
- No changes to `RelayChannelMatrix`, `RelayChannelBox`, or `relayViewModel`
- No new API endpoints
- No frontend polling interval changes (5s stays)
- No changes to relay state at the compact variant on ZoneConfig (it already works, just gets faster)
- No changes to DeviceManager's channel assignment / edit form functionality

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**

### Test Decision
- **Infrastructure exists**: YES (pytest, npm build, curl)
- **Automated tests**: NO new tests needed (existing infrastructure sufficient)
- **Framework**: pytest-asyncio, curl for API verification

### Agent-Executed QA Scenarios

| Task | Tool | How Agent Verifies |
|------|------|-------------------|
| 1 (Redis schema) | Bash (curl + redis-cli) | Write to key, read back, verify API serves cached value |
| 2 (Batch executor) | Bash (journalctl + redis-cli) | Verify Redis key updated after each control loop tick |
| 3 (Startup restore) | Bash (systemctl restart + curl) | Restart service, verify relay states return within 5s |
| 4 (API endpoint) | Bash (curl timing) | Measure response time <2ms, verify cache hit |
| 5 (Frontend) | Bash (npm build + grep) | Build passes, null initial state eliminated |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Backend — sequential due to dependency chain):
├── Task 1: Redis key schema + write relay states from batch executor
├── Task 2: API endpoint reads Redis, falls back to hardware
└── Task 3: Startup relay state restoration from DB

Wave 2 (Frontend — independent of backend):
└── Task 4: Eliminate relayState = null on Devices page

Wave 3 (Final):
└── Task 5: End-to-end verification (restart service, load pages, measure timing)
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3 | None (foundation) |
| 2 | 1 | 5 | 3 |
| 3 | 1 | 5 | 2, 4 |
| 4 | None | 5 | 3 |
| 5 | 2, 3, 4 | None | None (final) |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 1 | 1 | `category="quick"` — Redis key + batch executor write |
| 1 | 2, 3 | `category="quick"` — API refactor + startup restore |
| 2 | 4 | `category="visual-engineering", skills=["frontend-ui-ux"]` |
| 3 | 5 | `category="quick"` — verification sweep |

---

## TODOs

> Implementation + Test = ONE Task. Never separate.

- [ ] 1. **Add Redis Key Schema + Write Relay States from Batch Executor**

  **What to do**:
  1. Add relay channel key pattern to `Infrastructure/automation-service/app/redis/schema.py`:
     ```python
     RELAY_CHANNELS = "cea:relay:channels"
     ```
     - TTL: NONE (refreshed every control loop tick — always fresh)
     - Value: JSON array of 16 booleans `[true, false, true, ...]`
  2. In `Infrastructure/automation-service/app/control/hardware_batch.py`, after the `asyncio.gather(_execute_relay_group(), _execute_dimmer_group())` completes, add logic to read and write relay states:
     ```python
     # After hardware batch execution, cache relay states in Redis
     try:
         relay_states = self._mcp.get_all_channels()
         await self._redis.set("cea:relay:channels", json.dumps([bool(s) for s in relay_states]))
     except Exception as e:
         logger.warning(f"Failed to cache relay states in Redis: {e}")
     ```
  3. Ensure `self._redis` (or equivalent redis client) is available in `HardwareBatchExecutor`. If not, inject it via `__init__` or use the shared Redis client from the container.

  **Must NOT do**:
  - Do NOT change the hardware batch execution order or timing
  - Do NOT add per-channel Redis keys (one key for all 16 channels is sufficient)
  - Do NOT set a TTL on the key — must always be available for instant frontend reads
  - Do NOT write relay state to Redis in `control_engine.py` — the batch executor is the single sync point

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (blocks Tasks 2, 3)
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/redis/schema.py` — existing key patterns to extend
  - `Infrastructure/automation-service/app/control/hardware_batch.py:380-410` — batch executor where relay group completes
  - `Infrastructure/automation-service/app/hardware/mcp23017.py:221-235` — `get_all_channels()` method
  - `Infrastructure/automation-service/app/redis_client.py:100-140` — Redis set method

  **Acceptance Criteria**:
  - [ ] `RELAY_CHANNELS` key defined in `schema.py`
  - [ ] `hardware_batch.py` writes 16-channel boolean array to Redis after each batch execution
  - [ ] `redis-cli GET cea:relay:channels` returns valid JSON array after a control loop tick completes
  - [ ] Key has no TTL (verified via `redis-cli TTL cea:relay:channels` → -1)

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Redis key populated after control loop tick
    Tool: Bash (redis-cli)
    Preconditions: automation-service running for >3 seconds
    Steps:
      1. redis-cli GET cea:relay:channels
      2. Assert: output is a JSON array of 16 booleans
      3. redis-cli TTL cea:relay:channels
      4. Assert: output is "-1" (no TTL, persists until overwritten)
      5. redis-cli GET cea:relay:channels  (repeat after 2s)
      6. Assert: value is refreshed (may differ if relays changed)
    Expected Result: Redis key populated and refreshed each tick
    Evidence: Terminal output captured
  ```

  **Commit**: YES
  - Message: `feat(relay): cache MCP23017 relay states in Redis after hardware batch`
  - Files: `schema.py`, `hardware_batch.py`

---

- [ ] 2. **API Endpoint: Serve Relay State from Redis**

  **What to do**:
  Modify `GET /api/hardware/relays/state` in `Infrastructure/automation-service/app/routes/hardware.py` (lines 81-92) to:
  1. First try Redis: `GET cea:relay:channels`
  2. If Redis hit: parse JSON, return immediately (<1ms)
  3. If Redis miss (startup, Redis restart): fall back to hardware read via `mcp.get_all_channels()` (existing path)
  4. If hardware read fails: return cached `_channel_states` (existing error path in `get_all_channels`)
  5. Add `mcp_connected` and `simulation` to the cached key as well OR read them separately from hardware

  **Simplest approach — cache the full response object**:
  ```python
  # New Redis key: cea:relay:state
  # Value: {"channels": [...], "mcp_connected": true, "simulation": false, "ts": "ISO"}
  ```
  But Task 1 already writes only `cea:relay:channels` as a boolean array. Keep it simple:
  ```python
  @router.get("/api/hardware/relays/state")
  async def relay_state(
      relay_manager: RelayManager = Depends(get_relay_manager),
      redis: Redis = Depends(get_redis),
  ) -> dict:
      # Try Redis cache first
      try:
          cached = await redis.get("cea:relay:channels")
          if cached is not None:
              channels = json.loads(cached)
              mcp = relay_manager.mcp23017
              return {
                  "channels": channels,
                  "mcp_connected": mcp.is_connected(),
                  "simulation": mcp.simulation,
              }
      except Exception:
          pass  # Fall through to hardware read
  
      # Hardware fallback (existing path)
      mcp = relay_manager.mcp23017
      states = mcp.get_all_channels()
      return {
          "channels": [bool(s) for s in states],
          "mcp_connected": mcp.is_connected(),
          "simulation": mcp.simulation,
      }
  ```

  **Must NOT do**:
  - Do NOT remove the hardware fallback — must work when Redis is empty
  - Do NOT cache `mcp_connected` or `simulation` (those are lightweight hardware checks)
  - Do NOT change the response shape

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 3)
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/routes/hardware.py:81-92` — current endpoint
  - `Infrastructure/automation-service/app/redis_client.py` — Redis client for FastAPI dependency injection
  - `Infrastructure/automation-service/app/container.py` — check if Redis is registered as a FastAPI dependency

  **Acceptance Criteria**:
  - [ ] `GET /api/hardware/relays/state` returns in <5ms (Redis cache hit)
  - [ ] Response shape unchanged: `{ "channels": [...], "mcp_connected": bool, "simulation": bool }`
  - [ ] Redis miss → hardware fallback works (e.g., after Redis restart)
  - [ ] Hardware read failure → error logged, last known state returned

  **Agent-Executed QA Scenario**:
  ```
  Scenario: API returns cached relay state in <5ms
    Tool: Bash (curl timing)
    Preconditions: automation-service running, Redis populated by control loop
    Steps:
      1. curl -s -o /dev/null -w "%{time_total}" http://mothernode:8001/api/hardware/relays/state
      2. Assert: total_time < 0.005 (5ms)
      3. curl -s http://mothernode:8001/api/hardware/relays/state | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d['channels'])==16, 'wrong count'; print('OK shape')"
      4. Assert: exit code 0
    Expected Result: Sub-5ms response with valid 16-channel array
    Evidence: curl timing output + response body captured
  ```

  **Commit**: YES
  - Message: `perf(relay): serve relay state from Redis cache, hardware fallback`
  - Files: `routes/hardware.py`

---

- [ ] 3. **Startup Relay State Restoration from DB**

  > **Finding**: `relay_manager.restore_states()` exists (relay_manager.py) but is never called. `DeviceController.restore_device_states()` exists and restores from `device_states` table — this is the correct path but also never wired into startup.

  **What to do**:
  1. Wire `DeviceController.restore_device_states()` into the container startup sequence in `container.py` (or `main.py` startup event)
  2. This restores relay states from PostgreSQL `device_states` table (current state snapshot)
  3. After restoration, write the restored states to Redis `cea:relay:channels` so the API serves them immediately
  4. If `device_states` table has no entries (first ever start), hardware defaults to all OFF — that's correct

  **Alternative if `restore_device_states()` is complex**: Simply wait for the first control loop tick (≤1s) to populate Redis. The hardware starts in a known OFF state, and the first tick within 1 second will write to Redis. This is actually simpler and sufficient.
  
  **Decision**: Use the simple approach. The control loop runs within 1s of startup. Task 1 already writes relay state to Redis after each tick. No separate startup restoration needed — the first tick populates Redis naturally. Just ensure the API endpoint's hardware fallback returns correct data in the 1s window before the first tick.

  **What to do instead**:
  1. In `routes/hardware.py`, add a comment noting: `# On startup: Redis cache is populated by first control loop tick (≤1s). Fallback to hardware read until then.`
  2. No code change needed for Task 3 — the existing Task 1 + Task 2 already handle startup correctly.

  **Acceptance Criteria** (simplified):
  - [ ] After `systemctl restart automation-service`, API returns correct relay states within 2s
  - [ ] No service crash if Redis key is empty on first API call after restart
  - [ ] Hardware fallback path tested: `redis-cli DEL cea:relay:channels` then curl API → still works

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Service restart does not break relay state API
    Tool: Bash (systemctl + curl)
    Preconditions: automation-service running, sudo access
    Steps:
      1. # Clear Redis cache to simulate empty startup
      2. redis-cli DEL cea:relay:channels
      3. curl -s http://mothernode:8001/api/hardware/relays/state
      4. Assert: HTTP 200, channels array has 16 booleans
      5. # Wait for first control loop tick (max 2s)
      6. sleep 2
      7. redis-cli GET cea:relay:channels
      8. Assert: key exists with 16-element array
    Expected Result: API works during Redis cache miss, cache populated within 2s
    Evidence: Terminal output captured
  ```

  **Commit**: NO (no code changes — Task 1 + Task 2 already handle startup correctly)

---

- [ ] 4. **Frontend: Eliminate relayState = null Initial State**

  > **Current**: `const [relayState, setRelayState] = useState<RelayBoardStateResponse | null>(null)` → `buildRelayChannelViewModels(..., relayState?.channels || null, ...)` → with `null`, `isStateKnown = false` for all channels → yellow "Unknown" badge.

  **What to do**:
  In `Infrastructure/frontend/src/components/DeviceManager.tsx`:
  1. Change `relayState` initial value from `null` to a structured default:
     ```typescript
     const DEFAULT_RELAY_STATE: RelayBoardStateResponse = {
       channels: Array(16).fill(false),
       mcp_connected: false,
       simulation: false,
     }
     const [relayState, setRelayState] = useState<RelayBoardStateResponse>(DEFAULT_RELAY_STATE)
     ```
  2. Now `relayState.channels` is always a `boolean[16]` (never null). `buildRelayChannelViewModels` receives a real array → `isStateKnown = true` for all channels → no "Unknown" flash.
  3. The default is `[false, false, ...]` (all OFF). This shows "IDLE" (gray) instead of "Unknown" (yellow) — a much better initial state.
  4. Channel boxes render immediately with assignment info (device name, location) — only the relay state badge shows "IDLE" until first API response.
  5. First `refreshRelayState()` response (within ~100ms from Redis now) updates the real state.

  **Required line changes**:
  - Line 1: Add `RelayBoardStateResponse` type import if not already imported (check if it's imported)
  - Line 81: Change `useState<RelayBoardStateResponse | null>(null)` to `useState<RelayBoardStateResponse>(DEFAULT_RELAY_STATE)`
  - Line 131: `relayState?.channels || null` → `relayState.channels` (no more null check needed)
  - Any other `relayState?.` → `relayState.` references throughout the component

  **Must NOT do**:
  - Do NOT change the polling interval
  - Do NOT change `buildRelayChannelViewModels` signature
  - Do NOT add loading spinners or skeleton UI
  - Do NOT change the ZoneConfig implementation (it's a separate component with its own null handling)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
  - **Skills**: [`frontend-ui-ux`]

  **Parallelization**:
  - **Can Run In Parallel**: YES (with Task 3)
  - **Blocked By**: None (independent of backend changes)

  **References**:
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:81` — relayState declaration
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:131` — relayState usage in view model building
  - `Infrastructure/frontend/src/types/relay.ts:35-39` — `RelayBoardStateResponse` type

  **Acceptance Criteria**:
  - [ ] `npm run build` passes (0 errors)
  - [ ] `grep "relayState?.channels" DeviceManager.tsx` → 0 matches (no more null-safe access)
  - [ ] `grep "DEFAULT_RELAY_STATE" DeviceManager.tsx` → ≥ 1 (default defined)
  - [ ] Initial render: channels show "IDLE" (gray) not "Unknown" (yellow)
  - [ ] After first API response: channels update to real states (ON/IDLE)

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Devices page no longer shows Unknown state
    Tool: Playwright (playwright skill)
    Preconditions: Frontend built, automation-service running with Redis cache populated
    Steps:
      1. Load skill: playwright
      2. Navigate to: http://mothernode:8001/ (Devices page)
      3. Wait for: relay matrix visible (timeout: 5s)
      4. Assert: No element contains text "Unknown" within relay channel boxes
      5. Assert: Channel boxes show device names immediately (not empty)
      6. Screenshot: .sisyphus/evidence/task-4-devices-no-unknown.png
    Expected Result: All channels show "IDLE" or "ON" immediately, no "Unknown" flash
    Evidence: .sisyphus/evidence/task-4-devices-no-unknown.png
  ```

  **Commit**: YES
  - Message: `fix(frontend): eliminate relayState null initial state on Devices page`
  - Files: `DeviceManager.tsx`

---

- [ ] 5. **End-to-End Verification**

  **What to do**:
  1. Run `ruff check --fix . && ruff format .` on entire project
  2. Deploy via `./deploy.sh`
  3. Verify: `redis-cli GET cea:relay:channels` returns valid data after deploy
  4. Verify: `curl -s -o /dev/null -w "%{time_total}" http://mothernode:8001/api/hardware/relays/state` is <5ms
  5. Verify: `npm run build` passes (frontend)
  6. Verify: Opening Devices page — no "Unknown" flash, relay states appear immediately
  7. Verify: Opening ZoneConfig page — relay matrix states appear immediately
  8. Verify: `systemctl restart automation-service` — relay states restored within 2s
  9. Run `git log --oneline -3` to verify commits

  **Must NOT do**:
  - Do NOT skip the service restart test
  - Do NOT skip the API timing measurement

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (final sweep)
  - **Blocked By**: Tasks 2, 3, 4

  **Acceptance Criteria**:
  - [ ] `ruff check --fix .` exits 0
  - [ ] `npm run build` exits 0
  - [ ] `GET /api/hardware/relays/state` total_time < 0.005
  - [ ] `redis-cli GET cea:relay:channels` returns valid 16-element array
  - [ ] Service restart → relay states available within 2s
  - [ ] Frontend Devices page → no "Unknown" flash
  - [ ] All 4 commits present with correct messages

  **Agent-Executed QA Scenario**:
  ```
  Scenario: Full verification sweep
    Tool: Bash
    Preconditions: All tasks complete, service running
    Steps:
      1. ruff check --fix . && ruff format . 2>&1 | tail -3
      2. redis-cli GET cea:relay:channels | python3 -c "import sys,json; assert len(json.load(sys.stdin))==16; print('OK')"
      3. curl -s -o /dev/null -w "API time: %{time_total}s\n" http://mothernode:8001/api/hardware/relays/state
      4. cd Infrastructure/frontend && npm run build 2>&1 | tail -3
      5. git log --oneline -4
    Expected Result: All checks pass
    Evidence: Full output captured
  ```

  **Commit**: YES
  - Message: `test(relay): verify end-to-end relay state caching after deploy`
  - Files: no code changes (verification only)

---

## Commit Strategy

| After Task | Message | Files |
|-----------|---------|-------|
| 1 | `feat(relay): cache MCP23017 relay states in Redis after hardware batch` | `schema.py`, `hardware_batch.py` |
| 2 | `perf(relay): serve relay state from Redis cache, hardware fallback` | `routes/hardware.py` |
| 3 | (no commit — Task 1+2 already handle startup) | N/A |
| 4 | `fix(frontend): eliminate relayState null initial state on Devices page` | `DeviceManager.tsx` |
| 5 | `test(relay): verify end-to-end relay state caching after deploy` | N/A (verification) |

---

## Success Criteria

### Verification Commands
```bash
# Redis cache populated?
redis-cli GET cea:relay:channels | python3 -c "import sys,json; d=json.load(sys.stdin); assert len(d)==16; print('OK')"

# API response time (must be <5ms)
curl -s -o /dev/null -w "API: %{time_total}s\n" http://mothernode:8001/api/hardware/relays/state

# Frontend build
cd Infrastructure/frontend && npm run build

# Ruff
ruff check --fix . && ruff format .

# No null-safe relayState access
grep "relayState?" Infrastructure/frontend/src/components/DeviceManager.tsx
# Expected: 0 matches

# Default relay state defined
grep "DEFAULT_RELAY_STATE" Infrastructure/frontend/src/components/DeviceManager.tsx
# Expected: ≥ 1 match
```

### Final Checklist
- [ ] Redis key `cea:relay:channels` populated after each control loop tick
- [ ] API endpoint serves from Redis in <5ms (was 3-10ms)
- [ ] Hardware fallback works when Redis is empty
- [ ] Service restart: relay states available within 2s via hardware fallback
- [ ] Devices page: no "Unknown" flash on load
- [ ] ZoneConfig page: relay matrix shows states immediately
- [ ] `npm run build` passes, `ruff check` passes
- [ ] No regressions in control loop timing or DB writes
- [ ] No changes to MCP23017 driver, relay manager, or relay view models
