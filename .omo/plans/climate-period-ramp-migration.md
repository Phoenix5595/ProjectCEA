# Plan: Climate Period Ramp Migration

## TL;DR

> **Quick Summary**: Setpoints jump instead of ramping due to 3 bugs: mode-unaware StateManager cache, missing fields in cache-aside, and climate_periods.ramp_minutes being unused. Fix by wiring climate_periods table as the sole setpoint source, removing the deprecated mode-based system entirely, and updating the frontend.
>
> **Deliverables**:
> - Control loop reads setpoints from `climate_periods` table instead of `setpoints` table
> - `SetpointManager` uses `ramp_minutes` from climate periods for transitions
> - Hard removal of deprecated mode-based setpoints (table, routes, code paths)
> - StateManager cache updated for period-based system
> - Frontend updated to use `ClimatePeriodsTable` as sole setpoint editor
> - Database verification of existing climate_periods data
>
> **Estimated Effort**: Large
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 5 → Task 6 → Task 8

---

## Context

### Original Request
User reported setpoints are JUMPING to next values instead of gradually ramping during climate period transitions. Investigation revealed the ramp system is broken at multiple levels.

### Root Cause Analysis

**BUG 1 (PRIMARY): StateManager cache is MODE-UNAWARE**
- File: `app/repositories/setpoints.py` lines 116-122
- `SetpointRepository.get_setpoint(location, cluster, mode)` checks `state.get_setpoint(location, cluster)` — NO mode parameter
- Cache key `setpoint:{location}:{cluster}:{field}` has no mode dimension
- On mode change DAY→PRE_NIGHT: cache returns stale DAY setpoints with `ramp_in_duration=0`
- By time cache expires (~60s TTL), `mode_changed=False` — ramp window permanently missed

**BUG 2: Repository cache-aside missing fields**
- File: `app/repositories/setpoints.py` lines 149-161
- When populating StateManager cache after DB miss, omits `vpd` and `ramp_in_duration`

**BUG 3: climate_periods.ramp_minutes is unused**
- The `climate_periods` table stores `ramp_minutes` per period, but the control loop ONLY reads from the `setpoints` table
- `Scheduler.get_climate_period_setpoints()` exists but is NEVER called from the control loop

### Interview Summary
**Key Decisions**:
- Mode-based setpoints DEPRECATED → hard removal (setpoints table, /api/setpoints routes, all code paths)
- `climate_periods` table is the source of truth
- Climate periods INDEPENDENT of light schedule (own start_time/end_time)
- Keep `mode_id`/`submode_id` — periods are per room mode
- No humidity column — VPD is king
- Each period has own `ramp_minutes`; ramp at start of each period from previous period's setpoints
- Frontend updates included — `ClimatePeriodsTable.tsx` already exists
- No unit tests — Agent QA only

### Research Findings
- Linear interpolation is industry standard for CEA ramps
- Existing `RampManager`/`RampState` implementation is correct when actually triggered
- Redis persistence for ramp state across restarts is well-designed

### Metis Review
**Identified Gaps (addressed)**:
- System restart mid-ramp: Redis persistence handles this
- ramp_minutes=0: instant jump (existing behavior preserved)
- Period change during active ramp: new period interrupts old ramp
- Timezone: system uses America/Toronto throughout
- Historical data: store period_name in mode column

---

## Work Objectives

### Core Objective
Replace the deprecated mode-based setpoint system with the climate_periods table as sole setpoint source, ensuring ramp_minutes drives smooth transitions between periods.

### Concrete Deliverables
- Modified `control_engine.py`: reads from `climate_periods` instead of `setpoints` table
- Modified `setpoint_manager.py`: uses `ramp_minutes` from period data for ramp transitions
- Modified `state/__init__.py`: cache updated for period-based lookups
- Removed `routes/setpoints.py`: deprecated API routes deleted
- Removed mode-based code from `scheduler.py`, `setpoints.py` repository, `schedule_state.py`
- Updated frontend: deprecated components removed, ClimatePeriodsTable is primary
- Database verification: validate climate_periods data integrity

### Definition of Done
- [ ] Control loop reads setpoints exclusively from `climate_periods` table
- [ ] Ramps initiate using `ramp_minutes` from the active climate period
- [ ] No references to mode-based setpoints remain in active codebase
- [ ] Frontend uses `ClimatePeriodsTable` as the sole setpoint editor
- [ ] Service starts and runs without errors for 5+ minutes

### Must Have
- `climate_periods` table as sole setpoint source for control loop
- `ramp_minutes` from each period drives ramp transitions
- Hard removal of mode-based setpoint system (routes, code)
- Period transition detection (previous period → current period)

### Must NOT Have (Guardrails)
- MUST NOT change VPD cascade controller logic
- MUST NOT change PID tuning or device control
- MUST NOT modify the ramp algorithm itself (linear interpolation is correct)
- MUST NOT add new ramp types (S-curve, exponential)
- MUST NOT change light schedule system
- MUST NOT change room mode transitions
- MUST NOT delete historical data from effective_setpoints table
- MUST NOT use `sleep()` or blocking calls in control loop

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**

### Test Decision
- **Infrastructure exists**: YES (pytest)
- **Automated tests**: NO (user decision)
- **Agent-Executed QA**: YES (mandatory for all tasks)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately):
├── Task 1: Wire climate_periods into control_engine.py (core change)
└── Task 4: Code archaeology — grep all deprecated references

Wave 2 (After Wave 1):
├── Task 2: Update SetpointManager for period-based ramps
├── Task 3: Fix StateManager cache for period-based data
└── Task 5: Hard-remove deprecated mode-based system

Wave 3 (After Wave 2):
├── Task 6: Frontend migration
├── Task 7: Database verification
└── Task 8: End-to-end integration verification
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 2, 3, 5 | 4 |
| 2 | 1 | 5, 8 | 3, 4 |
| 3 | 1 | 5, 8 | 2, 4 |
| 4 | None | 5 | 1 |
| 5 | 1, 2, 3, 4 | 6, 8 | None |
| 6 | 5 | 8 | 7 |
| 7 | None (best after 1) | 8 | 6 |
| 8 | 5, 6, 7 | None | None (final) |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Dispatch |
|------|-------|---------------------|
| 1 | 1, 4 | Task 1: deep; Task 4: quick |
| 2 | 2, 3, 5 | Task 2: deep; Task 3: unspecified-high; Task 5: unspecified-high |
| 3 | 6, 7, 8 | Task 6: visual-engineering; Task 7: quick; Task 8: unspecified-high |

---

## TODOs

### Wave 1

- [ ] 1. Replace mode-based setpoint fetching in control_engine.py with climate_periods

  **What to do**:
  - In `control_engine.py` method `run_control_loop()`, replace the block at lines 316-435 that:
    1. Calls `scheduler.get_climate_mode()` to get DAY/NIGHT/PRE_DAY/PRE_NIGHT
    2. Fetches setpoints from `setpoints` table via `setpoint_repo.get_setpoint(location, cluster, mode)`
    3. Passes mode-based data to `setpoint_manager.compute_effective_setpoints()`
  - Replace with new flow:
    1. Get current time string (HH:MM format, America/Toronto)
    2. Call `climate_periods_repo.get_active_period(location, cluster, time_str)` to get the active period
    3. Build `setpoint_data` dict from period fields: `heating_setpoint`, `cooling_setpoint`, `vpd_setpoint`, `co2_setpoint`, `ramp_minutes`
    4. Detect period transitions: compare `current_period.period_name` vs stored `_current_period_name[key]`
    5. Pass `period_changed` flag and `ramp_minutes` to `setpoint_manager.compute_effective_setpoints()`
  - Get previous period's setpoints for ramp start values:
    - When period changes, query the PREVIOUS period's setpoints from `climate_periods` table
    - Use the period that was active before the transition (wrap-around: last period of day → first period)
  - Store `period_name` in the `mode` column when logging to `effective_setpoints` table
  - `climate_periods_repo` is available via `database.climate_periods_repo`

  **Must NOT do**:
  - MUST NOT change device control logic (lines after setpoint computation)
  - MUST NOT change VPD cascade logic
  - MUST NOT change PID tuning logic

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Control loop is the most critical code path; requires careful analysis of existing flow and precise replacement
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with Task 4)
  - **Blocks**: Tasks 2, 3, 5
  - **Blocked By**: None

  **References**:

  **Pattern References** (existing code to follow):
  - `Infrastructure/automation-service/app/control/control_engine.py:316-435` — Current mode-based setpoint fetching block (REPLACE THIS)
  - `Infrastructure/automation-service/app/control/control_engine.py:382-404` — Current StateManager cache read + repo fallback (REPLACE THIS)
  - `Infrastructure/automation-service/app/control/control_engine.py:79-85` — SetpointManager initialization (needs database param)

  **API/Type References**:
  - `Infrastructure/automation-service/app/repositories/climate_periods.py:141` — `get_active_period(location, cluster, time_str)` method signature and return type
  - `Infrastructure/automation-service/app/repositories/climate_periods.py:1-50` — ClimatePeriodRepository class and data model
  - `Infrastructure/automation-service/app/database.py` — `database.climate_periods_repo` access pattern

  **Documentation References**:
  - `Infrastructure/automation-service/REQUIREMENTS.md` — Ramp logic requirements and constraints
  - `Infrastructure/automation-service/app/control/AGENTS.md` — Control layer documentation

  **Acceptance Criteria**:
  - [ ] `control_engine.py` no longer calls `scheduler.get_climate_mode()`
  - [ ] `control_engine.py` no longer calls `setpoint_repo.get_setpoint()`
  - [ ] `control_engine.py` calls `climate_periods_repo.get_active_period()` instead
  - [ ] Period transition detected correctly (previous period name vs current)
  - [ ] `ramp_minutes` from climate period passed to setpoint_manager
  - [ ] `period_name` stored in mode column for effective_setpoints logging
  - [ ] ruff check passes, pyright shows no new type errors

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Service starts without errors after control loop change
    Tool: Bash (systemctl + journalctl)
    Preconditions: Code deployed to mothernode
    Steps:
      1. sudo systemctl restart automation-service
      2. sleep 5
      3. sudo systemctl status automation-service --no-pager | head -5
      4. Assert: status shows "active (running)"
      5. sudo journalctl -u automation-service --since "10 seconds ago" --no-pager | grep -i "error|traceback"
      6. Assert: No errors or tracebacks
    Expected Result: Service starts cleanly
    Evidence: systemctl status + journalctl output captured

  Scenario: Control loop reads from climate_periods table
    Tool: Bash (journalctl + curl)
    Preconditions: Service running, climate_periods configured for Flower Room
    Steps:
      1. sudo journalctl -u automation-service --since "30 seconds ago" --no-pager | grep -i "period|climate"
      2. Assert: Logs show active period name (not DAY/NIGHT/PRE_DAY/PRE_NIGHT modes)
      3. curl -s http://mothernode:8001/api/climate-periods/Flower%20Room/main | python3 -m json.tool
      4. Assert: Response contains periods array with ramp_minutes
    Expected Result: Control loop uses climate periods, not modes
    Evidence: Log output + API response captured

  Scenario: No references to get_climate_mode in control_engine
    Tool: Bash (grep)
    Preconditions: Code changes applied
    Steps:
      1. grep -n "get_climate_mode" Infrastructure/automation-service/app/control/control_engine.py
      2. Assert: No matches found (exit code 1)
      3. grep -n "setpoint_repo.get_setpoint" Infrastructure/automation-service/app/control/control_engine.py
      4. Assert: No matches found (exit code 1)
    Expected Result: All deprecated calls removed from control_engine
    Evidence: grep output captured
  ```

  **Commit**: YES
  - Message: `feat(control): wire climate_periods as sole setpoint source in control loop`
  - Files: `control_engine.py`
  - Pre-commit: `ruff check --fix . && ruff format .`

- [ ] 4. Code archaeology — grep all deprecated mode-based references

  **What to do**:
  - Exhaustive search across the entire codebase for all references to the deprecated system:
    - `get_climate_mode` — scheduler method
    - `ramp_in_duration` — old ramp field from setpoints table
    - `PRE_DAY`, `PRE_NIGHT` — old mode names (as string literals, not in comments/docs/historical)
    - `setpoint_repo.get_setpoint` — old repository method
    - `setpoints` table references (NOT `effective_setpoints` — that's kept)
    - `/api/setpoints` route references
    - Imports of `SetpointsTable`, `SetpointEditor`, `SetpointsDisplay` in frontend
  - Produce a file-by-file manifest of everything that needs removal/modification
  - Categorize each reference: REMOVE (dead code), MODIFY (needs update), KEEP (historical/unrelated)
  - This manifest will be used by Task 5 as a comprehensive removal checklist

  **Must NOT do**:
  - MUST NOT make any code changes — this is read-only analysis
  - MUST NOT flag references in comments, docstrings, or git history

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Pure grep/search task, no complex logic
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 1 (with Task 1)
  - **Blocks**: Task 5
  - **Blocked By**: None

  **References**:
  - `Infrastructure/automation-service/app/control/scheduler.py:672-769` — `get_climate_mode()` method to be removed
  - `Infrastructure/automation-service/app/repositories/setpoints.py:104-250` — Mode-based get/set methods
  - `Infrastructure/automation-service/app/routes/setpoints.py` — Entire file to be removed
  - `Infrastructure/automation-service/app/state/__init__.py:537-690` — Mode-unaware setpoint cache
  - `Infrastructure/automation-service/app/services/schedule_state.py` — ramp_in_duration references
  - `Infrastructure/automation-service/app/routes/schedules/utils.py` — ramp_in_duration references
  - `Infrastructure/automation-service/app/routes/room_modes.py` — Setpoint sync logic
  - `Infrastructure/frontend/src/components/SetpointEditor.tsx` — To be removed
  - `Infrastructure/frontend/src/components/SetpointsTable.tsx` — To be removed
  - `Infrastructure/frontend/src/types/setpoint.ts` — Mode-based types

  **Acceptance Criteria**:
  - [ ] Complete manifest of all deprecated references produced
  - [ ] Each reference categorized as REMOVE/MODIFY/KEEP
  - [ ] Manifest covers both backend (Python) and frontend (TypeScript)
  - [ ] Output saved to `.sisyphus/evidence/deprecated-refs-manifest.md`

  **Commit**: NO (read-only task, produces evidence file only)

### Wave 2

- [ ] 2. Update SetpointManager for period-based ramp transitions

  **What to do**:
  - Modify `setpoint_manager.py` to accept period-based input instead of mode-based:
    1. Update `compute_effective_setpoints()` signature: replace `mode`/`previous_mode` params with `period_name`/`previous_period_name`/`period_changed` flag
    2. Replace `mode_changed` detection with `period_changed` flag (passed from control_engine)
    3. Replace `ramp_in_duration` (from setpoints table) with `ramp_minutes` (from climate_periods)
    4. Update `_calculate_ramp_start_values()` to query PREVIOUS period's setpoints from `climate_periods` table instead of previous mode from `setpoints` table
    5. Update `_initiate_ramp()` to use `ramp_minutes` for ramp duration
  - Keep the existing `RampManager` and `RampState` classes unchanged — they work correctly
  - Keep the existing ramp algorithm (linear interpolation) unchanged
  - The `database` parameter was already wired in prior fix plan; ensure it's available for period queries
  - Update logging to reference period names instead of mode names

  **Must NOT do**:
  - MUST NOT change `RampManager` or `RampState` classes
  - MUST NOT change the linear interpolation algorithm
  - MUST NOT change ramp skip thresholds (0.2°C heating, 0.5°C cooling, 0.05 kPa VPD)
  - MUST NOT change VPD ramp warning (>15min)

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: SetpointManager is complex (~685 lines) with intricate ramp logic; wrong changes break all climate control
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2 (with Tasks 3, 5)
  - **Blocks**: Tasks 5, 8
  - **Blocked By**: Task 1

  **References**:

  **Pattern References**:
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:345-440` — `compute_effective_setpoints()` method (MODIFY)
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:531-610` — `_calculate_ramp_start_values()` method (MODIFY)
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:442-530` — `_initiate_ramp()` method (MODIFY)
  - `Infrastructure/automation-service/app/control/setpoint_manager.py:1-50` — RampState, RampManager classes (DO NOT MODIFY)

  **API/Type References**:
  - `Infrastructure/automation-service/app/repositories/climate_periods.py` — Repository for querying previous period setpoints
  - `Infrastructure/automation-service/app/redis/ramps.py` — Redis ramp persistence (keep as-is)

  **Documentation References**:
  - `Infrastructure/automation-service/REQUIREMENTS.md` — Ramp logic requirements

  **Acceptance Criteria**:
  - [ ] `compute_effective_setpoints()` accepts period-based parameters (not mode-based)
  - [ ] `_calculate_ramp_start_values()` queries previous PERIOD setpoints from climate_periods
  - [ ] `_initiate_ramp()` uses `ramp_minutes` for duration
  - [ ] RampManager/RampState classes unchanged
  - [ ] Skip thresholds unchanged
  - [ ] Logging references period names, not mode names
  - [ ] ruff check passes, pyright shows no new type errors

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Ramp initiates on period transition
    Tool: Bash (journalctl)
    Preconditions: Service running, period transition approaching
    Steps:
      1. sudo journalctl -u automation-service -f --no-pager | grep -m 5 "RAMP|ramp|period"
      2. Wait for period transition
      3. Assert: Log shows "Initiating ramp" with period name and ramp_minutes value
      4. Assert: Log shows previous period setpoints as ramp start values
    Expected Result: Ramp starts with correct previous period values
    Evidence: journalctl output captured

  Scenario: Ramp progresses gradually (not jumping)
    Tool: Bash (psql)
    Preconditions: Ramp in progress after period transition
    Steps:
      1. PGPASSWORD=cea_pass psql -h 192.168.1.78 -U cea_user -d cea_sensors -c "
         SELECT timestamp, mode, effective_heating_setpoint, nominal_heating_setpoint
         FROM effective_setpoints
         WHERE location='Flower Room' AND timestamp > now() - interval '10 minutes'
         ORDER BY timestamp DESC LIMIT 20;"
      2. Assert: effective_heating_setpoint values show gradual change (not instant jump)
      3. Assert: mode column shows period_name
    Expected Result: Effective setpoints transition gradually over ramp_minutes
    Evidence: Query results captured
  ```

  **Commit**: YES (groups with Task 3)
  - Message: `feat(control): update SetpointManager for period-based ramp transitions`
  - Files: `setpoint_manager.py`
  - Pre-commit: `ruff check --fix . && ruff format .`

- [ ] 3. Fix StateManager cache for period-based setpoint data

  **What to do**:
  - In `app/state/__init__.py`, update the setpoint cache to be period-aware:
    1. Change cache key from `setpoint:{location}:{cluster}:{field}` to `setpoint:{location}:{cluster}:{period_name}:{field}`
    2. Update `get_setpoint()` to accept `period_name` parameter
    3. Update `set_setpoint()` to accept `period_name` parameter and include ALL fields (vpd, ramp_minutes)
    4. Add cache invalidation method: `invalidate_setpoint_cache(location, cluster)` — clears all period entries for a location/cluster
  - Update callers in `control_engine.py` (from Task 1) and `setpoints.py` repository to pass period_name
  - Alternatively, since climate_periods are read directly from DB now, the StateManager setpoint cache may become unnecessary — evaluate whether to keep it for performance or remove it entirely
  - If keeping: ensure all fields are populated (heating, cooling, vpd, co2, ramp_minutes)
  - If removing: ensure control loop performance stays <5s with direct DB reads on every tick (1Hz)

  **Must NOT do**:
  - MUST NOT change non-setpoint caches in StateManager (sensor data, device state, etc.)
  - MUST NOT change Redis pub/sub patterns
  - MUST NOT change WebSocket update mechanisms

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Cache architecture change with performance implications
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 2 (with Tasks 2, 5)
  - **Blocks**: Tasks 5, 8
  - **Blocked By**: Task 1

  **References**:
  - `Infrastructure/automation-service/app/state/__init__.py:537-600` — `get_setpoint()` method (MODIFY)
  - `Infrastructure/automation-service/app/state/__init__.py:600-690` — `set_setpoint()` method (MODIFY)
  - `Infrastructure/automation-service/app/repositories/setpoints.py:116-165` — Cache-aside pattern (MODIFY or REMOVE)
  - `Infrastructure/automation-service/app/redis/schema.py` — Redis key patterns (for cache key format)
  - `Infrastructure/automation-service/app/redis/setpoints.py` — Redis setpoint writes for WebSocket (KEEP — separate from cache)

  **Acceptance Criteria**:
  - [ ] Cache is period-aware OR removed entirely (decision documented)
  - [ ] If kept: all fields populated (vpd, ramp_minutes included)
  - [ ] If kept: cache key includes period_name
  - [ ] If removed: control loop still completes in <5s
  - [ ] No stale cache causing wrong setpoints on period change
  - [ ] ruff check passes, pyright shows no new type errors

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: No stale setpoints on period change
    Tool: Bash (curl + journalctl)
    Preconditions: Service running, period transition occurs
    Steps:
      1. Note current period and setpoints via: curl -s http://mothernode:8001/api/climate-periods/Flower%20Room/main/active
      2. Wait for period transition (or simulate by changing period times)
      3. Immediately check: curl -s http://mothernode:8001/api/status | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('effective_setpoints',{}).get('Flower Room',{}))"
      4. Assert: Setpoints match the NEW period (not stale from old period)
    Expected Result: Setpoints update immediately on period change
    Evidence: API responses captured before/after transition
  ```

  **Commit**: YES (groups with Task 2)
  - Message: `fix(state): make setpoint cache period-aware to prevent stale data`
  - Files: `state/__init__.py`
  - Pre-commit: `ruff check --fix . && ruff format .`

- [ ] 5. Hard-remove deprecated mode-based setpoint system

  **What to do**:
  - Using the manifest from Task 4, systematically remove all deprecated code:

  **Backend removals**:
  - DELETE `app/routes/setpoints.py` entirely (mode-based setpoint API routes)
  - REMOVE router registration for setpoints routes in app setup
  - REMOVE `get_climate_mode()` method from `app/control/scheduler.py` (lines 672-769)
  - REMOVE mode-based `get_setpoint()`/`set_setpoint()` methods from `app/repositories/setpoints.py` (lines 104-250) — keep any logging/audit methods
  - REMOVE `ramp_in_duration` references from `app/services/schedule_state.py`
  - REMOVE `ramp_in_duration` references from `app/routes/schedules/utils.py`
  - REMOVE setpoint sync logic from `app/routes/room_modes.py`
  - UPDATE `app/container.py` — remove ramp restoration from mode-based setpoints, wire period-based ramp restoration
  - UPDATE `app/services/mode_transition_service.py` — remove setpoint cache invalidation tied to mode changes; add period-transition cache handling if needed
  - REMOVE unused imports across all modified files

  **Cleanup**:
  - Run `ruff check --fix .` and `ruff format .` across entire automation-service
  - Verify pyright strict mode passes

  **Must NOT do**:
  - MUST NOT drop the `setpoints` database table (historical data preservation)
  - MUST NOT remove `effective_setpoints` table or its write path
  - MUST NOT remove `climate_periods` routes/repository (that's the NEW system)
  - MUST NOT remove light schedule code from scheduler.py (only climate mode code)
  - MUST NOT remove Redis ramp persistence code (used by new system too)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Large-scale removal across 10+ files requires careful dependency tracking
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO — Sequential (after Wave 1 + Tasks 2, 3)
  - **Blocks**: Tasks 6, 8
  - **Blocked By**: Tasks 1, 2, 3, 4

  **References**:
  - `.sisyphus/evidence/deprecated-refs-manifest.md` — Complete removal checklist from Task 4
  - `Infrastructure/automation-service/app/routes/setpoints.py` — ENTIRE FILE TO DELETE
  - `Infrastructure/automation-service/app/control/scheduler.py:672-769` — `get_climate_mode()` to remove
  - `Infrastructure/automation-service/app/repositories/setpoints.py:104-250` — Mode-based methods to remove
  - `Infrastructure/automation-service/app/services/schedule_state.py` — ramp_in_duration refs to remove
  - `Infrastructure/automation-service/app/routes/schedules/utils.py` — ramp_in_duration refs to remove
  - `Infrastructure/automation-service/app/routes/room_modes.py` — Setpoint sync logic to remove
  - `Infrastructure/automation-service/app/container.py` — Ramp restoration to update
  - `Infrastructure/automation-service/app/services/mode_transition_service.py` — Cache invalidation to update

  **Acceptance Criteria**:
  - [ ] `app/routes/setpoints.py` deleted
  - [ ] No `get_climate_mode` calls remain in codebase
  - [ ] No `ramp_in_duration` references remain (except comments/docs)
  - [ ] No `setpoint_repo.get_setpoint` calls remain
  - [ ] `ruff check .` passes with 0 errors
  - [ ] `ruff format --check .` passes
  - [ ] pyright strict mode passes (0 errors)
  - [ ] Service starts and runs without import errors

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: No deprecated references remain
    Tool: Bash (grep)
    Preconditions: All removals applied
    Steps:
      1. grep -rn "get_climate_mode" Infrastructure/automation-service/app/ --include="*.py"
      2. Assert: No matches (exit code 1)
      3. grep -rn "ramp_in_duration" Infrastructure/automation-service/app/ --include="*.py" | grep -v "#|docstring|comment"
      4. Assert: No active code matches
      5. grep -rn "from.*routes.*setpoints import|from.*routes.setpoints" Infrastructure/automation-service/app/ --include="*.py"
      6. Assert: No imports of deleted setpoints routes
      7. ls Infrastructure/automation-service/app/routes/setpoints.py 2>&1
      8. Assert: "No such file or directory"
    Expected Result: All deprecated code removed
    Evidence: grep outputs captured

  Scenario: Service starts after removal
    Tool: Bash (systemctl)
    Preconditions: Code deployed
    Steps:
      1. sudo systemctl restart automation-service
      2. sleep 5
      3. sudo systemctl status automation-service --no-pager | head -5
      4. Assert: "active (running)"
      5. sudo journalctl -u automation-service --since "10 seconds ago" --no-pager | grep -i "error|import|module"
      6. Assert: No import errors or missing module errors
    Expected Result: Clean startup with no deprecated dependencies
    Evidence: systemctl + journalctl output

  Scenario: Ruff and pyright pass
    Tool: Bash
    Preconditions: All changes applied
    Steps:
      1. cd Infrastructure && ruff check automation-service/app/ && echo "RUFF OK"
      2. Assert: "RUFF OK" in output
      3. cd Infrastructure/automation-service && pyright app/ 2>&1 | tail -5
      4. Assert: "0 errors" in output
    Expected Result: Code quality checks pass
    Evidence: Tool output captured
  ```

  **Commit**: YES
  - Message: `refactor(control): hard-remove deprecated mode-based setpoint system`
  - Files: Multiple (all removed/modified files)
  - Pre-commit: `ruff check --fix . && ruff format .`

### Wave 3

- [ ] 6. Frontend migration — remove mode-based UI, wire ClimatePeriodsTable

  **What to do**:
  - **Remove deprecated components**:
    - DELETE `Infrastructure/frontend/src/components/SetpointEditor.tsx`
    - DELETE `Infrastructure/frontend/src/components/SetpointsTable.tsx`
    - REMOVE mode-based setpoint types from `src/types/setpoint.ts`
    - REMOVE all imports/usages of deleted components
  - **Update ZoneConfig page** (`src/pages/ZoneConfig.tsx`):
    - Already has ClimatePeriodsTable support — verify it's the primary/only setpoint editor
    - Remove any fallback to old SetpointsTable/SetpointEditor
    - Remove any references to `/api/setpoints` endpoints
  - **Update ZoneCard** (`src/components/ZoneCard.tsx`):
    - Display active period name instead of DAY/NIGHT/PRE_DAY/PRE_NIGHT mode
    - Show current period's setpoints
  - **Update SetpointTimeline** (`src/components/SetpointTimeline.tsx`):
    - Render periods from climate_periods data instead of fixed 4 modes
    - Show ramp transitions between periods
  - **Verify ClimatePeriodsTable** (`src/components/ClimatePeriodsTable.tsx`):
    - Already exists and functional — ensure it's wired as the sole editor
    - Verify it reads/writes via `/api/climate-periods/` endpoints
  - **Build verification**:
    - Run `npm run build` in frontend directory
    - Verify no TypeScript errors
    - Verify no missing imports

  **Must NOT do**:
  - MUST NOT change light schedule controls
  - MUST NOT change room mode selector UI
  - MUST NOT change Grafana dashboard integration
  - MUST NOT change device control UI (DeviceManager)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Frontend component removal and rewiring with visual verification
  - **Skills**: [`frontend-ui-ux`]
    - `frontend-ui-ux`: Component removal/rewiring requires understanding of React component tree

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 3 (with Task 7)
  - **Blocks**: Task 8
  - **Blocked By**: Task 5

  **References**:
  - `Infrastructure/frontend/src/components/ClimatePeriodsTable.tsx` — EXISTING component (keep, verify wiring)
  - `Infrastructure/frontend/src/components/SetpointEditor.tsx` — TO DELETE
  - `Infrastructure/frontend/src/components/SetpointsTable.tsx` — TO DELETE
  - `Infrastructure/frontend/src/components/SetpointTimeline.tsx` — MODIFY for dynamic periods
  - `Infrastructure/frontend/src/components/ZoneCard.tsx` — MODIFY for active period display
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` — MODIFY to remove old setpoint refs
  - `Infrastructure/frontend/src/types/setpoint.ts` — MODIFY to remove mode-based types

  **Acceptance Criteria**:
  - [ ] `SetpointEditor.tsx` deleted
  - [ ] `SetpointsTable.tsx` deleted
  - [ ] No imports of deleted components remain
  - [ ] `npm run build` succeeds (exit 0)
  - [ ] No TypeScript errors
  - [ ] ZoneConfig uses ClimatePeriodsTable exclusively
  - [ ] ZoneCard shows active period name

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Frontend builds successfully
    Tool: Bash
    Preconditions: Frontend code updated
    Steps:
      1. cd Infrastructure/frontend && npm run build 2>&1
      2. Assert: exit code 0
      3. Assert: no "error TS" in output
    Expected Result: Clean build
    Evidence: Build output captured

  Scenario: ZoneConfig page renders with ClimatePeriodsTable
    Tool: Playwright (playwright skill)
    Preconditions: Frontend running on mothernode:8001
    Steps:
      1. Navigate to: http://mothernode:8001/zone/Flower%20Room/main
      2. Wait for: page load (timeout: 10s)
      3. Assert: ClimatePeriodsTable visible (look for period rows with Start/End/Ramp columns)
      4. Assert: NO SetpointsTable visible (no DAY/NIGHT/PRE_DAY/PRE_NIGHT mode dropdowns)
      5. Screenshot: .sisyphus/evidence/task-6-zoneconfig.png
    Expected Result: Only ClimatePeriodsTable shown
    Evidence: .sisyphus/evidence/task-6-zoneconfig.png

  Scenario: Deleted components have no references
    Tool: Bash (grep)
    Preconditions: Components deleted
    Steps:
      1. grep -rn "SetpointEditor|SetpointsTable" Infrastructure/frontend/src/ --include="*.tsx" --include="*.ts"
      2. Assert: No matches (exit code 1)
      3. ls Infrastructure/frontend/src/components/SetpointEditor.tsx 2>&1
      4. Assert: "No such file or directory"
      5. ls Infrastructure/frontend/src/components/SetpointsTable.tsx 2>&1
      6. Assert: "No such file or directory"
    Expected Result: Clean removal
    Evidence: grep + ls output
  ```

  **Commit**: YES
  - Message: `feat(ui): migrate to ClimatePeriodsTable, remove deprecated setpoint components`
  - Files: Frontend components (deleted + modified)
  - Pre-commit: `npm run build`

- [ ] 7. Database verification — validate climate_periods data

  **What to do**:
  - Query the `climate_periods` table to verify existing data integrity:
    1. Check all rooms have periods configured (Flower Room at minimum)
    2. Verify 24-hour coverage for each room/cluster/mode combination
    3. Verify `ramp_minutes` values are reasonable (0-120 range)
    4. Verify setpoint values are within physical limits (heating: 15-35°C, cooling: 18-40°C, VPD: 0.4-1.8 kPa, CO2: 400-2000 ppm)
    5. Verify `start_time`/`end_time` are valid HH:MM format
  - If any room is missing climate_periods data, log a WARNING (don't fail) — the system should degrade gracefully
  - Document current data state in evidence file

  **Must NOT do**:
  - MUST NOT modify any data without user confirmation
  - MUST NOT drop or alter the table schema

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Read-only database verification with simple queries
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES — Wave 3 (with Task 6)
  - **Blocks**: Task 8
  - **Blocked By**: None (but logically after Task 1)

  **References**:
  - `Infrastructure/automation-service/app/repositories/climate_periods.py` — Table schema and column definitions
  - `Infrastructure/database/cea_schema.sql` — Database schema

  **Acceptance Criteria**:
  - [ ] All rooms have climate_periods configured
  - [ ] 24-hour coverage verified for each room
  - [ ] Setpoint values within physical limits
  - [ ] ramp_minutes values are reasonable
  - [ ] Results documented in `.sisyphus/evidence/task-7-db-verification.md`

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Verify climate_periods data exists
    Tool: Bash (psql)
    Preconditions: Database accessible
    Steps:
      1. PGPASSWORD=cea_pass psql -h 192.168.1.78 -U cea_user -d cea_sensors -c "
         SELECT location, cluster, mode_id, count(*) as period_count,
                array_agg(period_name ORDER BY start_time) as periods
         FROM climate_periods
         GROUP BY location, cluster, mode_id
         ORDER BY location, cluster, mode_id;"
      2. Assert: At least one row returned for Flower Room
      3. Assert: period_count >= 1 for each group
    Expected Result: Climate periods data exists for active rooms
    Evidence: Query results captured

  Scenario: Verify 24h coverage and value ranges
    Tool: Bash (psql)
    Preconditions: Data exists
    Steps:
      1. PGPASSWORD=cea_pass psql -h 192.168.1.78 -U cea_user -d cea_sensors -c "
         SELECT location, cluster, mode_id, period_name, start_time, end_time, ramp_minutes,
                heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint
         FROM climate_periods
         WHERE location='Flower Room'
         ORDER BY start_time;"
      2. Assert: Periods cover full 24 hours (no gaps)
      3. Assert: All setpoint values within physical limits
      4. Assert: ramp_minutes between 0 and 120
    Expected Result: Complete, valid data
    Evidence: Query results saved to .sisyphus/evidence/task-7-db-verification.md
  ```

  **Commit**: NO (read-only verification task)

- [ ] 8. End-to-end integration verification

  **What to do**:
  - Comprehensive verification that the entire system works after migration:
    1. Restart automation-service and verify clean startup
    2. Verify control loop is running and reading from climate_periods
    3. Verify effective setpoints are being written to database with period_name in mode column
    4. Verify ramp behavior during a period transition (may need to wait or temporarily adjust period times)
    5. Verify frontend loads and displays climate periods correctly
    6. Verify deprecated endpoints return 404 (e.g., `/api/setpoints/Flower Room/main`)
    7. Verify no errors in logs for >5 minutes of continuous operation
  - Document results in evidence file

  **Must NOT do**:
  - MUST NOT change any code — this is verification only
  - MUST NOT permanently modify period times for testing (restore if changed)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Multi-faceted verification across backend, frontend, and database
  - **Skills**: [`playwright`]
    - `playwright`: Needed for frontend verification

  **Parallelization**:
  - **Can Run In Parallel**: NO — Sequential (final task)
  - **Blocks**: None (final)
  - **Blocked By**: Tasks 5, 6, 7

  **References**:
  - All files modified in Tasks 1-6

  **Acceptance Criteria**:
  - [ ] Service runs stably for 5+ minutes with no errors
  - [ ] Control loop reads from climate_periods (verified via logs)
  - [ ] Effective setpoints written with period_name in mode column
  - [ ] Frontend renders ClimatePeriodsTable correctly
  - [ ] Deprecated `/api/setpoints` returns 404
  - [ ] No deprecated references found via grep
  - [ ] All evidence captured in `.sisyphus/evidence/`

  **Agent-Executed QA Scenarios**:

  ```
  Scenario: Full system health after migration
    Tool: Bash (systemctl + journalctl)
    Preconditions: All tasks 1-7 complete and deployed
    Steps:
      1. sudo systemctl status automation-service --no-pager | head -5
      2. Assert: "active (running)"
      3. sleep 300 (5 minutes of stable operation)
      4. sudo journalctl -u automation-service --since "5 minutes ago" --no-pager | grep -c -i "error|traceback"
      5. Assert: Count is 0
    Expected Result: Stable operation for 5+ minutes
    Evidence: Health check output captured

  Scenario: Control loop uses climate_periods
    Tool: Bash (journalctl + psql)
    Preconditions: Service running
    Steps:
      1. sudo journalctl -u automation-service --since "1 minute ago" --no-pager | grep -i "period"
      2. Assert: Logs show period names (not DAY/NIGHT modes)
      3. PGPASSWORD=cea_pass psql -h 192.168.1.78 -U cea_user -d cea_sensors -c "
         SELECT DISTINCT mode FROM effective_setpoints
         WHERE timestamp > now() - interval '5 minutes';"
      4. Assert: mode column contains period names (not DAY/NIGHT/PRE_DAY/PRE_NIGHT)
    Expected Result: System exclusively uses period-based setpoints
    Evidence: Log + query output captured

  Scenario: Deprecated API returns 404
    Tool: Bash (curl)
    Preconditions: Service running
    Steps:
      1. curl -s -o /dev/null -w "%{http_code}" http://mothernode:8001/api/setpoints/Flower%20Room/main
      2. Assert: HTTP status is 404 (or 405 or connection refused for that path)
    Expected Result: Old endpoint no longer exists
    Evidence: HTTP status captured

  Scenario: Frontend displays climate periods
    Tool: Playwright (playwright skill)
    Preconditions: Production frontend accessible
    Steps:
      1. Navigate to: http://mothernode:8001/zone/Flower%20Room/main
      2. Wait for: page load (timeout: 10s)
      3. Assert: Climate periods table visible with period rows
      4. Assert: No DAY/NIGHT/PRE_DAY/PRE_NIGHT mode selectors visible
      5. Screenshot: .sisyphus/evidence/task-8-frontend-final.png
    Expected Result: Frontend shows only period-based setpoint management
    Evidence: .sisyphus/evidence/task-8-frontend-final.png
  ```

  **Commit**: NO (verification only — capture evidence)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(control): wire climate_periods as sole setpoint source in control loop` | `control_engine.py` | Service starts |
| 2+3 | `feat(control): update SetpointManager for period-based ramp transitions` | `setpoint_manager.py`, `state/__init__.py` | Ramp logs |
| 5 | `refactor(control): hard-remove deprecated mode-based setpoint system` | Multiple backend files | ruff + pyright |
| 6 | `feat(ui): migrate to ClimatePeriodsTable, remove deprecated setpoint components` | Frontend files | `npm run build` |

---

## Success Criteria

### Verification Commands
```bash
# Service health
sudo systemctl status automation-service --no-pager | head -5  # Expected: active (running)

# No errors
sudo journalctl -u automation-service --since "5 minutes ago" --no-pager | grep -ci "error"  # Expected: 0

# Climate periods in use
sudo journalctl -u automation-service --since "1 minute ago" --no-pager | grep "period"  # Expected: period names in logs

# Deprecated code removed
grep -rn "get_climate_mode|ramp_in_duration|setpoint_repo.get_setpoint" Infrastructure/automation-service/app/ --include="*.py" | grep -v "#"  # Expected: no matches

# Frontend build
cd Infrastructure/frontend && npm run build  # Expected: exit 0

# Deprecated API gone
curl -s -o /dev/null -w "%{http_code}" http://mothernode:8001/api/setpoints/Flower%20Room/main  # Expected: 404

# Ramp debug
curl -s http://mothernode:8001/api/debug/ramps/Flower%20Room/main | python3 -m json.tool  # Expected: valid JSON with ramp state
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] Climate periods are sole setpoint source
- [ ] Ramps use ramp_minutes from climate periods
- [ ] No mode-based setpoint code remains
- [ ] Frontend builds and renders correctly
- [ ] Service runs stably for 5+ minutes
