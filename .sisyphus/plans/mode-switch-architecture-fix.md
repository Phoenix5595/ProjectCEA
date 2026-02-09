# Mode Switch Architecture Fix - Comprehensive Plan

## TL;DR

> **Quick Summary**: Fix fundamental architectural gaps where mode switching doesn't sync climate setpoints, light schedules, or properly coordinate state across tables. Unify mode detection to single source of truth and add observability.
> 
> **Deliverables**:
> - Unified mode sync service that syncs ALL mode-related parameters atomically
> - Climate setpoint sync on mode switch (the missing piece)
> - Light schedule sync on mode switch (original bug)
> - Single source of truth for mode detection
> - Multi-cluster coordination enforcement
> - Cache invalidation on mode switch
> - Observability: audit table, debug endpoints, improved logging
> 
> **Estimated Effort**: Large (3-5 days)
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: Task 1 → Task 3 → Task 5 → Task 8 → Task 11

---

## Context

### Original Request
User reported light ramp times didn't sync when switching from flower mode (15min ramp) to veg mode (60min ramp). Investigation revealed this is a symptom of a much larger architectural gap.

### Investigation Summary
**Key Findings**:
1. **Climate setpoints don't sync on mode switch** - `mode_parameters` table (UI) is completely disconnected from `setpoints` table (ControlEngine reads)
2. **Dual mode detection systems** - UI uses `room_active_mode` table, ControlEngine derives mode from light schedules
3. **Light schedules don't sync on mode switch** - Original bug, schedules table not updated
4. **Multi-cluster desync risk** - Clusters in same room can end up in different modes
5. **Cache not invalidated** - SetpointRepository cache persists old values
6. **No observability** - No audit trail, no debug endpoints, minimal logging

**Research Findings**:
- `sync_room_schedule_from_mode_parameters` exists for light schedules (room.py:557-591)
- No equivalent exists for climate setpoints
- SetpointRepository has internal cache (line 113) not invalidated on mode switch
- PID integrator reset works correctly (pid_controller_manager.py:330-338)
- Scheduler refresh is delayed 60 seconds (background_tasks.py)

### Metis Review
**Identified Gaps** (addressed in this plan):
- Transaction atomicity for multi-table updates
- Immediate scheduler/control refresh after sync
- Frontend WebSocket notification
- Cache invalidation strategy

---

## Work Objectives

### Core Objective
Create a unified, atomic mode switch operation that properly syncs ALL mode-related parameters (climate setpoints, light schedules, ramp times) to their respective control tables, with single source of truth for mode detection and full observability.

### Concrete Deliverables
1. `ModeTransitionService` - orchestrates atomic mode transitions
2. `sync_climate_setpoints_from_mode_parameters()` - new function for setpoint sync
3. Enhanced `set_mode_with_transaction()` - includes all syncs atomically
4. `mode_transition_history` table - audit trail
5. Debug endpoints - `/api/debug/mode-state`, `/api/debug/ramps`, `/api/debug/scheduler`
6. Cache invalidation on mode switch
7. Immediate scheduler/control refresh
8. Multi-cluster coordination enforcement
9. Integration tests for full flow

### Definition of Done
- [x] `bun test` passes with new integration tests
- [x] Mode switch syncs climate setpoints to `setpoints` table
- [x] Mode switch syncs light schedules to `schedules` table
- [x] SetpointRepository cache invalidated on mode switch
- [x] Scheduler immediately refreshes after mode switch
- [x] All clusters in room switch together atomically
- [x] `mode_transition_history` records all mode changes
- [x] Debug endpoints return current state for troubleshooting

### Must Have
- Atomic transaction for all syncs (all succeed or all fail)
- Immediate effect (no 60-second delay)
- Cache invalidation
- Multi-cluster coordination
- Audit trail

### Must NOT Have (Guardrails)
- Breaking changes to existing API contracts
- Changes to `mode_parameters` table schema
- Changes to how UI writes mode parameters
- Modifications to PID tuning logic
- Changes to sensor reading logic
- New external dependencies

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: YES (pytest in tests/)
- **Automated tests**: YES (Tests-after for most, TDD for critical sync logic)
- **Framework**: pytest + pytest-asyncio

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

Each task includes detailed QA scenarios using:
- **API Testing**: curl/httpie for endpoint verification
- **Database Verification**: psql queries to confirm data changes
- **Log Inspection**: journalctl for log verification
- **Redis Inspection**: redis-cli for cache state

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation - Start Immediately):
├── Task 1: Create mode_transition_history table (database)
├── Task 2: Add sync_climate_setpoints_from_mode_parameters function
└── Task 6: Add cache invalidation method to SetpointRepository

Wave 2 (After Wave 1):
├── Task 3: Create ModeTransitionService orchestrator
├── Task 7: Fix light schedule ambiguity (LIMIT 1 issue)
└── Task 9: Add debug endpoints

Wave 3 (After Wave 2):
├── Task 4: Integrate ModeTransitionService into set_room_mode route
├── Task 5: Add immediate scheduler refresh
└── Task 8: Add multi-cluster coordination enforcement

Wave 4 (After Wave 3):
├── Task 10: Fix code/comment contradictions
├── Task 11: Integration tests for full flow
└── Task 12: Elevate log levels for schedule/mode changes

Critical Path: Task 1 → Task 3 → Task 4 → Task 5 → Task 11
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 3, 9 | 2, 6 |
| 2 | None | 3 | 1, 6 |
| 3 | 1, 2 | 4 | 7, 9 |
| 4 | 3 | 5, 8 | None |
| 5 | 4 | 11 | 8 |
| 6 | None | 4 | 1, 2 |
| 7 | None | 11 | 3, 9 |
| 8 | 4 | 11 | 5 |
| 9 | 1 | 11 | 3, 7 |
| 10 | None | None | Any |
| 11 | 5, 7, 8, 9 | None | 12 |
| 12 | None | None | Any |

---

## TODOs

### PHASE 1: FOUNDATION

- [x] 1. Create mode_transition_history audit table

  **What to do**:
  - Create migration SQL for `mode_transition_history` table
  - Columns: id, location, cluster, old_mode_id, old_submode_id, new_mode_id, new_submode_id, triggered_by (api/schedule/system), triggered_at, parameters_synced (jsonb), success, error_message
  - Add indexes on (location, cluster, triggered_at)
  - Apply migration to database

  **Must NOT do**:
  - Modify existing tables
  - Add foreign key constraints that could slow mode switches

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Simple SQL migration, no complex logic

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 2, 6)
  - **Blocks**: Tasks 3, 9
  - **Blocked By**: None

  **References**:
  - `Infrastructure/database/cea_schema.sql` - Existing schema patterns
  - `Infrastructure/database/migrations/` - Migration file patterns
  - `Infrastructure/automation-service/app/repositories/room_modes.py:1-50` - RoomModeRepository patterns

  **Acceptance Criteria**:
  - [ ] Migration file created: `Infrastructure/database/migrations/XXX_add_mode_transition_history.sql`
  - [ ] Table exists after migration: `psql -U cea -d projectcea -c "\d mode_transition_history"` shows table
  - [ ] Indexes exist: `psql -U cea -d projectcea -c "\di" | grep mode_transition` shows indexes

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Table created with correct schema
    Tool: Bash (psql)
    Preconditions: Database accessible
    Steps:
      1. psql -U cea -d projectcea -c "\d mode_transition_history"
      2. Assert: Columns include id, location, cluster, old_mode_id, new_mode_id, triggered_by, triggered_at, success
      3. Assert: triggered_at has default NOW()
    Expected Result: Table exists with all required columns
    Evidence: psql output captured

  Scenario: Indexes created for query performance
    Tool: Bash (psql)
    Preconditions: Migration applied
    Steps:
      1. psql -U cea -d projectcea -c "SELECT indexname FROM pg_indexes WHERE tablename = 'mode_transition_history'"
      2. Assert: Index on (location, cluster, triggered_at) exists
    Expected Result: Required indexes present
    Evidence: Query result captured
  ```

  **Commit**: YES
  - Message: `feat(database): add mode_transition_history audit table`
  - Files: `Infrastructure/database/migrations/XXX_add_mode_transition_history.sql`

---

- [x] 2. Add sync_climate_setpoints_from_mode_parameters function

  **What to do**:
  - Create new function in `repositories/setpoints.py` or new file `repositories/mode_sync.py`
  - Function: `async def sync_climate_setpoints_from_mode_parameters(conn, location, cluster, mode_id, submode_id) -> dict`
  - Read climate parameters from `mode_parameters` table: heating_setpoint_day, cooling_setpoint_day, humidity_day, co2_target, vpd_target, and night equivalents
  - Write to `setpoints` table for modes: day, night, pre_day, pre_night
  - Use same transaction/connection passed in (for atomicity)
  - Return dict of what was synced for audit logging

  **Must NOT do**:
  - Create new database connection (use passed connection)
  - Modify mode_parameters table
  - Change existing setpoint validation logic

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Core business logic, requires understanding of both tables

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 6)
  - **Blocks**: Task 3
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/repositories/setpoints.py:136-200` - set_setpoint pattern
  - `Infrastructure/automation-service/app/repositories/room_modes.py:100-200` - mode_parameters query patterns
  - `Infrastructure/automation-service/app/routes/schedules/room.py:557-591` - sync_room_schedule_from_mode_parameters pattern to follow
  - `Infrastructure/database/cea_schema.sql` - mode_parameters and setpoints table schemas

  **Acceptance Criteria**:
  - [ ] Function created in appropriate repository file
  - [ ] Function accepts connection object (not pool) for transaction atomicity
  - [ ] Function reads from mode_parameters WHERE mode_id=$1 AND submode_id=$2
  - [ ] Function writes to setpoints table for day/night/pre_day/pre_night modes
  - [ ] Function returns dict with synced values for audit trail
  - [ ] Unit test verifies correct data mapping

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Setpoints synced from mode_parameters
    Tool: Bash (pytest)
    Preconditions: Test database with mode_parameters data
    Steps:
      1. pytest tests/test_mode_sync.py::test_sync_climate_setpoints -v
      2. Assert: Test passes
      3. Assert: Setpoints table updated with mode_parameters values
    Expected Result: Climate setpoints correctly synced
    Evidence: pytest output captured

  Scenario: Transaction atomicity preserved
    Tool: Bash (pytest)
    Preconditions: Test with simulated failure mid-sync
    Steps:
      1. pytest tests/test_mode_sync.py::test_sync_rollback_on_failure -v
      2. Assert: On failure, no partial updates in setpoints table
    Expected Result: All-or-nothing sync behavior
    Evidence: pytest output captured
  ```

  **Commit**: YES
  - Message: `feat(automation): add sync_climate_setpoints_from_mode_parameters`
  - Files: `Infrastructure/automation-service/app/repositories/mode_sync.py`

---

- [x] 6. Add cache invalidation method to SetpointRepository

  **What to do**:
  - Add method `invalidate_cache_for_location_cluster(self, location: str, cluster: str)` to SetpointRepository
  - Method clears all cached entries matching location/cluster pattern
  - Add method `invalidate_all_cache(self)` for full cache clear
  - Ensure thread-safety if cache is shared

  **Must NOT do**:
  - Change cache TTL or caching strategy
  - Remove caching entirely
  - Modify get_setpoint or set_setpoint logic

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Simple cache method addition

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Tasks 1, 2)
  - **Blocks**: Task 4
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/repositories/setpoints.py:112-115` - Current caching implementation
  - `Infrastructure/automation-service/app/repositories/setpoints.py:13-50` - SetpointRepository class structure
  - `Infrastructure/automation-service/app/repositories/base.py` - BaseRepository cache methods if any

  **Acceptance Criteria**:
  - [ ] `invalidate_cache_for_location_cluster(location, cluster)` method added
  - [ ] Method clears cache entries matching location/cluster
  - [ ] Unit test verifies cache cleared after invalidation

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Cache invalidation clears relevant entries
    Tool: Bash (pytest)
    Preconditions: SetpointRepository with cached data
    Steps:
      1. pytest tests/test_setpoint_repository.py::test_cache_invalidation -v
      2. Assert: After invalidation, next get_setpoint hits database
    Expected Result: Cache properly cleared for location/cluster
    Evidence: pytest output captured
  ```

  **Commit**: YES (groups with Task 2)
  - Message: `feat(automation): add SetpointRepository cache invalidation`
  - Files: `Infrastructure/automation-service/app/repositories/setpoints.py`

---

### PHASE 2: ORCHESTRATION

- [x] 3. Create ModeTransitionService orchestrator

  **What to do**:
  - Create new file `Infrastructure/automation-service/app/services/mode_transition.py`
  - Class `ModeTransitionService` with method `async def execute_mode_transition(location, cluster, new_mode_name, new_submode_name=None, triggered_by="api") -> ModeTransitionResult`
  - Orchestrates in single transaction:
    1. Get current mode (for audit)
    2. Save current parameters to mode_parameters (existing logic)
    3. Set new active mode in room_active_mode
    4. Sync light schedules from new mode parameters
    5. Sync climate setpoints from new mode parameters
    6. Record transition in mode_transition_history
  - Return result with success status and what was synced
  - On any failure, entire transaction rolls back

  **Must NOT do**:
  - Call external services during transaction
  - Make network calls during transaction
  - Modify existing repository methods

  **Recommended Agent Profile**:
  - **Category**: `ultrabrain`
  - **Skills**: []
  - Reason: Complex orchestration logic with transaction management

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 7, 9)
  - **Blocks**: Task 4
  - **Blocked By**: Tasks 1, 2

  **References**:
  - `Infrastructure/automation-service/app/repositories/room_modes.py:350-527` - Existing set_mode_with_transaction logic to refactor
  - `Infrastructure/automation-service/app/routes/schedules/room.py:557-591` - sync_room_schedule_from_mode_parameters
  - `Infrastructure/automation-service/app/repositories/schedules.py:350-377` - update_light_schedule_ramp_times
  - Task 2 output: sync_climate_setpoints_from_mode_parameters function

  **Acceptance Criteria**:
  - [ ] ModeTransitionService class created
  - [ ] execute_mode_transition method uses single database transaction
  - [ ] All 6 steps execute in order within transaction
  - [ ] Rollback on any step failure
  - [ ] Returns ModeTransitionResult with details of what changed
  - [ ] Unit test for successful transition
  - [ ] Unit test for rollback on failure

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Full mode transition succeeds atomically
    Tool: Bash (pytest)
    Preconditions: Test database with valid mode configuration
    Steps:
      1. pytest tests/test_mode_transition_service.py::test_full_transition -v
      2. Assert: room_active_mode updated
      3. Assert: schedules table updated with new ramp times
      4. Assert: setpoints table updated with new climate targets
      5. Assert: mode_transition_history has new record
    Expected Result: All tables updated in single transaction
    Evidence: pytest output + database state verification

  Scenario: Transaction rollback on setpoint sync failure
    Tool: Bash (pytest)
    Preconditions: Mock setpoint sync to fail
    Steps:
      1. pytest tests/test_mode_transition_service.py::test_rollback_on_failure -v
      2. Assert: room_active_mode NOT updated
      3. Assert: schedules table unchanged
      4. Assert: No partial state in database
    Expected Result: Complete rollback, no partial updates
    Evidence: pytest output captured
  ```

  **Commit**: YES
  - Message: `feat(automation): add ModeTransitionService for atomic mode switches`
  - Files: `Infrastructure/automation-service/app/services/mode_transition.py`

---

- [x] 7. Fix ALL schedule query ambiguities (multiple LIMIT 1 issues)

  **What to do**:
  - **Metis identified 3 ambiguous queries** - fix ALL of them:
    1. `get_light_schedule` (schedules.py:104): Currently `ORDER BY target_intensity DESC LIMIT 1` - picks brightest, not most relevant
    2. `get_room_light_schedule` (schedules.py:139): Currently `ORDER BY id DESC LIMIT 1` - picks newest by ID
    3. `get_climate_schedule` (schedules.py:72): Currently `ORDER BY id DESC LIMIT 1` - picks latest metadata
  - For each query, add deterministic tie-breaker: `ORDER BY device_name ASC, id ASC LIMIT 1`
  - Add comment explaining selection logic for each query
  - Ensure consistent behavior across all schedule retrievals

  **Must NOT do**:
  - Remove the LIMIT 1 (needed for single schedule return)
  - Change schedule table schema
  - Break existing schedule queries

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Small query fixes with clear pattern

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 9)
  - **Blocks**: Task 11
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/repositories/schedules.py:72` - get_climate_schedule query
  - `Infrastructure/automation-service/app/repositories/schedules.py:104` - get_light_schedule query
  - `Infrastructure/automation-service/app/repositories/schedules.py:139` - get_room_light_schedule query
  - `Infrastructure/automation-service/app/control/scheduler.py` - How schedules are used

  **Acceptance Criteria**:
  - [ ] All 3 LIMIT 1 queries have deterministic ORDER BY with tie-breaker
  - [ ] Comments explain selection logic for each query
  - [ ] Same schedule always selected given same data (run 10x, same result)
  - [ ] grep -n "LIMIT 1" schedules.py shows all queries have proper ORDER BY

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Deterministic schedule selection across all query types
    Tool: Bash (psql + curl)
    Preconditions: Cluster with multiple schedules
    Steps:
      1. Call each schedule endpoint 10 times
      2. Assert: Same schedule returned every time for each endpoint
      3. Verify ordering: psql -c "SELECT device_name FROM schedules WHERE location='Flower Room' ORDER BY device_name ASC, id ASC LIMIT 1"
    Expected Result: Consistent, predictable schedule selection
    Evidence: API responses captured

  Scenario: Tie-breaker works when primary sort is equal
    Tool: Bash (psql)
    Preconditions: Two schedules with same target_intensity
    Steps:
      1. Insert test data with equal intensities
      2. Run get_light_schedule query
      3. Assert: Returns schedule with lower device_name (alphabetically first)
    Expected Result: id ASC tie-breaker produces consistent result
    Evidence: Query results captured
  ```

  **Commit**: YES
  - Message: `fix(automation): make all schedule queries deterministic with tie-breakers`
  - Files: `Infrastructure/automation-service/app/repositories/schedules.py`

---

- [x] 9. Add debug endpoints for troubleshooting

  **What to do**:
  - Create new router file `Infrastructure/automation-service/app/routes/debug.py`
  - Add endpoints:
    - `GET /api/debug/mode-state/{location}/{cluster}` - Returns current mode from all sources (room_active_mode, schedules-derived, mode_parameters)
    - `GET /api/debug/ramps/{location}/{cluster}` - Returns current ramp states from scheduler._light_ramp_state and setpoint ramp_manager
    - `GET /api/debug/scheduler/{location}/{cluster}` - Returns loaded schedules and next transitions
    - `GET /api/debug/setpoints/{location}/{cluster}` - Returns setpoints from both setpoints table AND mode_parameters for comparison
  - Include timestamps, cache status, refresh status
  - Add to main.py router includes

  **Must NOT do**:
  - Expose sensitive information
  - Allow write operations through debug endpoints
  - Add endpoints that would impact performance if called frequently

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
  - **Skills**: []
  - Reason: Standard API endpoints, straightforward implementation

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 2 (with Tasks 3, 7)
  - **Blocks**: Task 11
  - **Blocked By**: Task 1 (for mode_transition_history query)

  **References**:
  - `Infrastructure/automation-service/app/routes/` - Existing route patterns
  - `Infrastructure/automation-service/app/control/scheduler.py` - Scheduler state access
  - `Infrastructure/automation-service/app/control/setpoint_manager.py` - Ramp manager access
  - `Infrastructure/automation-service/app/main.py` - Router registration

  **Acceptance Criteria**:
  - [ ] All 4 debug endpoints created and registered
  - [ ] Endpoints return JSON with relevant state
  - [ ] No authentication required (internal debugging tool)
  - [ ] Endpoints don't modify state

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Debug mode-state endpoint works
    Tool: Bash (curl)
    Preconditions: Automation service running
    Steps:
      1. curl -s http://localhost:8001/api/debug/mode-state/Flower%20Room/main | jq
      2. Assert: Response contains room_active_mode, schedule_derived_mode, mode_parameters
      3. Assert: Status 200
    Expected Result: Complete mode state visibility
    Evidence: Response body captured

  Scenario: Debug ramps endpoint shows current ramp state
    Tool: Bash (curl)
    Preconditions: Light ramp in progress
    Steps:
      1. curl -s http://localhost:8001/api/debug/ramps/Flower%20Room/main | jq
      2. Assert: Response shows light_ramp_state entries
      3. Assert: Response shows setpoint_ramp_state entries
    Expected Result: Full ramp state visibility
    Evidence: Response body captured
  ```

  **Commit**: YES
  - Message: `feat(automation): add debug endpoints for mode/ramp/scheduler state`
  - Files: `Infrastructure/automation-service/app/routes/debug.py`, `Infrastructure/automation-service/app/main.py`

---

### PHASE 3: INTEGRATION

- [x] 4. Integrate ModeTransitionService into set_room_mode route

  **What to do**:
  - Modify `Infrastructure/automation-service/app/routes/room_modes.py` set_room_mode endpoint
  - Replace direct calls to set_mode_with_transaction with ModeTransitionService.execute_mode_transition
  - After successful transition:
    1. Invalidate SetpointRepository cache
    2. Broadcast WebSocket updates (room_mode_update, room_schedule_update)
  - Handle ModeTransitionResult and return appropriate API response
  - Keep backward compatibility with existing response format

  **Must NOT do**:
  - Change API request/response contract
  - Remove existing error handling
  - Break set_room_submode endpoint (should also use new service)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Integration work requiring understanding of multiple components

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential after Wave 2
  - **Blocks**: Tasks 5, 8
  - **Blocked By**: Tasks 3, 6

  **References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py:192-228` - Current set_room_mode endpoint
  - Task 3 output: ModeTransitionService
  - Task 6 output: SetpointRepository.invalidate_cache_for_location_cluster
  - `Infrastructure/automation-service/app/routes/websocket.py` - broadcast_room_mode_update, broadcast_room_schedule_update

  **Acceptance Criteria**:
  - [ ] set_room_mode uses ModeTransitionService
  - [ ] set_room_submode uses ModeTransitionService
  - [ ] Cache invalidation called after successful transition
  - [ ] WebSocket broadcasts sent after transition
  - [ ] API response format unchanged
  - [ ] Existing tests still pass

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Mode switch via API syncs all parameters
    Tool: Bash (curl + psql)
    Preconditions: Flower Room in "flower" mode with specific setpoints
    Steps:
      1. Record current setpoints: psql -c "SELECT * FROM setpoints WHERE location='Flower Room'"
      2. curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode -d '{"mode_name":"veg"}'
      3. Assert: Status 200
      4. psql -c "SELECT * FROM setpoints WHERE location='Flower Room'"
      5. Assert: Setpoints changed to veg mode values
      6. psql -c "SELECT * FROM schedules WHERE location='Flower Room'"
      7. Assert: Schedules updated with veg mode ramp times
      8. psql -c "SELECT * FROM mode_transition_history ORDER BY triggered_at DESC LIMIT 1"
      9. Assert: Transition recorded with success=true
    Expected Result: Full sync on mode switch
    Evidence: Before/after database state captured

  Scenario: WebSocket broadcast on mode switch
    Tool: Playwright (WebSocket listener)
    Preconditions: WebSocket connected to automation service
    Steps:
      1. Connect WebSocket to ws://localhost:8001/ws
      2. Trigger mode switch via API
      3. Assert: Received room_mode_update message
      4. Assert: Received room_schedule_update message
    Expected Result: Frontend notified of changes
    Evidence: WebSocket messages captured
  ```

  **Commit**: YES
  - Message: `feat(automation): integrate ModeTransitionService into mode endpoints`
  - Files: `Infrastructure/automation-service/app/routes/room_modes.py`

---

- [x] 5. Add SYNCHRONOUS scheduler refresh after mode switch (CRITICAL)

  **What to do**:
  - **CRITICAL**: Metis identified a 60-second race condition - scheduler refresh MUST be synchronous (blocking)
  - After ModeTransitionService completes, trigger immediate scheduler refresh
  - Access scheduler via container: `container.get_control_engine().scheduler`
  - Call `scheduler.refresh_schedules()` or `scheduler.update_schedules(new_schedules)` - MUST BLOCK until complete
  - Clear light ramp state: add `scheduler.clear_light_ramp_state(location, cluster)` method
  - Ensure this happens AFTER transaction commits but BEFORE API response returns
  - **Without this**: Up to 60 seconds of wrong climate control after every mode switch

  **Must NOT do**:
  - Modify the 60-second background refresh loop
  - Block on scheduler refresh failure (log error, continue)
  - Add scheduler refresh inside the database transaction

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Requires understanding of scheduler internals and container access

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 8)
  - **Blocks**: Task 11
  - **Blocked By**: Task 4

  **References**:
  - `Infrastructure/automation-service/app/control/scheduler.py:300-420` - Scheduler class and _light_ramp_state
  - `Infrastructure/automation-service/app/container.py` - Container access patterns
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:389-400` - _detect_mode_change pattern for clearing ramps
  - `Infrastructure/automation-service/app/background_tasks.py` - Background refresh loop

  **Acceptance Criteria**:
  - [ ] `Scheduler.clear_light_ramp_state(location, cluster)` method added
  - [ ] `Scheduler.refresh_schedules()` or `update_schedules()` accessible
  - [ ] Mode switch triggers immediate scheduler refresh
  - [ ] Light ramp state cleared for affected location/cluster
  - [ ] Scheduler refresh failure logged but doesn't fail mode switch

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Scheduler updates immediately after mode switch
    Tool: Bash (curl + log inspection)
    Preconditions: Mode switch from flower to veg
    Steps:
      1. Note current time
      2. curl -X POST mode switch
      3. journalctl -u automation-service --since "1 minute ago" | grep -i "schedule"
      4. Assert: Log shows schedule refresh within 1 second of mode switch
      5. curl /api/debug/scheduler to verify new schedules loaded
    Expected Result: No 60-second delay for schedule updates
    Evidence: Logs and debug endpoint response

  Scenario: Light ramp state cleared on mode switch
    Tool: Bash (curl)
    Preconditions: Light ramp in progress
    Steps:
      1. curl /api/debug/ramps before mode switch
      2. Note active ramp state
      3. Trigger mode switch
      4. curl /api/debug/ramps after mode switch
      5. Assert: Old ramp state cleared
    Expected Result: Clean slate for new mode's ramps
    Evidence: Before/after ramp state
  ```

  **Commit**: YES
  - Message: `feat(automation): add immediate scheduler refresh on mode switch`
  - Files: `Infrastructure/automation-service/app/control/scheduler.py`, `Infrastructure/automation-service/app/routes/room_modes.py`

---

- [x] 8. Add multi-cluster coordination enforcement

  **What to do**:
  - When mode switch requested for one cluster, optionally switch ALL clusters in same room
  - Add parameter to mode switch: `coordinate_clusters: bool = True`
  - If true: Get all clusters for location, execute ModeTransitionService for each in same transaction
  - If false: Only switch specified cluster (current behavior, for advanced users)
  - Add validation: warn if clusters would be in different modes after switch

  **Must NOT do**:
  - Force coordination without option to disable
  - Change default behavior without clear communication
  - Add inter-service calls (all within automation-service)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Complex coordination logic

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with Task 5)
  - **Blocks**: Task 11
  - **Blocked By**: Task 4

  **References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py` - Mode switch endpoint
  - `Infrastructure/automation-service/app/repositories/room_modes.py` - Get clusters for location
  - Task 3 output: ModeTransitionService

  **Acceptance Criteria**:
  - [ ] `coordinate_clusters` parameter added to mode switch endpoint
  - [ ] When true, all clusters in room switch together
  - [ ] All cluster switches in single transaction
  - [ ] API response shows which clusters were updated

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Multi-cluster coordinated switch
    Tool: Bash (curl + psql)
    Preconditions: Room with 2 clusters (main, secondary)
    Steps:
      1. curl mode switch with coordinate_clusters=true
      2. psql -c "SELECT cluster, mode FROM room_active_mode WHERE location='Flower Room'"
      3. Assert: Both clusters show new mode
    Expected Result: All clusters switch atomically
    Evidence: Database state showing both clusters

  Scenario: Single cluster switch when coordination disabled
    Tool: Bash (curl + psql)
    Preconditions: Room with 2 clusters
    Steps:
      1. curl mode switch with coordinate_clusters=false
      2. Check room_active_mode for both clusters
      3. Assert: Only specified cluster changed
    Expected Result: Independent cluster control preserved
    Evidence: Database state
  ```

  **Commit**: YES
  - Message: `feat(automation): add multi-cluster mode switch coordination`
  - Files: `Infrastructure/automation-service/app/routes/room_modes.py`, `Infrastructure/automation-service/app/services/mode_transition.py`

---

### PHASE 4: CLEANUP & TESTING

- [x] 10. Fix code/comment contradictions

  **What to do**:
  - `control_engine.py:791 vs 212`: Resolve ramp restoration contradiction
    - Determine correct behavior: Should ramps restore on restart or not?
    - Update code OR comment to match
  - `pid_controller_manager.py:334`: Fix first-tick edge case
    - Handle `previous_mode is None` case explicitly
    - Ensure integrator resets on first tick after restart if mode changed while service was down

  **Must NOT do**:
  - Change intended behavior without understanding impact
  - Remove safety checks

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Small fixes with clear locations

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Any (no dependencies)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/control/control_engine.py:791` - Ramp restoration code
  - `Infrastructure/automation-service/app/control/control_engine.py:212` - Contradicting comment
  - `Infrastructure/automation-service/app/control/pid_controller_manager.py:330-340` - Mode change detection

  **Acceptance Criteria**:
  - [ ] Code and comments align on ramp restoration behavior
  - [ ] PID reset handles None previous_mode case
  - [ ] Behavior documented in comments

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: PID reset on first tick after restart
    Tool: Bash (systemctl + log inspection)
    Preconditions: Mode changed while service was down
    Steps:
      1. Stop automation-service
      2. Change mode in database directly
      3. Start automation-service
      4. Check logs for PID reset
    Expected Result: PID integrator reset on first tick
    Evidence: Log messages showing reset
  ```

  **Commit**: YES
  - Message: `fix(automation): resolve code/comment contradictions in control_engine and pid_manager`
  - Files: `Infrastructure/automation-service/app/control/control_engine.py`, `Infrastructure/automation-service/app/control/pid_controller_manager.py`

---

- [x] 11. Integration tests for full mode switch flow

  **What to do**:
  - Create comprehensive integration test file: `tests/integration/test_mode_transition_full.py`
  - Test scenarios:
    1. Full mode switch flower→veg: verify all tables updated
    2. Setpoints sync: verify climate targets change
    3. Schedules sync: verify ramp times change
    4. Cache invalidation: verify fresh data after switch
    5. Immediate refresh: verify scheduler has new data
    6. Multi-cluster: verify all clusters switch together
    7. Audit trail: verify mode_transition_history populated
    8. Rollback: verify atomic rollback on failure
    9. Submode switch: verify submode changes also sync

  **Must NOT do**:
  - Skip any of the listed scenarios
  - Use mocks that hide real integration issues
  - Depend on specific timing

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
  - **Skills**: []
  - Reason: Comprehensive testing requiring system understanding

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 12)
  - **Blocks**: None (final verification)
  - **Blocked By**: Tasks 5, 7, 8, 9

  **References**:
  - `Infrastructure/automation-service/tests/` - Existing test patterns
  - `Infrastructure/automation-service/tests/conftest.py` - Test fixtures
  - All previous tasks in this plan

  **Acceptance Criteria**:
  - [ ] All 9 test scenarios implemented
  - [ ] Tests use real database (test database)
  - [ ] Tests clean up after themselves
  - [ ] `pytest tests/integration/test_mode_transition_full.py -v` passes

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Full integration test suite passes
    Tool: Bash (pytest)
    Preconditions: Test database configured
    Steps:
      1. pytest tests/integration/test_mode_transition_full.py -v --tb=short
      2. Assert: All tests pass
      3. Assert: No warnings about skipped tests
    Expected Result: 9/9 tests pass
    Evidence: pytest output captured
  ```

  **Commit**: YES
  - Message: `test(automation): add comprehensive mode transition integration tests`
  - Files: `Infrastructure/automation-service/tests/integration/test_mode_transition_full.py`

---

- [x] 12. Elevate log levels for schedule/mode changes

  **What to do**:
  - Find schedule change logging (currently DEBUG level)
  - Elevate to INFO level for production visibility
  - Add structured logging fields: location, cluster, old_value, new_value
  - Ensure mode switch logged at INFO with all details
  - Add ramp state change logging at INFO level

  **Must NOT do**:
  - Log sensitive data
  - Add excessive logging that would impact performance
  - Change log format (keep structured JSON)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Simple logging changes

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 4 (with Task 11)
  - **Blocks**: None
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py` - Mode switch logging
  - `Infrastructure/automation-service/app/repositories/schedules.py` - Schedule change logging
  - `Infrastructure/automation-service/app/control/scheduler.py` - Ramp state logging
  - `Infrastructure/shared/logging.py` - Logging configuration

  **Acceptance Criteria**:
  - [ ] Schedule changes logged at INFO level
  - [ ] Mode switches logged at INFO with old/new mode
  - [ ] Ramp state changes logged at INFO
  - [ ] Logs include location, cluster, relevant values

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Mode switch visible in production logs
    Tool: Bash (journalctl)
    Preconditions: Logging configured for INFO level
    Steps:
      1. Trigger mode switch
      2. journalctl -u automation-service --since "1 minute ago" | grep -i "mode"
      3. Assert: Mode switch log entry at INFO level
      4. Assert: Entry includes old_mode, new_mode, location, cluster
    Expected Result: Mode changes visible without DEBUG level
    Evidence: Log output captured
  ```

  **Commit**: YES
  - Message: `chore(automation): elevate mode/schedule/ramp logging to INFO level`
  - Files: Multiple files with logging changes

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(database): add mode_transition_history audit table` | migrations/*.sql | psql table check |
| 2 | `feat(automation): add sync_climate_setpoints_from_mode_parameters` | repositories/mode_sync.py | pytest |
| 6 | `feat(automation): add SetpointRepository cache invalidation` | repositories/setpoints.py | pytest |
| 3 | `feat(automation): add ModeTransitionService for atomic mode switches` | services/mode_transition.py | pytest |
| 7 | `fix(automation): make light schedule selection deterministic` | repositories/schedules.py | API test |
| 9 | `feat(automation): add debug endpoints for mode/ramp/scheduler state` | routes/debug.py, main.py | curl test |
| 4 | `feat(automation): integrate ModeTransitionService into mode endpoints` | routes/room_modes.py | Full API test |
| 5 | `feat(automation): add immediate scheduler refresh on mode switch` | control/scheduler.py, routes/room_modes.py | Log + API test |
| 8 | `feat(automation): add multi-cluster mode switch coordination` | routes/room_modes.py, services/mode_transition.py | Multi-cluster test |
| 10 | `fix(automation): resolve code/comment contradictions` | control/control_engine.py, control/pid_controller_manager.py | Code review |
| 11 | `test(automation): add comprehensive mode transition integration tests` | tests/integration/* | pytest |
| 12 | `chore(automation): elevate mode/schedule/ramp logging to INFO level` | Multiple | Log inspection |

---

## Success Criteria

### Verification Commands
```bash
# Run all new tests
pytest tests/integration/test_mode_transition_full.py -v

# Verify mode switch syncs everything
curl -X POST http://localhost:8001/api/room/Flower%20Room/main/mode -d '{"mode_name":"veg"}'
psql -U cea -d projectcea -c "SELECT * FROM setpoints WHERE location='Flower Room'"
psql -U cea -d projectcea -c "SELECT * FROM schedules WHERE location='Flower Room'"
psql -U cea -d projectcea -c "SELECT * FROM mode_transition_history ORDER BY triggered_at DESC LIMIT 1"

# Verify debug endpoints
curl http://localhost:8001/api/debug/mode-state/Flower%20Room/main | jq
curl http://localhost:8001/api/debug/ramps/Flower%20Room/main | jq

# Verify logging
journalctl -u automation-service --since "5 minutes ago" | grep -i "mode.*switch"
```

### Final Checklist
- [x] Mode switch syncs climate setpoints to setpoints table
- [x] Mode switch syncs light schedules to schedules table  
- [x] Mode switch syncs ramp times to schedules table
- [x] All syncs happen in single atomic transaction
- [x] Scheduler refreshes immediately (no 60s delay)
- [x] Light ramp state cleared on mode switch
- [x] SetpointRepository cache invalidated on mode switch
- [x] All clusters in room can switch together (optional)
- [x] Mode transitions recorded in audit table
- [x] Debug endpoints available for troubleshooting
- [x] Important events logged at INFO level
- [x] All integration tests pass
- [x] No breaking changes to existing APIs
- [x] Old superseded plan file deleted

---

## Post-Completion Cleanup

- [x] 13. Delete Superseded Plan File

  **What to do**:
  - Delete the old narrower plan that this comprehensive plan supersedes
  - File to delete: `.sisyphus/plans/mode-switch-schedule-sync.md`

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []
  - Reason: Single file deletion

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: After Wave 4 (final step)
  - **Blocks**: None
  - **Blocked By**: All other tasks (run last)

  **Acceptance Criteria**:
  - [x] File `.sisyphus/plans/mode-switch-schedule-sync.md` no longer exists
  - [ ] `ls .sisyphus/plans/` shows only `mode-switch-architecture-fix.md`

  **Commit**: NO (cleanup only, no code change)
