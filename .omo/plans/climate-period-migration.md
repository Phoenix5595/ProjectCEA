# Climate Period Migration: Ramp Bug Fix & Legacy Cleanup

## TL;DR

> **Quick Summary**: Fix the production ramp time bug (light ramps stuck at 60m instead of saved 15m) caused by ZoneConfig passing old climate ramp values to `saveRoomSchedule`, then clean up all legacy 4-period (PRE_DAY/DAY/PRE_NIGHT/NIGHT) code across backend, drop 22 obsolete DB columns via migration, and update documentation.
>
> **Deliverables**:
> - Ramp bug fix (one field-name change in ZoneConfig.handleSave + backend race condition hardening)
> - Removal of 22 legacy setpoint columns from `mode_parameters` (TypeScript type + Pydantic schema + SQL + DB migration)
> - Cleanup of old backend code (leaf_delta, scheduler, schedule_state, climate routes, setpoints repo)
> - Updated documentation across ARCHITECTURE.md, AGENTS.md files
>
> **Estimated Effort**: Medium
> **Parallel Execution**: YES — 3 waves
> **Critical Path**: Task 1 (ramp fix) → Task 3 (schema cleanup) → Task 5 (DB migration)

---

## Context

### Current State (verified Mar 26)

**Git State**:
- **HEAD (committed, deployed via dist/ built Mar 24)**: ZoneConfig ALREADY uses ClimatePeriodTimeline + ClimatePeriodsTable. SetpointTimeline.tsx and SetpointsTable.tsx are DELETED from repo. The frontend migration is DONE.
- **Working tree (unstaged)**: User's WIP improvements — type extraction to `climatePeriod.ts`, better timeline rendering with polylines, simplified CircularTimePicker/Dashboard. **MUST PRESERVE.**
- **Staged (index)**: HARMFUL revert of ZoneConfig back to deleted SetpointTimeline/SetpointsTable imports. **MUST DISCARD.**
- **Production (mothernode:8001)**: Shows new components correctly. User confirms this is what they see and want.

**What's Already Done**:
- ✅ ClimatePeriodsTable integrated into ZoneConfig (HEAD)
- ✅ ClimatePeriodTimeline integrated into ZoneConfig (HEAD)
- ✅ Climate periods loaded from API on mount (HEAD)
- ✅ Climate periods saved via `saveClimatePeriods` on save (HEAD)
- ✅ Control loop uses climate_periods (`ClimatePeriodResolver`)
- ✅ Backend API for climate_periods fully operational

**What Still Needs Fixing**:
- ❌ Ramp bug: `saveRoomSchedule({ramp_up_duration: p.ramp_up_minutes})` sends old climate ramp (60) not light ramp (15)
- ❌ Backend race conditions in save flow (events fire mid-transaction, cache staleness)
- ❌ 22 legacy setpoint columns in mode_parameters (DB + Pydantic + TypeScript)
- ❌ Old backend code: leaf_delta ClimateMode enum, scheduler validate_climate_schedule_conflicts, schedule_state pre_day/pre_night refs
- ❌ Documentation out of date

### Ramp Bug Root Cause (confirmed in HEAD code)

```javascript
// ZoneConfig.handleSave in HEAD (lines 97-104):
await apiClient.saveRoomSchedule(location, cluster, {
  ramp_up_duration: p.ramp_up_minutes ?? null,    // BUG: sends 60 (old climate ramp)
  ramp_down_duration: p.ramp_down_minutes ?? null, // BUG: sends 60 (old climate ramp)
})
// Should be: p.light_ramp_up_minutes / p.light_ramp_down_minutes (= 15)
```

**Compounding factors**:
1. Step 1 (`updateRoomParameters`) correctly sets light ramps to 15m and publishes `RAMP_TIMES_CHANGED`
2. Step 2 (`saveRoomSchedule`) overwrites room_schedule ramp with 60m (old climate ramp) and publishes `SCHEDULE_CHANGED` mid-transaction
3. `"schedules:all"` cache key NOT invalidated by step 1's `update_light_schedule_ramp_times`
4. Background consumers may read stale data if transaction hasn't committed

### Interview Decisions
- **Ramp bug visible in**: BOTH UI display and actual light behavior (60m instead of 15m)
- **Target architecture**: Full migration to `climate_periods` — remove old SetpointsTable, DAY/NIGHT/PRE_DAY/PRE_NIGHT model entirely
- **Light vs climate ramps**: Two independent concepts — light fade-in/out (CircularTimePicker) stays separate from climate period `ramp_minutes`
- **DB migration**: Remove old columns via Alembic (not just deprecate)
- **Rooms scope**: Flower Room only currently active with new system
- **Test strategy**: Full TDD (per AGENTS.md mandate)

---

## Work Objectives

### Core Objective
Fix the production light ramp bug and complete the legacy cleanup so only the `climate_periods` system manages climate setpoints, while `mode_parameters` retains only photoperiod/light fields.

### Definition of Done
- [ ] Light ramp saves of 15m in Flower Room result in actual 15m ramp behavior
- [ ] `mode_parameters` table has only photoperiod/light columns (no setpoint columns)
- [ ] No TypeScript or Python code references `day_heat_temp`, `night_heat_temp`, `pre_day_*`, `pre_night_*` setpoint fields
- [ ] `npm run build` succeeds with zero errors
- [ ] `ruff check . && ruff format --check .` passes
- [ ] All existing tests pass

### Must NOT Have (Guardrails)
- **DO NOT modify** ClimatePeriodsTable.tsx, ClimatePeriodTimeline.tsx, or climatePeriodTimeline.ts (user's active work)
- **DO NOT modify** CircularTimePicker.tsx or Dashboard.tsx (user's WIP improvements)
- **DO NOT remove** `day_start_time`, `night_start_time`, `light_ramp_up_minutes`, `light_ramp_down_minutes`, `main_light_intensity`, `supplemental_light_intensity` from mode_parameters
- **DO NOT break** the climate_periods API or control loop
- **DO NOT add** AI-slop abstractions, unnecessary error handling, or utility functions
- **DO NOT modify** the control engine, PID controllers, or device controllers

---

## Execution Strategy

```
Wave 1 (Start Immediately):
├── Task 1: Fix ramp bug in ZoneConfig + discard staged revert
└── Task 2: Fix backend save flow race conditions + cache

Wave 2 (After Wave 1):
├── Task 3: Clean up ModeParameters schema (TS + Python + SQL)
└── Task 4: Clean up backend legacy code (leaf_delta, scheduler, etc.)

Wave 3 (After Wave 2):
├── Task 5: Database migration (DROP columns)
└── Task 6: Documentation updates
```

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 1 | None | 3 | 2 |
| 2 | None | None | 1 |
| 3 | 1 | 5 | 4 |
| 4 | None (soft dep on 3) | 5 | 3 |
| 5 | 3, 4 | 6 | None |
| 6 | 5 | None | None |

---

## TODOs

- [x] 1. Fix ramp bug in ZoneConfig.handleSave + discard harmful staged revert

  **What to do**:
  - **First**: Discard the staged (index) modification to ZoneConfig.tsx that reverts to deleted SetpointTimeline/SetpointsTable imports. Run `git checkout -- Infrastructure/frontend/src/pages/ZoneConfig.tsx` to restore the working tree to the HEAD version (which is the correct one with ClimatePeriodTimeline + ClimatePeriodsTable).
  - **Then**: In ZoneConfig.tsx `handleSave`, change the `saveRoomSchedule` call:
    - `ramp_up_duration: p.ramp_up_minutes ?? null` → `ramp_up_duration: p.light_ramp_up_minutes ?? null`
    - `ramp_down_duration: p.ramp_down_minutes ?? null` → `ramp_down_duration: p.light_ramp_down_minutes ?? null`
  - Verify `npm run build` succeeds

  **Must NOT do**:
  - DO NOT modify ClimatePeriodsTable, ClimatePeriodTimeline, CircularTimePicker, or Dashboard
  - DO NOT refactor the save flow beyond the field name fix

  **Recommended Agent**: `task(category="quick", load_skills=["git-master"])`

  **References**:
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx` (HEAD version, lines 97-104) — the `saveRoomSchedule` call with the wrong ramp field
  - `Infrastructure/frontend/src/services/api.ts` — `saveRoomSchedule` method signature

  **Acceptance Criteria**:
  - [ ] Staged ZoneConfig revert discarded — `git diff --cached ZoneConfig.tsx` is empty
  - [ ] `handleSave` uses `p.light_ramp_up_minutes` for `ramp_up_duration`
  - [ ] `handleSave` uses `p.light_ramp_down_minutes` for `ramp_down_duration`
  - [ ] User's unstaged WIP changes to other files preserved (`git diff --stat` still shows CircularTimePicker, ClimatePeriodTimeline, etc.)
  - [ ] `npm run build` succeeds

  **Commit**: `fix(frontend): use light ramp fields in saveRoomSchedule instead of climate ramp`

---

- [x] 2. Fix backend save flow race conditions and cache invalidation

  **What to do**:
  - In `app/routes/room_modes.py` (~lines 210-238): add `"schedules:all"` to cache invalidation in `update_light_schedule_ramp_times` (currently only clears location/cluster-specific keys)
  - In `app/repositories/schedules.py` (around `create_schedule` ~line 234): ensure `SCHEDULE_CHANGED` events publish AFTER the transaction commits, not during
  - In `save_room_schedule`: verify it reads `mode_params_for_ramps` from committed data (no race with updateRoomParameters commit)
  - In `app/background_tasks.py` (~lines 310-354): verify event consumers handle rapid successive events properly

  **Must NOT do**:
  - DO NOT restructure the event bus architecture
  - DO NOT change the control loop or scheduler beyond cache/event fixes

  **Recommended Agent**: `task(category="unspecified-low")`

  **References**:
  - `Infrastructure/automation-service/app/routes/room_modes.py:210-238` — `update_room_parameters` handler that publishes `RAMP_TIMES_CHANGED`
  - `Infrastructure/automation-service/app/repositories/schedules.py:234` — `create_schedule` that publishes `SCHEDULE_CHANGED`
  - `Infrastructure/automation-service/app/background_tasks.py:310-354` — event consumers that reload scheduler
  - `Infrastructure/automation-service/app/events/__init__.py` — event type definitions

  **Acceptance Criteria**:
  - [ ] `"schedules:all"` cache key invalidated when schedule ramp times change
  - [ ] `SCHEDULE_CHANGED` events publish after transaction commit
  - [ ] `ruff check app/ && ruff format --check app/` passes
  - [ ] `python -m pytest tests/ -x` passes

  **Commit**: `fix(backend): fix schedule save race conditions and cache invalidation for ramp times`

---

- [x] 3. Clean up ModeParameters schema (frontend TypeScript + backend Pydantic + repository SQL)

  **What to do**:
  - **Frontend** (`src/types/modes.ts`): Remove 22 legacy setpoint fields from `ModeParameters` interface
  - **Backend Pydantic** (`app/schemas/room_modes.py`): Remove same from `ModeParameters` and `UpdateParametersRequest`
  - **Backend Repository** (`app/repositories/room_modes.py`): Update INSERT/UPDATE SQL (~lines 250-479) to only handle remaining fields
  - **Startup schema** (`app/migrations.py`): Update CREATE TABLE IF NOT EXISTS to reflect new schema

  **Columns to DROP (22)**:
  `ramp_up_minutes`, `ramp_down_minutes`, `pre_day_minutes`, `pre_night_minutes`,
  `pre_day_heat_temp`, `pre_day_cool_temp`, `pre_day_vpd`, `pre_day_co2`,
  `day_heat_temp`, `day_cool_temp`, `day_vpd`, `day_co2`, `day_leaf_delta`,
  `pre_night_heat_temp`, `pre_night_cool_temp`, `pre_night_vpd`, `pre_night_co2`,
  `night_heat_temp`, `night_cool_temp`, `night_vpd`, `night_co2`, `night_leaf_delta`

  **Columns to KEEP (6 + identity + timestamps)**:
  `day_start_time`, `night_start_time`, `light_ramp_up_minutes`, `light_ramp_down_minutes`, `main_light_intensity`, `supplemental_light_intensity`

  **Must NOT do**:
  - DO NOT modify ClimatePeriodsTable or any user WIP files
  - DO NOT rename the `mode_parameters` table itself
  - DO NOT alter the `climate_periods` table or its repository

  **Recommended Agent**: `task(category="unspecified-high")`

  **References**:
  - `Infrastructure/frontend/src/types/modes.ts` — `ModeParameters` TypeScript interface
  - `Infrastructure/automation-service/app/schemas/room_modes.py` — Pydantic models
  - `Infrastructure/automation-service/app/repositories/room_modes.py:250-479` — INSERT/UPDATE SQL with all 22 columns
  - `Infrastructure/automation-service/app/migrations.py:108-163` — CREATE TABLE with all columns and defaults

  **Acceptance Criteria**:
  - [ ] `ModeParameters` TypeScript interface has only kept fields
  - [ ] `ModeParameters` Pydantic model has only kept fields
  - [ ] Repository SQL only references remaining columns
  - [ ] `migrations.py` CREATE TABLE only includes remaining columns
  - [ ] `npm run build` succeeds
  - [ ] `ruff check .` passes
  - [ ] No `day_heat_temp`, `night_heat_temp`, `pre_day_*`, `pre_night_*` in schemas or repos

---

- [x] 4. Clean up backend legacy climate code (control, routes, schedule state)

  **What to do**:
  - `app/control/leaf_delta.py`: Remove/update `ClimateMode` enum (only DAY/NIGHT). Use `lsp_find_references` first.
  - `app/control/scheduler.py:696-772`: Remove `validate_climate_schedule_conflicts()` (assumes 4-period structure)
  - `app/services/schedule_state.py`: Remove `pre_day_duration` / `pre_night_duration` references
  - `app/routes/schedules/climate.py`: Remove old pre_day/pre_night duration management
  - `app/schemas/schedules.py`: Remove `pre_day_duration` / `pre_night_duration` from `ClimateScheduleCreate`
  - `app/repositories/schedules.py`: Remove `get_climate_schedule()` pre_day/pre_night handling
  - `app/repositories/setpoints.py`: Remove mode-based lookups (DAY/NIGHT/PRE_DAY/PRE_NIGHT) if present

  **Must NOT do**:
  - DO NOT modify `climate_resolver.py`, `setpoint_manager.py`, `climate_periods.py` (NEW system, working)
  - DO NOT modify `control_engine.py` beyond removing calls to deleted functions
  - DO NOT modify `light_ramp_calculator.py`

  **Recommended Agent**: `task(category="unspecified-high")`

  **References**:
  - `app/control/leaf_delta.py` — `ClimateMode` enum
  - `app/control/scheduler.py:696-772` — `validate_climate_schedule_conflicts()`
  - `app/services/schedule_state.py` — pre_day/pre_night state building
  - `app/routes/schedules/climate.py` — old climate schedule routes
  - `app/schemas/schedules.py` — `ClimateScheduleCreate` schema
  - `app/repositories/schedules.py` — `get_climate_schedule()`
  - `app/repositories/setpoints.py` — mode-based lookups

  **Acceptance Criteria**:
  - [ ] `ClimateMode` enum removed or replaced
  - [ ] `validate_climate_schedule_conflicts()` removed
  - [ ] No `pre_day_duration` / `pre_night_duration` in schedule_state.py
  - [ ] No `PRE_DAY` / `PRE_NIGHT` string literals in routes/services/control
  - [ ] `ruff check .` passes, `pytest tests/ -x` passes

  **Commit (3 + 4 together)**: `refactor: remove legacy 4-period climate code and 22 setpoint columns from schemas`

---

- [x] 5. Database migration — DROP old columns from mode_parameters

  **What to do**:
  - Create `alembic/versions/002_drop_legacy_setpoint_columns.py`
  - `ALTER TABLE mode_parameters DROP COLUMN IF EXISTS` for all 22 columns
  - Include downgrade function that re-adds columns with original defaults (from migrations.py:108-163)
  - Handle `pre_day_duration` / `pre_night_duration` in `schedules` table if present
  - Check `alembic/versions/001_baseline.py` for mode CHECK constraints referencing PRE_DAY/PRE_NIGHT
  - Run `alembic upgrade head` to verify

  **Must NOT do**:
  - DO NOT drop kept columns (`day_start_time`, `night_start_time`, `light_ramp_*`, `*_light_intensity`)
  - DO NOT drop the `mode_parameters` table itself
  - DO NOT modify the `climate_periods` table
  - DO NOT run on production (deployment handles that)

  **Recommended Agent**: `task(category="unspecified-low")`

  **References**:
  - `Infrastructure/automation-service/alembic/versions/001_baseline.py` — existing migration pattern
  - `Infrastructure/automation-service/app/migrations.py:108-163` — column types and defaults for downgrade

  **Acceptance Criteria**:
  - [ ] Migration file exists at `alembic/versions/002_drop_legacy_setpoint_columns.py`
  - [ ] Drops all 22 columns with `IF EXISTS` safety
  - [ ] Has working downgrade function
  - [ ] `alembic upgrade head` succeeds
  - [ ] `\d mode_parameters` shows only kept columns

  **Commit**: `migrate(db): drop 22 legacy setpoint columns from mode_parameters`

---

- [x] 6. Update documentation

  **What to do**:
  - Archive `ARCHITECTURE.md` and `ARCHITECTURE_SCHEMATIC.md` to `archive/` with dated filenames
  - Update sections referencing DAY/NIGHT/PRE_DAY/PRE_NIGHT setpoint model
  - Update `Infrastructure/frontend/AGENTS.md`: component table (remove SetpointTimeline/SetpointsTable, add ClimatePeriodsTable/ClimatePeriodTimeline), ZONECONFIG SAVE section (now 3-step: mode params → room schedule → climate periods)
  - Update `Infrastructure/frontend/README.md`: remove "Mode-aware Setpoints: DAY/NIGHT/TRANSITION", remove/update "Setpoint Timeline Behavior" section
  - Verify consistency in `Infrastructure/automation-service/AGENTS.md` and root `AGENTS.md`

  **Must NOT do**:
  - DO NOT create new documentation files
  - DO NOT rewrite from scratch — surgical updates only

  **Recommended Agent**: `task(category="writing")`

  **Acceptance Criteria**:
  - [ ] Archived copies in `archive/` with dated filenames
  - [ ] Zero grep matches for `PRE_DAY|PRE_NIGHT|SetpointTimeline|SetpointsTable` in updated docs
  - [ ] Frontend AGENTS.md lists ClimatePeriodsTable and ClimatePeriodTimeline
  - [ ] ZONECONFIG SAVE documents 3-step save

  **Commit**: `docs: update architecture docs to reflect climate_periods migration`

---

## Commit Strategy

| After Task | Message | Key Files | Verification |
|------------|---------|-----------|--------------|
| 1 | `fix(frontend): use light ramp fields in saveRoomSchedule` | ZoneConfig.tsx | `npm run build` |
| 2 | `fix(backend): fix schedule save race conditions and cache` | schedules.py, room_modes.py | `ruff check && pytest` |
| 3+4 | `refactor: remove legacy 4-period climate code and schema columns` | modes.ts, room_modes.py, schemas, leaf_delta, scheduler, etc. | `npm run build && ruff check && pytest` |
| 5 | `migrate(db): drop 22 legacy setpoint columns` | alembic migration, migrations.py | `alembic upgrade head` |
| 6 | `docs: update architecture docs for climate_periods migration` | ARCHITECTURE*.md, AGENTS.md, README.md | grep verification |

---

## Success Criteria

```bash
# Frontend builds
cd Infrastructure/frontend && npm run build  # Expected: exit 0

# Backend lint
cd Infrastructure && ruff check . && ruff format --check .  # Expected: exit 0

# Backend tests
cd Infrastructure/automation-service && python -m pytest tests/ -x  # Expected: all pass

# DB migration
cd Infrastructure/automation-service && alembic upgrade head  # Expected: exit 0

# No legacy references in code
grep -rn "day_heat_temp\|night_heat_temp\|pre_day_heat\|pre_night_heat" \
  Infrastructure/automation-service/app/schemas/ \
  Infrastructure/automation-service/app/repositories/room_modes.py \
  Infrastructure/frontend/src/types/modes.ts  # Expected: no matches

# No legacy references in docs
grep -rn "PRE_DAY\|PRE_NIGHT\|SetpointTimeline\|SetpointsTable" \
  ARCHITECTURE.md Infrastructure/frontend/AGENTS.md Infrastructure/frontend/README.md  # Expected: no matches
```
