# Mode Switch Performance Fix

## TL;DR

> **Quick Summary**: Fix tremendous lag and errors when switching room modes (veg → flower) by batching sequential database calls into a single transaction, caching static lookups, and adding missing WebSocket broadcast.
> 
> **Deliverables**:
> - Batched mode switch transaction (11-15 queries → 3-4 queries)
> - Mode ID caching layer for static reference data
> - WebSocket broadcast integration (existing `broadcast_mode_update()` is dead code)
> - Control loop transition handling to prevent cascade
> - Timing instrumentation for verification
> 
> **Estimated Effort**: Medium (8-12 hours)
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 1 (Baseline) → Task 2 (Transaction) → Task 3 (Cache) → Task 5 (Verification)

---

## Context

### Original Request
User reported "tremendous lag and errors" when switching from veg mode to flower mode in the Flower Room.

### Interview Summary
**Key Findings**:
- Mode switch endpoint makes 11-15 sequential database queries
- Each repository method acquires its own connection (no reuse)
- No database transaction wrapping the operation (not atomic)
- `broadcast_mode_update()` exists at `websocket.py:184` but is NEVER called (dead code)
- Control loop detects mode change and triggers additional DB queries via `_calculate_ramp_start_values()`
- Connection pool: min=2, max=10 (potential contention)

**Research Findings**:
- `set_room_mode` endpoint at `routes/room_modes.py:189-211`
- Repository at `repositories/room_modes.py` with N+1 pattern
- `setpoint_manager._detect_mode_change()` triggers cascade at line 374
- Transaction pattern exists at `database.py:177` (`async with conn.transaction()`)

### Metis Review
**Identified Gaps** (addressed):
- Correction: `broadcast_mode_update()` EXISTS but isn't called (not "doesn't exist")
- Need latency target: <500ms for mode switch
- Need atomicity: transaction rollback on failure
- Need concurrent switch handling: debounce/mutex
- Need control loop behavior during transition: don't stop, but handle gracefully

---

## Work Objectives

### Core Objective
Reduce mode switch latency from current (likely 1-3 seconds with errors) to <500ms with zero errors, while maintaining control loop integrity.

### Concrete Deliverables
- `Infrastructure/automation-service/app/repositories/room_modes.py` - Refactored with transaction batching
- `Infrastructure/automation-service/app/routes/room_modes.py` - Add broadcast call, connection reuse
- `Infrastructure/automation-service/app/database.py` - Add mode ID cache
- `Infrastructure/automation-service/app/control/setpoint_manager.py` - Add transition flag handling

### Definition of Done
- [ ] Mode switch completes in <500ms (verified via curl timing)
- [ ] Zero errors in journalctl during mode switch
- [ ] WebSocket `mode_update` message received by frontend
- [ ] Control loop continues uninterrupted during switch
- [ ] Database query count reduced by ≥50%

### Must Have
- Single database transaction for mode switch (atomic)
- Cached mode_id/submode_id lookups
- `broadcast_mode_update()` called after successful switch
- <500ms latency target

### Must NOT Have (Guardrails)
- No changes to `mode_parameters` table schema
- No changes to control loop timing (1-5s tick is non-negotiable)
- No new REST endpoints
- No refactoring of WebSocket infrastructure beyond adding the call
- No optimization of unrelated database queries
- Control loop must NEVER stop during mode switch

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: YES (pytest, existing tests in `tests/`)
- **Automated tests**: Tests-after (unit tests for new transaction logic)
- **Framework**: pytest

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

**Verification Tool by Deliverable Type:**

| Type | Tool | How Agent Verifies |
|------|------|-------------------|
| **API Performance** | Bash (curl with timing) | Measure response time, assert <500ms |
| **Database Queries** | Bash (enable pg_stat_statements or logging) | Count queries per operation |
| **WebSocket** | Playwright | Subscribe to WS, trigger mode switch, verify message |
| **Control Loop** | Bash (Redis monitoring) | Check device state updates continue during switch |
| **Logs** | Bash (journalctl) | Verify no ERROR/WARN during switch |

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
├── Task 1: Add timing instrumentation [no dependencies]
└── Task 4: Add WebSocket broadcast call [no dependencies]

Wave 2 (After Wave 1):
├── Task 2: Batch into transaction [depends: 1]
└── Task 3: Add mode ID caching [depends: 1]

Wave 3 (After Wave 2):
└── Task 5: Final verification [depends: 2, 3, 4]

Critical Path: Task 1 → Task 2 → Task 5
Parallel Speedup: ~30% faster than sequential
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3, 5 | 4 |
| 2 | 1 | 5 | 3 |
| 3 | 1 | 5 | 2 |
| 4 | None | 5 | 1 |
| 5 | 2, 3, 4 | None | None (final) |

---

## TODOs

- [x] 1. Add Timing Instrumentation (Baseline Measurement)

  **What to do**:
  - Add timing decorator/context manager to `set_room_mode` endpoint
  - Log individual operation times: get_active_mode, get_mode_parameters, save_mode_parameters, set_active_mode
  - Log total endpoint time
  - Capture baseline metrics (current latency)
  - Add query counter to verify current 11-15 queries

  **Must NOT do**:
  - Don't add permanent instrumentation to production (use env flag)
  - Don't modify business logic yet

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple instrumentation addition, single file focus
  - **Skills**: []
    - No special skills needed - standard Python async logging
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not needed for backend instrumentation

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 4)
  - **Blocks**: Tasks 2, 3, 5
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py:189-211` - Target endpoint for instrumentation
  - `Infrastructure/automation-service/app/utils/logging.py` - Existing logging patterns (if exists)

  **API/Type References**:
  - Python `time.perf_counter()` for high-resolution timing
  - `structlog` or standard `logging` module patterns in codebase

  **Why Each Reference Matters**:
  - The endpoint is the target; wrap each `await db.*` call with timing

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Baseline timing captured for mode switch
    Tool: Bash (curl + journalctl)
    Preconditions: automation-service running, timing instrumentation deployed
    Steps:
      1. curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" \
           -d '{"mode_name":"flower","submode_name":"bulk"}'
      2. journalctl -u automation-service -n 20 --no-pager | grep -E "timing|duration"
      3. Assert: Log entries show individual operation times
      4. Assert: Total duration logged (capture as baseline)
    Expected Result: Timing data visible in logs
    Evidence: journalctl output saved to .sisyphus/evidence/task-1-baseline-timing.txt
  ```

  **Commit**: YES
  - Message: `perf(automation): add timing instrumentation for mode switch baseline`
  - Files: `app/routes/room_modes.py`
  - Pre-commit: Service starts without error

---

- [x] 2. Batch Mode Switch Operations into Single Transaction

  **What to do**:
  - Create new method `switch_mode_atomic()` in `RoomModeRepository` that:
    - Acquires ONE connection
    - Opens a transaction (`async with conn.transaction()`)
    - Performs all operations within that transaction:
      - Get current mode (1 query)
      - Get current params (1 query, reusing mode_id)
      - Save current params (1 query)
      - Set new mode (1 query)
    - Returns complete result
  - Update `set_room_mode` endpoint to use new atomic method
  - Remove redundant `get_room_mode_with_params()` call at return (data already available)
  - Add rollback handling for failures

  **Must NOT do**:
  - Don't change method signatures that other code might call
  - Don't remove old methods yet (deprecate first)
  - Don't modify mode_parameters table schema

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Core database refactoring requiring careful async transaction handling
  - **Skills**: []
    - Standard Python/asyncpg - no special skills needed
  - **Skills Evaluated but Omitted**:
    - `git-master`: Standard commits, not complex git operations

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Task 1)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1 (need baseline first)

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/database.py:177` - Transaction pattern: `async with conn.transaction():`
  - `Infrastructure/automation-service/app/repositories/room_modes.py:64-97` - Current `set_active_mode` implementation
  - `Infrastructure/automation-service/app/repositories/room_modes.py:99-162` - Current `get_mode_parameters`
  - `Infrastructure/automation-service/app/repositories/room_modes.py:164-328` - Current `save_mode_parameters`

  **API/Type References**:
  - `asyncpg` transaction docs: https://magicstack.github.io/asyncpg/current/api/index.html#transactions
  - `SetModeRequest` model in routes for input validation

  **Why Each Reference Matters**:
  - database.py:177 shows the exact transaction pattern to follow
  - Repository methods show current query structure to consolidate

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Mode switch uses single transaction (query count reduced)
    Tool: Bash (curl + timing)
    Preconditions: Transaction refactor deployed
    Steps:
      1. time curl -s -o /dev/null -w "%{time_total}" -X POST \
           http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" \
           -d '{"mode_name":"veg"}'
      2. Assert: Response time < 0.500 seconds
      3. Check logs: journalctl -u automation-service -n 10 | grep "transaction"
      4. Assert: Single transaction logged (not 11-15 separate queries)
    Expected Result: Mode switch in <500ms with single transaction
    Evidence: Timing output saved to .sisyphus/evidence/task-2-transaction-timing.txt

  Scenario: Transaction rolls back on failure
    Tool: Bash (curl with invalid data)
    Preconditions: Transaction refactor deployed
    Steps:
      1. curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" \
           -d '{"mode_name":"invalid_mode_xxx"}'
      2. Assert: HTTP status 4xx or 5xx
      3. Verify mode unchanged: curl http://localhost:8001/api/room/Flower%20Room/main/mode
      4. Assert: Previous mode still active (no partial state)
    Expected Result: Invalid mode rejected, no partial writes
    Evidence: Response bodies captured
  ```

  **Commit**: YES
  - Message: `perf(automation): batch mode switch into single atomic transaction`
  - Files: `app/repositories/room_modes.py`, `app/routes/room_modes.py`
  - Pre-commit: `pytest tests/test_room_modes.py -v` (if exists)

---

- [x] 3. Add Mode ID Caching Layer

  **What to do**:
  - Add cache dict to `DatabaseManager` for mode_id and submode_id lookups
  - Cache is populated on first lookup, never expires (static reference data)
  - Add `get_mode_id_cached(mode_name)` and `get_submode_id_cached(submode_name)` methods
  - Update repository to use cached lookups instead of repeated SELECTs
  - Cache structure: `{"veg": 1, "flower": 2, ...}` for modes, similar for submodes

  **Must NOT do**:
  - Don't add Redis caching (overkill for static data)
  - Don't add TTL (these values never change)
  - Don't cache mode_parameters (those are dynamic)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple in-memory caching, minimal code changes
  - **Skills**: []
    - Standard Python dict caching
  - **Skills Evaluated but Omitted**:
    - None applicable

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Task 2)
  - **Blocks**: Task 5
  - **Blocked By**: Task 1 (need baseline first)

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/database.py:_query_cache` - Existing 30s TTL cache (but mode IDs should be permanent)
  - `Infrastructure/automation-service/app/repositories/room_modes.py:70-71` - Query: `SELECT id FROM room_modes WHERE name = $1`
  - `Infrastructure/automation-service/app/repositories/room_modes.py:78-80` - Query: `SELECT id FROM flower_submodes WHERE name = $1`

  **Why Each Reference Matters**:
  - Shows existing cache pattern (but we need simpler, permanent cache)
  - Shows exact queries to eliminate via caching

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Mode ID cache populated on startup
    Tool: Bash (service logs)
    Preconditions: Caching deployed, service restarted
    Steps:
      1. systemctl restart automation-service
      2. sleep 3
      3. journalctl -u automation-service -n 30 | grep -i "cache"
      4. Assert: Log shows mode IDs cached (e.g., "Cached 3 mode IDs")
    Expected Result: Cache populated at startup
    Evidence: Log output captured

  Scenario: Subsequent lookups use cache (no DB query)
    Tool: Bash (mode switch + log analysis)
    Preconditions: Cache populated
    Steps:
      1. Switch mode twice in succession:
         curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" -d '{"mode_name":"flower"}'
         curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" -d '{"mode_name":"veg"}'
      2. Check logs for "SELECT id FROM room_modes" queries
      3. Assert: No mode_id lookup queries (cache hit)
    Expected Result: Zero mode_id SELECTs after initial cache population
    Evidence: Query log analysis saved
  ```

  **Commit**: YES
  - Message: `perf(automation): add mode ID caching for static reference data`
  - Files: `app/database.py`, `app/repositories/room_modes.py`
  - Pre-commit: Service starts without error

---

- [x] 4. Add WebSocket Broadcast Call

  **What to do**:
  - Import `broadcast_mode_update` from `websocket.py` in routes
  - Call `await broadcast_mode_update(location, cluster, new_mode_data)` after successful mode switch
  - Verify broadcast_mode_update function signature and update if needed
  - Handle broadcast failure gracefully (log warning, don't fail the switch)

  **Must NOT do**:
  - Don't refactor WebSocket infrastructure
  - Don't add new broadcast types
  - Don't make broadcast failure block the mode switch

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single function call addition
  - **Skills**: []
    - Standard Python import and async call
  - **Skills Evaluated but Omitted**:
    - `playwright`: Used for verification, not implementation

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Task 5
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/routes/websocket.py:184` - Existing `broadcast_mode_update()` function (currently dead code)
  - `Infrastructure/automation-service/app/routes/room_modes.py:211` - Return statement where broadcast should be added
  - `Infrastructure/automation-service/app/routes/schedules.py` - Example of `broadcast_schedule_update()` usage pattern

  **Why Each Reference Matters**:
  - websocket.py:184 has the function signature we need to call
  - schedules.py shows the pattern of calling broadcast after successful update

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: WebSocket broadcast sent after mode switch
    Tool: Playwright (playwright skill)
    Preconditions: Dev server running, WebSocket endpoint available
    Steps:
      1. Navigate to: http://localhost:8001 (or connect to WS directly)
      2. Open browser DevTools Network tab, filter WebSocket
      3. Connect to WebSocket endpoint
      4. In separate terminal: curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" -d '{"mode_name":"flower"}'
      5. Assert: WebSocket message received with type "mode_update"
      6. Assert: Message contains location "Flower Room" and mode_name "flower"
      7. Screenshot: .sisyphus/evidence/task-4-websocket-broadcast.png
    Expected Result: Frontend receives real-time mode update
    Evidence: .sisyphus/evidence/task-4-websocket-broadcast.png

  Scenario: Broadcast failure doesn't block mode switch
    Tool: Bash (simulate broadcast failure)
    Preconditions: Add temporary error injection to broadcast function
    Steps:
      1. Temporarily make broadcast_mode_update raise an exception
      2. curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode \
           -H "Content-Type: application/json" -d '{"mode_name":"veg"}'
      3. Assert: HTTP status 200 (switch succeeded despite broadcast failure)
      4. Assert: Warning logged about broadcast failure
    Expected Result: Mode switch succeeds even if broadcast fails
    Evidence: Response and logs captured
  ```

  **Commit**: YES
  - Message: `fix(automation): call broadcast_mode_update after successful mode switch`
  - Files: `app/routes/room_modes.py`
  - Pre-commit: Service starts without error

---

- [x] 5. Final Verification and Cleanup

  **What to do**:
  - Run complete mode switch test suite
  - Verify <500ms latency target met
  - Verify zero errors in logs
  - Verify WebSocket broadcast received
  - Verify control loop continues during switch
  - Remove timing instrumentation (or gate behind debug flag)
  - Update any relevant documentation

  **Must NOT do**:
  - Don't add new features
  - Don't optimize further (scope lock)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Verification and cleanup, no complex logic
  - **Skills**: [`playwright`]
    - `playwright`: Needed for WebSocket verification test
  - **Skills Evaluated but Omitted**:
    - `git-master`: Simple commit, not complex git operations

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (final, sequential)
  - **Blocks**: None (final task)
  - **Blocked By**: Tasks 2, 3, 4

  **References**:

  **Pattern References**:
  - All previous task deliverables
  - `Infrastructure/automation-service/tests/` - Existing test patterns

  **Acceptance Criteria**:

  **Agent-Executed QA Scenarios:**

  ```
  Scenario: Mode switch latency under 500ms
    Tool: Bash (curl timing, 5 iterations)
    Preconditions: All fixes deployed
    Steps:
      1. for i in {1..5}; do
           time curl -s -o /dev/null -w "%{time_total}\n" -X POST \
             http://localhost:8001/api/room/Flower%20Room/main/mode \
             -H "Content-Type: application/json" \
             -d "{\"mode_name\":\"$([ $((i % 2)) -eq 0 ] && echo veg || echo flower)\"}"
         done
      2. Assert: All 5 responses < 0.500 seconds
      3. Calculate average latency
    Expected Result: Average < 300ms, max < 500ms
    Evidence: Timing results saved to .sisyphus/evidence/task-5-final-timing.txt

  Scenario: Zero errors during mode switch
    Tool: Bash (journalctl)
    Preconditions: Mode switch test just completed
    Steps:
      1. journalctl -u automation-service --since "1 minute ago" | grep -E "(ERROR|CRITICAL)"
      2. Assert: No ERROR or CRITICAL logs
    Expected Result: Clean log output
    Evidence: Log output saved

  Scenario: Control loop continues during mode switch
    Tool: Bash (Redis monitoring)
    Preconditions: Control loop running
    Steps:
      1. Start monitoring Redis device states:
         redis-cli MONITOR > /tmp/redis-monitor.log &
      2. Trigger mode switch
      3. Wait 10 seconds
      4. Kill redis monitor
      5. grep "device_states" /tmp/redis-monitor.log | wc -l
      6. Assert: Count > 0 (device states updated during switch period)
    Expected Result: Control loop never paused
    Evidence: Redis monitor log saved
  ```

  **Commit**: YES
  - Message: `chore(automation): finalize mode switch performance optimization`
  - Files: Any cleanup changes
  - Pre-commit: Full test suite passes

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `perf(automation): add timing instrumentation for mode switch baseline` | routes/room_modes.py | Service starts |
| 2 | `perf(automation): batch mode switch into single atomic transaction` | repositories/room_modes.py, routes/room_modes.py | pytest |
| 3 | `perf(automation): add mode ID caching for static reference data` | database.py, repositories/room_modes.py | Service starts |
| 4 | `fix(automation): call broadcast_mode_update after successful mode switch` | routes/room_modes.py | Service starts |
| 5 | `chore(automation): finalize mode switch performance optimization` | Cleanup | Full suite |

---

## Success Criteria

### Verification Commands
```bash
# Mode switch latency
time curl -s -o /dev/null -w "%{time_total}" -X POST \
  http://localhost:8001/api/room/Flower%20Room/main/mode \
  -H "Content-Type: application/json" \
  -d '{"mode_name":"flower","submode_name":"bulk"}'
# Expected: < 0.500

# No errors
journalctl -u automation-service --since "5 minutes ago" | grep -c ERROR
# Expected: 0

# WebSocket test (manual verification with Playwright)
# Expected: mode_update message received

# Control loop active
redis-cli GET "device_states:Flower Room:main" | jq .
# Expected: Recent timestamp
```

### Final Checklist
- [ ] Mode switch < 500ms (was 1-3 seconds)
- [ ] Database queries reduced by ≥50% (was 11-15, now 3-4)
- [ ] Single atomic transaction (was no transaction)
- [ ] WebSocket broadcast sent (was missing)
- [ ] Zero errors during switch
- [ ] Control loop uninterrupted
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
