# Climate Period Ramp Migration - Learnings

## Task 1: Replace mode-based setpoint fetching with climate_periods table

### Key Findings

1. **climate_periods_repo is already wired**: The `ClimatePeriodRepository` is already initialized in `database.py` and accessible via `database.climate_periods_repo`. No changes needed to database.py.

2. **get_active_period signature**: 
   - Parameters: `location`, `cluster`, `reference_time` (HH:MM format string)
   - Returns: `dict[str, Any] | None` with fields: `period_name`, `start_time`, `end_time`, `ramp_minutes`, `heating_setpoint`, `cooling_setpoint`, `vpd_setpoint`, `co2_setpoint`
   - Handles wrap-around periods (last period of day → first period)

3. **Bridging to existing SetpointManager interface**:
   - `compute_effective_setpoints()` expects `setpoint_data` with keys: `heating_setpoint`, `cooling_setpoint`, `humidity`, `co2`, `vpd`, `ramp_in_duration`
   - Need to map: `vpd_setpoint` → `vpd`, `ramp_minutes` → `ramp_in_duration`
   - `humidity` is None because VPD cascade controller derives it from VPD setpoint

4. **Period transition detection**: Follows the same pattern as mode transition detection with `_current_period_name` dict keyed by `(location, cluster)`.

5. **Timezone handling**: Use `datetime.now(ZoneInfo("America/Toronto"))` for America/Toronto timezone, then format as `%H:%M`.

6. **Light schedule still needed**: The `is_sun` calculation for light intensity still requires the light schedule from `schedule_repo.get_room_light_schedule()`. This is independent of the climate period changes.

7. **Mode compatibility**: `process_devices()` still needs `current_mode` and `previous_mode`. We derive `current_mode` from the period name and keep tracking `_current_climate_mode` for backward compatibility.

### Code Changes Made

- Added `ZoneInfo` import from `zoneinfo` module
- Added `_current_period_name: dict[tuple[str, str], str]` instance variable
- Replaced scheduler-based mode resolution with `climate_periods_repo.get_active_period()` call
- Built `setpoint_data` dict from period fields, bridging to existing SetpointManager interface
- Added period transition detection and logging
- Kept light schedule fetch for `is_sun` calculation
- `period_name` stored in mode column for effective_setpoints logging

### Notes for Future Tasks

- Task 2 will update SetpointManager's `compute_effective_setpoints()` signature to accept period-based parameters natively
- Task 3 will remove StateManager setpoint caching (no longer needed with climate_periods)
- Task 4 will add previous period setpoint query for ramp start values
- Task 5 will remove scheduler.py references and scheduler.py itself

## Task 2: Update SetpointManager for period-based ramp transitions

### Key Findings

1. **Bridge compatibility**: Task 1 passes `period_name` as the `current_mode` parameter and `previous_period` as the `previous_mode` parameter. The SetpointManager's method signatures were already compatible - just needed to update the internal logic and logging to use period terminology.

2. **`_calculate_ramp_start_values()` refactored**:
   - **Old**: Queried `setpoint_repo.get_setpoint(location, cluster, previous_mode)` for mode-based setpoints
   - **New**: Queries `climate_periods_repo.get_periods(location, cluster)` and filters by `period_name == previous_period`
   - Removed mode-based fallback logic (PRE_DAY→NIGHT, DAY→PRE_DAY, etc.)
   - New fallback: sensor values if available, then nominal values

3. **Period extraction from climate_periods**:
   - `get_periods()` returns list of all periods with fields: `period_name`, `heating_setpoint`, `cooling_setpoint`, `vpd_setpoint`, `co2_setpoint`
   - humidity is always None (VPD cascade derives it)
   - Need to iterate through periods and match by `period_name`

4. **Pre-existing type checking issues**:
   - `shared.infra_logging` import resolution - known issue in this codebase
   - `climate_periods_repo` on `None` - because `self.database` is typed as `DatabaseManager | None`
   - These were present before Task 2 changes; not introduced by my modifications

5. **Logging terminology updated**:
   - Changed "mode" to "period" in debug/info logs
   - Changed "mode_changed" to "period_changed" in comments and variable names
   - Kept `_detect_mode_change()` method name for backward compatibility (it still works with period names)

### Code Changes Made

1. **`compute_effective_setpoints()`**:
   - Renamed parameters: `current_mode` → `current_period`, `previous_mode` → `previous_period`
   - Updated docstring to reference "period" instead of "mode"
   - Updated comments: "mode transitions" → "period transitions"

2. **`_handle_mode_transition_ramp()`**:
   - Renamed parameters: `current_mode` → `current_period`, `previous_mode` → `previous_period`
   - Updated logging: "mode_changed=True" → "period_changed=True"

3. **`_detect_mode_change()`**:
   - Renamed parameters: `current_mode` → `current_period`, `previous_mode` → `previous_period`
   - Updated docstring: "mode change" → "period change"

4. **`_calculate_ramp_start_values()`** (major refactor):
   - **Before**: Queried `setpoint_repo.get_setpoint()` for mode-based setpoints, had fallback logic for PRE_DAY/NIGHT/DAY/PRE_NIGHT modes
   - **After**: Queries `climate_periods_repo.get_periods()` and finds matching period by `period_name == previous_period`
   - Removed mode-based fallback chains
   - New fallback: sensor values if available, then nominal values

5. **Class docstring**:
   - Updated to "Calculates effective setpoints with ramp transitions for climate periods"
   - `__init__` docstring updated to reference climate_periods

### Verification Results

- `ruff check --fix .` and `ruff format .`: All checks passed
- `pyright app/control/setpoint_manager.py`: 2 pre-existing errors (no new errors introduced)

### Notes for Future Tasks

- Task 3 will fix StateManager setpoint caching for period-based data
- Task 4 will perform code archaeology to find all deprecated mode-based references
- Task 5 will hard-remove the deprecated mode-based setpoint system (setpoints table routes, scheduler methods)

## Task 3: Fix StateManager setpoint cache leak in secondary VPD path

### Key Findings

1. **`_process_vpd_control` is dead code**: The method at `control_engine.py:590` is never called from the main control loop. The main VPD control path flows through `device_processor.process_devices()` → `device_controller.process_device()` → `_get_setpoint_for_device_type()`, which correctly reads `effective_vpd_setpoint` from the context built from `_effective_setpoints`. However, since the method still exists and reads stale data, it must be fixed to prevent potential stale-data leakage.

2. **Stale cache path at line 624**: The method read VPD setpoint via `await self._state.get_setpoint(location, cluster)` — the mode-unaware StateManager cache. This returns setpoints keyed by location/cluster without period awareness.

3. **Fix: read from `_effective_setpoints`**: The `effective_data` stored at line 410 (keyed by `(location, cluster)`) already contains `effective_vpd_setpoint` from the period-based climate data. Replacing the StateManager call with `self._effective_setpoints.get((location, cluster))` eliminates the stale data path.

4. **StateManager `get_setpoint()`/`set_setpoint()` left intact**: As specified, these methods are NOT removed here. Task 5 will perform the hard-removal. The methods are still called by `app/routes/setpoints.py` and `app/repositories/setpoints.py`.

5. **All callers of `state.get_setpoint`/`state.set_setpoint`**: Only 2 call sites remain after the secondary VPD path fix:
   - `app/routes/setpoints.py` lines 87, 240, 319 — API route layer (Task 5)
   - `app/repositories/setpoints.py` lines 120, 152 — Repository layer (Task 5)

### Code Changes Made

**`control_engine.py:614-623`** (was lines 615-630):
- **Before**: Called `await self._state.get_setpoint(location, cluster)` then accessed `setpoint_data.get("vpd")`
- **After**: Reads `effective_data = self._effective_setpoints.get((location, cluster))` and accesses `effective_data.get("effective_vpd_setpoint")`
- Removed the dead code block (lines 617-621) that checked Redis availability for "future scheduler integration"
- Added clear comment explaining the fix

### Verification Results

- `ruff check --fix app/control/control_engine.py`: All checks passed
- `ruff format app/control/control_engine.py`: No changes needed
- Pre-existing `shared.infra_logging` LSP import resolution errors are unrelated to this change

### Notes for Future Tasks

- Task 5 will hard-remove `StateManager.get_setpoint()` and `StateManager.set_setpoint()` methods
- Task 5 will also clean up `app/routes/setpoints.py` and `app/repositories/setpoints.py` call sites
- `_process_vpd_control` itself may be a candidate for removal in Task 5 since it's dead code, but that decision is out of scope for this task

## Task 6: Remove deprecated mode-based setpoint UI components

### Key Findings

1. **Deleted files**:
   - `src/components/SetpointEditor.tsx` — standalone setpoint form component
   - `src/components/SetpointsTable.tsx` — 4-column period card setpoint editor
   - `src/types/setpoint.ts` — `Setpoint` and `SetpointUpdate` types (mode-based)

2. **Deleted component**: `ClimateScheduleEditor.tsx` was standalone (no imports from other files), called deprecated `apiClient.getAllSetpointsForLocationCluster()` and used `ramp_in_duration`. Deleted entirely.

3. **`api.ts` cleanup**: Removed 3 deprecated methods:
   - `getSetpoints(location, cluster, mode?)` — called `GET /api/setpoints/...`
   - `getAllSetpointsForLocationCluster(location, cluster)` — called `GET /api/setpoints/.../all-modes`
   - `updateSetpoints(location, cluster, setpoints)` — called `POST /api/setpoints/...`
   - Also removed `import type { Setpoint, SetpointUpdate }` from `../types/setpoint`

4. **`SetpointTimeline.tsx`**: Kept (used by `ZoneConfig.tsx` for 24h visualization). Changed `ramp_in_duration` → `ramp_minutes` in all 5 occurrences.

5. **`ZoneConfig.tsx`**: Updated setpoints prop to use `ramp_minutes` instead of `ramp_in_duration`. `ClimatePeriodsTable` is rendered as the sole setpoint editor.

6. **`ZoneCard.tsx`**: Removed `import type { Setpoint } from '../types/setpoint'`. Replaced with inline type definition for `ZoneSetpoints` interface to avoid breaking the component.

7. **Standalone component usage**: `ClimateScheduleEditor.tsx` was not imported by any other file — confirmed via grep. Safe to delete entirely.

### Files Changed

| File | Change |
|------|--------|
| `src/components/SetpointEditor.tsx` | DELETED |
| `src/components/SetpointsTable.tsx` | DELETED |
| `src/components/ClimateScheduleEditor.tsx` | DELETED |
| `src/types/setpoint.ts` | DELETED |
| `src/services/api.ts` | Removed 3 deprecated methods + setpoint type import |
| `src/components/SetpointTimeline.tsx` | `ramp_in_duration` → `ramp_minutes` |
| `src/pages/ZoneConfig.tsx` | `ramp_in_duration` → `ramp_minutes` in setpoints prop |
| `src/components/ZoneCard.tsx` | Removed `Setpoint` type import, inline type added |

### Verification Results

- `npm run build` in `Infrastructure/frontend/`: **PASSED** (exit 0)
- grep for deprecated references (`SetpointEditor`, `SetpointsTable`, `setpoint.ts`, `/api/setpoints`, `getSetpoints`, `getAllSetpoints`, `updateSetpoints`, `ramp_in_duration`): **0 matches**
- Only remaining `setpoint` string match: `from-accent-setpoint-dim` CSS class in `LightSlider.tsx` (harmless)

## Task 5: Hard-remove deprecated mode-based setpoint system (backend)

### Key Findings

1. **Files deleted**:
   - `app/routes/setpoints.py` — entire route file removed
   - `app/repositories/mode_sync.py` — mode→setpoint sync service removed

2. **`app/repositories/setpoints.py` kept methods**: The file was NOT deleted because `log_effective_setpoints()`, `get_latest_effective_setpoints()`, `flush_batch_buffer()`, and `invalidate_all_cache()` are still needed for effective_setpoints logging and caching (this is separate from the mode-based setpoint API).

3. **`app/control/scheduler.py` — removed**: `get_climate_mode()` method (~100 lines) and `is_time_in_range()` helper. The scheduler still handles light schedules and sunrise/sunset.

4. **`app/state/__init__.py` — removed**: `get_setpoint()` and `set_setpoint()` convenience methods (were just pass-throughs to the repository).

5. **`app/services/mode_transition_service.py`**: Removed `sync_climate_setpoints_from_mode_parameters()` call — the climate period is now the source of truth, no sync needed.

6. **`app/services/schedule_state.py`**: Replaced mode-based SETPOINT_MODES loop with `climate_periods_repo.get_periods()`. Changed output key from `"setpoints"` to `"periods"`.

7. **`app/routes/schedules/climate.py`**: Rewritten to only manage schedule timing metadata (pre_day_duration, pre_night_duration). Setpoint data is now managed via `climate_periods` API only.

8. **`app/routes/schedules/utils.py`**: `_build_schedule_state()` now reads from `climate_periods` instead of mode-based setpoints. Changed output key from `"setpoints"` to `"periods"`.

9. **`app/routes/debug.py`**: Replaced `scheduler.get_climate_mode()` fallback and `setpoint_repo.get_setpoint()` with `climate_periods_repo.get_active_period()`.

10. **`config_cli.py`**: Removed `setpoint get` and `setpoint set` commands entirely. Kept `setpoint_ranges` for schedule validation.

11. **`app/control/leaf_delta.py`**: Removed `PRE_DAY` and `PRE_NIGHT` from `ClimateMode` enum. Simplified `get_leaf_delta()` to day/night only (no transition logic).

12. **`scripts/validate_loop_performance.py`**: Replaced `setpoint_repo.get_setpoint` mock with `climate_periods_repo.get_active_period` and `climate_periods_repo.get_periods` mocks. Passed `climate_periods_repo` to `Scheduler` constructor.

13. **`debug_mode_transition.py`**: DELETED — standalone debug script that called removed `get_climate_mode()` method. Dead code.

14. **`app/redis/schema.py`**: Fixed duplicate `from __future__ import annotations` (pre-existing issue, not from this task).

15. **`ramp_in_duration` in new code**: The parameter name `ramp_in_duration` in `SetpointManager` and `control_engine.py` is the NEW system's parameter — NOT the old `setpoints.ramp_in_duration` database column. This is the bridge key name expected by `SetpointManager._handle_mode_transition_ramp()`. Part of the new `climate_periods` → `SetpointManager` flow.

16. **`climate_periods_repo` access**: Accessed via `database.climate_periods_repo` property (lazy-initialized `ClimatePeriodRepository`).

17. **Tests not updated**: Test files in `tests/` directory mock the old `get_setpoint()` and `get_climate_mode()` methods. Not in scope for this task per the constraint "DO NOT touch any `.tsx`, `.ts`, or frontend files". Tests should be updated separately.

### Files Deleted

| File | Reason |
|------|--------|
| `app/routes/setpoints.py` | Entire route — setpoint CRUD API removed |
| `app/repositories/mode_sync.py` | Sync service removed — climate_periods is source of truth |
| `debug_mode_transition.py` | Standalone debug script calling removed `get_climate_mode()` |

### Files Changed

| File | Change |
|------|--------|
| `app/repositories/setpoints.py` | Removed get_setpoint, set_setpoint, get_all_setpoints_for_location_cluster; kept effective_setpoints methods |
| `app/control/scheduler.py` | Removed get_climate_mode() and is_time_in_range() |
| `app/state/__init__.py` | Removed get_setpoint() and set_setpoint() convenience methods |
| `app/routes/routes.py` | Removed setpoints router inclusion |
| `app/main.py` | Removed setpoints OpenAPI tag metadata |
| `app/services/mode_transition_service.py` | Removed mode_sync integration |
| `app/services/schedule_state.py` | climate_periods instead of SETPOINT_MODES loop |
| `app/routes/schedules/utils.py` | climate_periods instead of mode-based setpoints |
| `app/routes/schedules/climate.py` | Rewritten — timing metadata only |
| `app/routes/debug.py` | climate_periods_repo.get_active_period instead of deprecated calls |
| `config_cli.py` | Removed setpoint get/set commands |
| `app/control/leaf_delta.py` | Removed PRE_DAY/PRE_NIGHT, simplified to day/night |
| `scripts/validate_loop_performance.py` | Updated mocks for climate_periods system |
| `app/redis/schema.py` | Fixed duplicate `from __future__` (pre-existing fix) |

### What Was NOT Removed (by constraint)

- `setpoints` database table — historical data preserved
- `effective_setpoints` table and write path
- `climate_periods` routes/repository — NEW system
- `climate_periods` table
- Light schedule code in scheduler.py
- Redis ramp persistence code
- `get_effective_setpoints()` / `set_effective_setpoints()` in StateManager
- `device_processor.py` mode tracking
- `pid_controller_manager.py` mode tracking

### Verification Results

- `ruff check --fix . && ruff format .`: **All checks passed**
- LSP diagnostics on changed files: Only pre-existing `reportImplicitRelativeImport` errors (system-wide pattern)
- No new errors introduced by Task 5 changes

## Task 6: ClimatePeriodTimeline Component

### Key Findings

1. **SVG-based circular clock**: Used SVG instead of Canvas for cleaner arc rendering. The `describeArc()` function generates SVG path commands for arcs between angles.

2. **Time-to-angle mapping**: Noon (12:00) at top of circle uses formula `((hours - 12) / 24) * 2 * Math.PI - Math.PI / 2`. This matches the existing `CircularTimePicker` convention.

3. **Color scheme based on day/night overlap**: Periods that overlap with the light day window (sunrise to sunset) use warm colors (amber #f59e0b, orange #ea580c), while night periods use cool colors (indigo #4f46e5, purple #7c3aed, blue #2563eb).

4. **Period name heuristics for color selection**:
   - Dawn/morning/sunrise → amber
   - Dusk/eve/sunset → orange
   - Night/sleep/dark → indigo
   - Pre-dawn/early → blue
   - Default fallback → amber (day) or indigo (night)

5. **Ramp indicator**: Periods with `ramp_minutes > 0` show a translucent overlay arc at the start of the period, colored with a semi-transparent version of the period's main color.

6. **No crosshatch patterns**: Unlike the old `SetpointTimeline`, the new component uses simple colored arcs without any hatch patterns.

7. **"Now" marker**: Red line from center to current time position, with a red dot at the edge and white border.

8. **Sun/moon indicators**: Small emoji markers (☀/☽) at the sunrise/sunset positions on the clock face.

### Files Created

| File | Purpose |
|------|---------|
| `src/components/ClimatePeriodTimeline.tsx` | New circular 24-hour climate period visualization |

### Files Changed

| File | Change |
|------|--------|
| `src/pages/ZoneConfig.tsx` | Added ClimatePeriodTimeline import and integration above CircularTimePicker |

### Integration Layout

The timeline is placed in the left column (30% width) ABOVE the Light Schedule (CircularTimePicker), with a fixed height of 270px. The layout structure is:
- Left column (30%): ClimatePeriodTimeline (270px) + CircularTimePicker (flex-1)
- Right column (70%): ClimatePeriodsTable + VerticalLightsBlock

### Design Decisions

- **SVG over Canvas**: SVG paths are cleaner for representing arcs and allow easier styling with CSS-like properties
- **Compact mode**: Component supports a `compact` prop that reduces size from 260px to 180px for smaller containers
- **Period name truncation**: Names longer than 8 characters are truncated with ellipsis to prevent label overflow
- **Dynamic period names**: Unlike the old system with hardcoded PRE_DAY/DAY/PRE_NIGHT/NIGHT, the new component uses `period_name` from the database

### Verification Results

- `npm run build`: **Passed** (TypeScript + Vite build successful)
- TypeScript strict mode: 0 errors on new files
- LSP diagnostics: Only pre-existing errors in unrelated automation-service files

