# Climate Periods Table - UI Redesign Work Plan

## TL;DR

> **Quick Summary**: Replace the current 4-period setpoint UI (PRE_DAY, DAY, PRE_NIGHT, NIGHT) with a configurable table allowing 1-7 climate periods with absolute start/end times, similar to Priva and Damatex commercial climate control systems.

> **Deliverables**:
> - New `climate_periods` database table
> - Backend API for period CRUD operations
> - Updated frontend with ClimatePeriodsTable component
> - Dynamic timeline visualization
> - Migration script from existing mode_parameters

> **Estimated Effort**: Large (20-30 tasks)
> **Parallel Execution**: YES - 3 waves
> **Critical Path**: Database → Backend API → Frontend Table → Timeline → Integration

---

## Context

### Original Request
User wants to rework the setpoint section of the frontend. Instead of fixed zones for PRE_DAY, DAY, PRE_NIGHT, NIGHT, they want a table (like Priva and Damatex systems) where they can configure climate periods with columns: Period, Start, End, Ramp, Heating, Cooling, VPD, CO2, Details.

### Interview Summary
**Key Discussions**:
- Replace 4 fixed periods with 1-7 configurable periods
- Time format: hh:mm (24-hour), with overnight support (e.g., 22:00-06:00)
- Strict 24h coverage required (no gaps, no overlaps)
- Periods decoupled from light schedule (lights stay separate)
- Ramp = ramp-in duration (transition TO this period's setpoints)
- Free text for period names and details
- Dynamic timeline visualization
- Migration: Auto-convert from existing mode_parameters

**Technical Decisions Confirmed**:
- New `climate_periods` table (not reuse existing)
- Per-room + per-submode periods (like current mode_parameters)
- Reject invalid 24h coverage with error
- Backward compatibility: control loop checks new table first, falls back to legacy

### Research Findings
- **Priva**: Uses "stages" with absolute times and ramp transitions
- **Damatex**: Offers flexible period configuration
- Industry standard is time-based (not duration-based) periods
- Ramp transitions are critical for smooth climate control
- Current system: Duration-based (pre_day_minutes relative to light boundaries)

### Metis Review
**Identified Gaps (addressed)**:
1. Control loop backward compatibility: Added dual-read (new table first, fallback to legacy)
2. Migration strategy: Auto-convert using current day_start/night_start times
3. Validation: 24h coverage check API before save
4. Period scope: Per-room + per-submode (confirmed)
5. Edge cases: Overnight periods, DST handling, concurrent edits

---

## Work Objectives

### Core Objective
Implement a configurable climate periods system that allows users to define 1-7 time-based climate periods with full 24h coverage, replacing the current fixed 4-period approach.

### Concrete Deliverables
- Database: New `climate_periods` table with period rows
- Backend: API endpoints for CRUD + validation + active period lookup
- Frontend: New ClimatePeriodsTable component (replaces SetpointsTable)
- Frontend: Updated SetpointTimeline with dynamic period rendering
- Migration: Auto-convert mode_parameters to climate_periods
- Tests: Coverage validation, API tests, integration tests

### Definition of Done
- [ ] User can add/edit/delete 1-7 periods in table
- [ ] Times use hh:mm format with overnight support
- [ ] System validates 24h coverage (rejects gaps/overlaps)
- [ ] Timeline visualization renders dynamic periods
- [ ] Migration converts existing data automatically
- [ ] Control loop reads from new table with legacy fallback
- [ ] All tests pass

### Must Have
- Periods: Period (text), Start (hh:mm), End (hh:mm), Ramp (min), Heating (°C), Cooling (°C), VPD (kPa), CO2 (ppm), Details (text)
- 1-7 configurable periods
- Strict 24h coverage validation
- Overnight period support (end < start wraps to next day)
- Per-room + per-submode configuration
- Migration from existing mode_parameters

### Must NOT Have (Guardrails)
- MUST NOT modify light schedule (day_start_time, night_start_time stay in mode_parameters)
- MUST NOT change VPD cascade controller logic
- MUST NOT modify room mode transitions (Veg→Flower→Drying→Sleep)
- MUST NOT add new device types or hardware changes
- MUST NOT modify PID tuning parameters UI
- MUST NOT change existing setpoints API (backward compatibility)

---

## Verification Strategy (MANDATORY)

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
>
> ALL tasks in this plan MUST be verifiable WITHOUT any human action.

### Test Decision
- **Infrastructure exists**: YES (pytest, vitest)
- **Automated tests**: YES (TDD approach)
- **Framework**: pytest (backend), vitest (frontend)

### Agent-Executed QA Scenarios (MANDATORY — ALL tasks)

| Type | Tool | Verification Method |
|------|------|-------------------|
| **Backend API** | Bash (curl) | HTTP requests to endpoints |
| **Database** | Bash (psql) | Direct queries to verify schema |
| **Frontend** | Playwright | Browser automation |
| **CLI/TUI** | interactive_bash | Terminal commands |

**Example Scenarios**:

```
Scenario: Validate 24h coverage - valid periods
  Tool: Bash (curl)
  Preconditions: API running on localhost:8001
  Steps:
    1. curl -s -X POST http://localhost:8001/api/climate-periods/validate \
         -H "Content-Type: application/json" \
         -d '{"location":"Flower Room","cluster":"main","periods":[
               {"name":"Night","start":"22:00","end":"06:00","ramp":30,"heating":18,"cooling":25,"vpd":0.8,"co2":800,"details":""},
               {"name":"Day","start":"06:00","end":"22:00","ramp":30,"heating":22,"cooling":28,"vpd":1.2,"co2":1000,"details":""}
             ]}'
    2. Assert: response.valid === true
  Expected Result: Validation passes
  Evidence: JSON response captured

Scenario: Validate 24h coverage - gaps
  Tool: Bash (curl)
  Preconditions: API running
  Steps:
    1. curl -s -X POST http://localhost:8001/api/climate-periods/validate \
         -H "Content-Type: application/json" \
         -d '{"location":"Flower Room","cluster":"main","periods":[
               {"name":"Day","start":"08:00","end":"18:00","ramp":30,"heating":22,"cooling":28,"vpd":1.2,"co2":1000,"details":""}
             ]}'
    2. Assert: response.valid === false
    3. Assert: response.errors includes "24-hour coverage"
  Expected Result: Validation fails with coverage error

Scenario: Get active period at specific time
  Tool: Bash (curl)
  Steps:
    1. curl -s "http://localhost:8001/api/climate-periods/Flower%20Room/main/active?time=14:30"
    2. Assert: response.period.name === "Day"
    3. Assert: response.period.ramp_progress exists
  Expected Result: Returns correct active period

Scenario: Frontend table renders
  Tool: Playwright
  Preconditions: Dev server running on localhost:3001
  Steps:
    1. Navigate to /zone/Flower%20Room/main
    2. Wait for: .climate-periods-table
    3. Assert: 7 row inputs visible (max periods)
    4. Assert: Period column accepts text input
    5. Assert: Start/End columns show time picker
  Expected Result: Table renders with all columns
```

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Database + Backend Foundation):
├── Task 1: Create climate_periods database table
├── Task 2: Create ClimatePeriodRepository
├── Task 3: Create climate periods API routes (CRUD)
└── Task 4: Create validation endpoint (24h coverage)

Wave 2 (Backend Integration):
├── Task 5: Update control loop with dual-read (new table + fallback)
├── Task 6: Create migration endpoint/script
├── Task 7: Add active period lookup endpoint
└── Task 8: Write backend tests

Wave 3 (Frontend + Integration):
├── Task 9: Create ClimatePeriodsTable component
├── Task 10: Update SetpointTimeline for dynamic periods
├── Task 11: Update ZoneConfig to use new table
├── Task 12: Create frontend types for climate periods
├── Task 13: Add API client methods
├── Task 14: Frontend build and verify
└── Task 15: Integration testing

Critical Path: Task 1 → Task 3 → Task 5 → Task 9 → Task 14
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 (DB table) | None | 2, 3 | - |
| 2 (Repository) | 1 | 3 | - |
| 3 (API routes) | 2 | 4, 5, 6, 7 | - |
| 4 (Validation) | 3 | - | 8 |
| 5 (Control loop) | 3 | 15 | 4, 6, 7 |
| 6 (Migration) | 3 | - | 4, 5, 7 |
| 7 (Active lookup) | 3 | - | 4, 5, 6 |
| 8 (Backend tests) | 3, 4 | - | 5, 6, 7 |
| 9 (Frontend table) | 4, 6 | 11, 13 | 10, 12 |
| 10 (Timeline) | 9 | 14 | 11, 12, 13 |
| 11 (ZoneConfig) | 10 | 14 | 9, 12, 13 |
| 12 (Types) | None | 9 | 10, 11, 13 |
| 13 (API client) | 12 | 11 | 9, 10, 11 |
| 14 (Build) | 10, 11, 13 | 15 | - |
| 15 (Integration) | 5, 8, 14 | None | - |

---

## TODOs

- [ ] 1. Create climate_periods database table

  **What to do**:
  - Add new table in `cea_schema.sql` or create migration
  - Columns: id, location, cluster, mode_id, submode_id, period_name, start_time (HH:MM), end_time (HH:MM), ramp_minutes, heating_setpoint, cooling_setpoint, vpd_setpoint, co2_setpoint, details, created_at, updated_at
  - Add indexes for location/cluster/mode lookup
  - Add unique constraint for period order within location/cluster/mode

  **Must NOT do**:
  - MUST NOT modify existing setpoints table
  - MUST NOT change mode_parameters schema

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Database schema work is well-defined
  - **Skills**: []
    - No special skills needed

  **References**:
  - `Infrastructure/database/cea_schema.sql` - Existing schema patterns
  - `Infrastructure/automation-service/app/migrations.py:106-168` - mode_parameters columns (for reference)

  **Acceptance Criteria**:
  - [ ] Table created with all required columns
  - [ ] Indexes for query performance
  - [ ] Unique constraint prevents duplicate periods
  - [ ] psql \d climate_periods shows correct schema

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Verify table exists and has correct schema
    Tool: Bash (psql)
    Preconditions: Database accessible
    Steps:
      1. psql -U cea -d projectcea -c "\d climate_periods"
      2. Assert: Table exists
      3. Assert: Columns include period_name, start_time, end_time, ramp_minutes, etc.
    Expected Result: Table with correct schema
  ```

- [ ] 2. Create ClimatePeriodRepository

  **What to do**:
  - Create `app/repositories/climate_periods.py` following existing repository pattern
  - Methods: get_periods, get_period_by_id, save_periods, delete_periods, get_active_period
  - Implement 24h coverage validation logic
  - Handle overnight period calculation (end < start wraps)

  **Must NOT do**:
  - MUST NOT modify existing setpoint repository
  - MUST NOT change database connection logic

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Python async repository with validation logic
  - **Skills**: []
    - Standard Python async patterns

  **References**:
  - `app/repositories/setpoints.py` - Existing repository pattern to follow
  - `app/repositories/room_modes.py` - Similar structure

  **Acceptance Criteria**:
  - [ ] Repository class created with all CRUD methods
  - [ ] 24h coverage validation implemented
  - [ ] Overnight period handling (end < start = next day)
  - [ ] Unit tests for validation logic

- [ ] 3. Create climate periods API routes (CRUD)

  **What to do**:
  - Add routes in `app/routes/climate_periods.py`
  - Endpoints: GET/POST /climate-periods/{location}/{cluster}, PUT, DELETE
  - GET /climate-periods/{location}/{cluster}/validate
  - Use existing dependency injection pattern

  **Must NOT do**:
  - MUST NOT modify existing setpoints routes
  - MUST NOT change router prefix (break existing API)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: FastAPI route implementation
  - **Skills**: []

  **References**:
  - `app/routes/setpoints.py` - Route patterns
  - `app/routes/schedules/climate.py` - Similar CRUD pattern

  **Acceptance Criteria**:
  - [ ] All CRUD endpoints functional
  - [ ] Validation endpoint returns proper JSON
  - [ ] Error handling with appropriate status codes

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Create climate periods
    Tool: Bash (curl)
    Steps:
      1. curl -s -X POST http://localhost:8001/api/climate-periods/Flower%20Room/main \
           -H "Content-Type: application/json" \
           -d '{"periods":[...]}'
      2. Assert: HTTP 200
      3. Assert: Response contains saved periods
    Expected Result: Periods created successfully

  Scenario: Get climate periods
    Tool: Bash (curl)
    Steps:
      1. curl -s http://localhost:8001/api/climate-periods/Flower%20Room/main
      2. Assert: HTTP 200
      3. Assert: Response contains periods array
    Expected Result: Periods retrieved

  Scenario: Validate coverage - gap
    Tool: Bash (curl)
    Steps:
      1. curl -s -X POST http://localhost:8001/api/climate-periods/validate \
           -H "Content-Type: application/json" \
           -d '{"location":"Test","cluster":"main","periods":[
                 {"name":"Day","start":"08:00","end":"18:00","ramp":30,"heating":22,"cooling":28,"vpd":1.0,"co2":800,"details":""}
               ]}'
      2. Assert: response.valid === false
    Expected Result: Validation rejects incomplete coverage
  ```

- [ ] 4. Create 24h coverage validation endpoint

  **What to do**:
  - Implement validation logic that checks:
    - All periods have valid HH:MM times
    - Total coverage equals 1440 minutes (24h)
    - No overlaps between periods
    - Ramp <= period duration
  - Return detailed error messages

  **Must NOT do**:
  - MUST NOT save data during validation

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Complex validation logic
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] Validates complete 24h coverage
  - [ ] Detects overlaps
  - [ ] Handles overnight periods correctly
  - [ ] Returns detailed error messages

- [ ] 5. Update control loop with dual-read

  **What to do**:
  - Modify scheduler to check climate_periods first
  - Fall back to mode_parameters if no periods configured
  - Ensure backward compatibility for existing installations

  **Must NOT do**:
  - MUST NOT break existing setpoint resolution
  - MUST NOT change VPD cascade logic

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Control loop modification requires careful testing
  - **Skills**: []

  **References**:
  - `app/control/scheduler.py` - Period resolution logic
  - `app/services/schedule_state.py` - Setpoint fetching

  **Acceptance Criteria**:
  - [ ] Control loop reads from climate_periods when available
  - [ ] Falls back to mode_parameters when no periods
  - [ ] No regression in existing functionality

- [ ] 6. Create migration endpoint/script

  **What to do**:
  - Create API endpoint for auto-migration: POST /migrate
  - Convert mode_parameters (duration-based) to climate_periods (time-based)
  - Use day_start_time/night_start_time to generate absolute times
  - Support dry-run mode

  **Must NOT do**:
  - MUST NOT delete original mode_parameters data

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Data transformation logic
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] Migrates PRE_DAY → period before day_start
  - [ ] Migrates DAY → period from day_start to night_start
  - [ ] Migrates PRE_NIGHT → period before night_start
  - [ ] Migrates NIGHT → period after night_start
  - [ ] Dry-run shows converted periods without saving
  - [ ] Actual migration saves to climate_periods

- [ ] 7. Add active period lookup endpoint

  **What to do**:
  - GET /climate-periods/{location}/{cluster}/active?time=HH:MM
  - Returns the active period at the given time
  - Includes ramp progress if within ramp period

  **Must NOT do**:
  - MUST NOT change existing status endpoints

  **Acceptance Criteria**:
  - [ ] Returns correct period for any time of day
  - [ ] Handles overnight periods
  - [ ] Returns ramp progress info

- [ ] 8. Write backend tests

  **What to do**:
  - Test validation logic
  - Test CRUD operations
  - Test migration conversion
  - Test active period lookup

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Test implementation
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] pytest runs successfully
  - [ ] Coverage > 80%

- [ ] 9. Create ClimatePeriodsTable component

  **What to do**:
  - New component: `components/ClimatePeriodsTable.tsx`
  - Table with columns: Period, Start, End, Ramp, Heating, Cooling, VPD, CO2, Details
  - 1-7 rows (add/remove periods)
  - Time inputs with hh:mm picker
  - Number inputs with validation
  - Text inputs for Period and Details

  **Must NOT do**:
  - MUST NOT modify existing SetpointsTable (keep for reference initially)
  - MUST NOT change other UI components

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: UI component creation
  - **Skills**: ["frontend-ui-ux"]

  **References**:
  - `components/SetpointsTable.tsx` - Current implementation to replace
  - `components/DeviceManager.tsx` - Table patterns

  **Acceptance Criteria**:
  - [ ] Table renders with all columns
  - [ ] Can add/remove periods (1-7 range)
  - [ ] Time inputs validate hh:mm format
  - [ ] Number inputs validate ranges
  - [ ] Overnight periods work (end < start)

  **Agent-Executed QA Scenarios**:
  ```
  Scenario: Add new period
    Tool: Playwright
    Preconditions: Dev server, ZoneConfig page
    Steps:
      1. Navigate to /zone/Flower%20Room/main
      2. Click: "Add Period" button
      3. Assert: New row appears
      4. Fill: Period name = "Morning"
      5. Fill: Start = "06:00"
      6. Fill: End = "12:00"
      7. Assert: Row count increases by 1
    Expected Result: New period added

  Scenario: Remove period
    Tool: Playwright
    Steps:
      1. Click delete button on existing period
      2. Assert: Row count decreases
    Expected Result: Period removed

  Scenario: Overnight period input
    Tool: Playwright
    Steps:
      1. Fill: Start = "22:00"
      2. Fill: End = "06:00"
      3. Assert: No validation error
    Expected Result: Overnight period accepted
  ```

- [ ] 10. Update SetpointTimeline for dynamic periods

  **What to do**:
  - Modify `components/SetpointTimeline.tsx`
  - Accept periods array instead of fixed 4 periods
  - Render timeline segments dynamically based on configuration
  - Show ramp transitions between periods

  **Must NOT do**:
  - MUST NOT break existing timeline (keep backward compatible initially)

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Visualization update
  - **Skills**: ["frontend-ui-ux"]

  **References**:
  - `components/SetpointTimeline.tsx` - Current implementation

  **Acceptance Criteria**:
  - [ ] Timeline renders configured periods
  - [ ] Shows correct time ranges
  - [ ] Displays ramp transitions
  - [ ] Handles overnight wrap visually

- [ ] 11. Update ZoneConfig to use new table

  **What to do**:
  - Modify `pages/ZoneConfig.tsx`
  - Replace SetpointsTable with ClimatePeriodsTable
  - Integrate timeline update
  - Update save logic for new API

  **Must NOT do**:
  - MUST NOT remove light schedule controls
  - MUST NOT change mode selection UI

  **Recommended Agent Profile**:
  - **Category**: `visual-engineering`
    - Reason: Page integration
  - **Skills**: ["frontend-ui-ux"]

  **Acceptance Criteria**:
  - [ ] New table displayed in ZoneConfig
  - [ ] Save triggers correct API endpoint
  - [ ] Light schedule section unchanged
  - [ ] Timeline updates with periods

- [ ] 12. Create frontend types for climate periods

  **What to do**:
  - Add types in `types/climate-period.ts`
  - ClimatePeriod, ClimatePeriodsConfig, ValidationResult

  **Must NOT do**:
  - MUST NOT modify existing types

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Type definitions
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] Types match API schema
  - [ ] Include all fields from table columns

- [ ] 13. Add API client methods

  **What to do**:
  - Add methods to `services/api.ts`
  - getClimatePeriods, saveClimatePeriods, validateClimatePeriods, migrateClimatePeriods

  **Must NOT do**:
  - MUST NOT change existing API methods

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: API client update
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] All new endpoints have corresponding methods
  - [ ] Types properly used

- [ ] 14. Frontend build and verify

  **What to do**:
  - Run npm run build in frontend
  - Verify no TypeScript errors
  - Verify no lint errors

  **Recommended Agent Profile**:
  - **Category**: `unspecified-low`
    - Reason: Build verification
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] Build succeeds (exit 0)
  - [ ] No TypeScript errors
  - [ ] No console errors

- [ ] 15. Integration testing

  **What to do**:
  - Full end-to-end test
  - Create periods → Save → Verify in timeline
  - Test migration workflow

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration verification
  - **Skills**: []

  **Acceptance Criteria**:
  - [ ] Full workflow works end-to-end
  - [ ] Control loop receives correct setpoints
  - [ ] No regressions in existing features

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 1 | `feat(db): add climate_periods table` | cea_schema.sql, migration | psql verification |
| 2 | `feat(repo): add ClimatePeriodRepository` | app/repositories/climate_periods.py | Unit tests |
| 3 | `feat(api): add climate periods CRUD routes` | app/routes/climate_periods.py | curl tests |
| 5 | `feat(control): add dual-read for periods` | app/control/scheduler.py | Integration test |
| 6 | `feat(migration): add auto-migration endpoint` | app/routes/migration.py | Dry-run test |
| 9 | `feat(ui): add ClimatePeriodsTable component` | ClimatePeriodsTable.tsx | Playwright |
| 10 | `feat(timeline): dynamic period rendering` | SetpointTimeline.tsx | Playwright |
| 11 | `feat(zoneconfig): integrate new table` | ZoneConfig.tsx | Playwright |
| 14 | `chore: build and verify frontend` | dist/ | Build success |

---

## Success Criteria

### Verification Commands
```bash
# Backend API tests
cd Infrastructure/automation-service && pytest tests/ -v

# Frontend build
cd Infrastructure/frontend && npm run build

# Integration test
curl -s http://mothernode:8001/api/climate-periods/Flower%20Room/main | jq
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] 24h coverage validation works
- [ ] Control loop backward compatible
- [ ] Frontend builds without errors
- [ ] Timeline renders dynamic periods
- [ ] Migration converts existing data
