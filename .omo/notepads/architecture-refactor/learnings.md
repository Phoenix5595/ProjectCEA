
## 2026-07-12: Legacy cleanup (frontend + schemas + comments)

### Changes made

1. **LightIntensity.tsx** — Removed `fetchLightsAndStatusLegacy()` (lines 38-90).
   - Verified no other call sites exist in the frontend (only self-references inside the component).
   - Updated `fetchLightsAndStatus()` to remove legacy fallback paths (empty-rows fallback and catch-block fallback).
   - Removed `fetchLightsAndStatusLegacy` from the `useCallback` dependency array.
   - Frontend build (`npx tsc --noEmit && npm run build`) passes.

2. **room_modes.py** — Added deprecation comments to `main_light_intensity` and `supplemental_light_intensity` fields.
   - `ModeParameters.main_light_intensity` (line 46)
   - `ModeParameters.supplemental_light_intensity` (line 47)
   - `UpdateParametersRequest.main_light_intensity` (line 78)
   - `UpdateParametersRequest.supplemental_light_intensity` (line 79)
   - Comment: `# DEPRECATED: use light_target_intensity table`
   - DB columns intentionally left in place per safety rule.

3. **schedules.py** — `ClimateScheduleUpdate` removal.
   - Grepped entire automation-service: zero imports of `ClimateScheduleUpdate`.
   - The class does not exist in `schedules.py` (already removed or never present). No action required.

4. **container.py** — Updated stale comment at line 159.
   - Old: `# 6. Initialize scheduler with schedules from database (+ synthetic SUN rows from room_schedule)`
   - New: `# 6. Initialize scheduler with schedules from database merged with config (synthetic SUN rows from merge_schedules_with_config)`
   - Reflects actual code path: `merge_schedules_with_config(db_schedules, self.config)` generates synthetic rows, not `room_schedule` directly.

5. **schedule_state.py** — Updated comments referencing removed `SchedulesMixin`.
   - Removed "T10 cleanup" reference from docstring and log message.
   - Rephrased for clarity: "SchedulesMixin was removed; this function is kept as a no-op..."

### Verification

- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- `npx tsc --noEmit && npm run build` in `Infrastructure/frontend` — **Build succeeded**.

### Notes

- The `shared.infra_logging` LSP import-resolution error in `schedule_state.py` is pre-existing and unrelated to these changes.

## 2026-07-12 — Wave 1 Dead Code Removal

### Files Deleted
- `app/ai_export.py` — `export_training_data()` and `get_available_ranges()` had zero callers (verified via codegraph_callers). Only referenced in `alembic/staged/README.md`.
- `app/routes/rules.py` — 66 lines, all 5 handlers were stubs returning empty/success. No imports found outside `app/routes/routes.py`.
- `app/routes/schedules/climate.py` — 79 lines, legacy no-op endpoints. Header explicitly stated "legacy - maintained for API compatibility". `climate_periods` is canonical.
- `alembic/staged/004_drop_legacy_setpoint_columns.py` — Staged migration never applied. Referenced only in `alembic/staged/README.md` and a comment in `005_phase5a_schema_reconcile.py`.

### Files Modified
- `app/routes/pid.py` — Removed lines 570-654 (legacy PID routes block). 8 endpoints hardcoding "Flower Room"/"main" were duplicating v2 location/cluster path-param routes. File now ends at line 567.
- `app/routes/routes.py` — Removed `rules` import, router registration (`app.include_router(rules.router)`), and dependency override (`app.dependency_overrides[rules.get_database]`).
- `app/routes/schedules/__init__.py` — Removed `climate_router` import and `router.include_router(climate_router)`. Removed `ClimateScheduleCreate` and `ClimateScheduleSetpoint` from imports and `__all__` (only consumed by deleted `climate.py`). Updated module docstring.
- `alembic/versions/005_phase5a_schema_reconcile.py` — Updated comment to state `004_drop_legacy_setpoint_columns` was deleted.
- `alembic/staged/README.md` — Cleared "Currently staged" section (now "None").

### Verification
- `ruff check .` passes with zero errors.
- No import errors from deleted files.
- v2 PID routes preserved intact.

## 2026-07-12 — Wave 1 Dead Code Removal (Methods, Imports, Config)

### Methods Removed (verified no callers via codegraph_callers + grep)
- `app/control/control_engine.py` — Removed `get_performance_stats()` (lines 184-197). Zero callers found.
- `app/background_tasks.py` — Removed `set_update_interval()` (lines 134-141). Zero callers found.
- `app/repositories/setpoints.py` — Removed `log_effective_setpoint()` (lines 121-177). Zero callers found; superseded by `log_effective_setpoints()` (plural).

### Methods NOT Removed (live callers found — safety rule)
- `app/control/control_engine.py` — `restore_ramp_state_from_database()` has live caller at `app/container.py:197`.
- `app/calendar/sync_worker.py` — `test_connection()` has live caller at `app/routes/calendar.py:272` (API route `test_sync_connection`).
- `app/repositories/schedules.py` — `get_room_schedule()` has live caller at `app/services/schedule_state.py:37`.

### Config/Schema Removals
- `app/models/config_schema.py` — Removed deprecated `DeviceType` enum and `DeviceConfig` model (lines 8-44). Verified zero imports.
- `app/models/config_schema.py` — Removed `allow_legacy_flower_main` validation flag (line 73) and all three conditional guards (lines 118, 227, 249). Validation now always enforced.
- `app/config.py` — Removed `_update_device_config_yaml()` (lines 602-635) and `_update_light_dimming_assignment_yaml()` (lines 684-728). Updated their callers to return `False` instead of falling back to YAML.

### Unused Re-Exports/Imports Removed
- `app/control/__init__.py` — Removed all re-exports (`ControlEngine`, `DeviceController`, `PIDControllerManager`, `SetpointManager`, `VPDCascadeController`, `VPDController`). Verified zero imports from `app.control` package level.
- `app/middleware/__init__.py` — Removed all imports and `__all__` (`APIError`, `ConflictError`, `NotFoundError`, `ValidationAPIError`, `exception_handler_middleware`, `profiling_middleware`). Verified zero imports from `app.middleware` package level.

### Commented-Out Code Blocks
- **No commented-out code blocks found** at the 13 specified locations. All specified line numbers contain active explanatory comments, not commented-out code. This suggests the blocks were already removed in a prior cleanup or the line numbers referred to comment blocks that are still relevant.

### Verification
- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- No import errors from removed symbols.
- Live methods with callers preserved per safety rule.

## 2026-07-12 — Extract Shared Relay Steal Logic and Hierarchy Traversal

### Helpers Added
- `app/repositories/devices.py` — `iter_devices_flat()` (sync generator for any hierarchy dict)
  - Yields `(location, cluster, device_name, device_info)` tuples
  - Optional filters: `location`, `cluster`, `device_type`
  - Used by `schedule_merge.py` (config-driven hierarchy)
- `app/repositories/devices.py` — `DeviceRepository.iter_all_devices_flat()` (async generator)
  - Fetches hierarchy from DB via `get_all_as_hierarchy()` then delegates to `iter_devices_flat()`
  - Used by `devices_crud.py` and `lights.py`
- `app/repositories/devices.py` — `_find_displaced_device()` (async helper)
  - Takes `(device_repo, channel, exclude_device_id)`
  - Returns `device_id` of device occupying the channel, or `None`
  - Uses `iter_all_devices_flat()` internally

### Instances Replaced
1. `devices_crud.py` ~line 95 — DFR conflict check on light creation (POST /api/devices/registry)
2. `devices_crud.py` ~line 156 — Relay channel conflict check on non-light creation
3. `devices_crud.py` ~line 229 — Relay steal on light update → uses `_find_displaced_device()`
4. `devices_crud.py` ~line 252 — DFR conflict check on light update
5. `devices_crud.py` ~line 302 — Relay steal on non-light update → uses `_find_displaced_device()`
6. `lights.py` ~line 963 — DFR conflict check on light creation (POST /api/lights)
7. `schedule_merge.py` ~line 199 — Schedule validation hierarchy traversal
8. `schedule_merge.py` ~line 255 — Schedule validation hierarchy traversal

### Verification
- `ruff check .` — **All checks passed**.
- `grep -c "for.*clusters.*items" app/routes/devices_crud.py` — **0** (≤1 required).
- `grep -c "for.*clusters.*items" app/control/schedule_merge.py` — **0**.
- No business logic changed; pure refactoring.

## 2026-07-12 — Redis Key Helper Refactoring (Wave 2B)

### Helpers Added to `app/redis/schema.py`

**New cea-prefixed helpers (5 requested):**
- `light_state_key(location, cluster, device_name)` → `cea:light:{location}:{cluster}:{device_name}`
- `automation_state_key(location, cluster, device_name)` → `cea:automation:{location}:{cluster}:{device_name}`
- `schedule_cache_key(location, cluster)` → `schedules:loc:{location}:cluster:{cluster}`
- `effective_setpoint_light_key(location, cluster, device_name)` → `effective_setpoint:{location}:{cluster}:{device_name}:light`
- `pid_key_with_location(location, cluster, device_type)` → `cea:pid:{location}:{cluster}:{device_type}`

**Legacy helpers added (18 additional) to preserve key formats during refactoring:**
- `legacy_light_state_key`, `legacy_automation_state_key`, `legacy_setpoint_field_key`, `legacy_effective_setpoint_prefix`
- `legacy_alarm_key`, `legacy_alarm_prefix`, `legacy_alarm_pattern`, `alarm_pattern`
- `legacy_ramp_key`, `legacy_ramp_persist_key`, `legacy_mode_key`, `legacy_failsafe_key`
- `pid_parameters_key`, `pid_autotune_key`, `pid_autotune_key_with_location`
- `schedule_cache_key_all`, `schedule_cache_key_location`, `schedule_cache_key_light`, `schedule_cache_key_room_light`

### Files Modified (inline f-strings replaced with helper calls)

| File | Patterns Replaced | Count |
|------|-------------------|-------|
| `app/redis/ramps.py` | `legacy_ramp_key`, `legacy_ramp_persist_key` | 3 |
| `app/redis/setpoints.py` | `legacy_setpoint_field_key`, `legacy_effective_setpoint_prefix` | 15 |
| `app/redis/alarms.py` | `legacy_alarm_key`, `legacy_alarm_pattern`, `alarm_pattern` | 8 |
| `app/redis/lighting.py` | `legacy_light_state_key` | 2 |
| `app/redis/streams.py` | `legacy_automation_state_key` | 1 |
| `app/state/__init__.py` | `pid_parameters_key`, `pid_autotune_key`, `legacy_ramp_key`, `legacy_ramp_persist_key`, `legacy_mode_key`, `legacy_alarm_key`, `legacy_alarm_prefix`, `legacy_failsafe_key` | 19 |
| `app/repositories/schedules.py` | `schedule_cache_key`, `schedule_cache_key_all`, `schedule_cache_key_location`, `schedule_cache_key_light`, `schedule_cache_key_room_light` | 5 |
| `app/repositories/pid.py` | `pid_parameters_key`, `pid_autotune_key_with_location` | 4 |

**Total: ~57 inline f-strings replaced across 8 files.**

### Key Principle

All legacy helpers produce **identical strings** to the original inline f-strings. No key formats were changed. The new `cea:`-prefixed helpers are available for future migration work.

### Verification

- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- `grep` for inline Redis key f-strings in all 8 target files — **0 matches**.
- `devices.py` and `devices_crud.py` intentionally untouched per task boundary.

## 2026-07-12 — Wave 2 Deduplication (_ROOM_PREFIXES + _load_yaml_file)

### A. Moved `_ROOM_PREFIXES` and `_room_prefix()` to `shared/cluster_topology.py`

**Rationale:** `shared/cluster_topology.py` is already the canonical source for room validation (cluster topology contract). The room prefix registry logically belongs there too.

**Files modified:**
- `Infrastructure/shared/cluster_topology.py` — Added `_ROOM_PREFIXES` dict and `_room_prefix()` function, added both to `__all__`.
- `app/repositories/devices.py` — Removed local definitions, added `from shared.cluster_topology import _room_prefix`.
- `app/routes/devices_crud.py` — Changed import from `app.repositories.devices` to `shared.cluster_topology`.

**Files intentionally NOT modified:**
- `app/calendar/sync_worker.py` — Contains a *different* `_room_prefix(location, title)` with a different signature/purpose. Not the same function.
- `alembic/versions/009_seed_device_registry_from_yaml.py` — Migrations are self-contained by design.
- `app/routes/devices.py` — No usage of `_room_prefix` found.

### B. Added `_load_yaml_file()` helper in `app/config.py`

**Rationale:** `yaml.safe_load(f) or {}` appeared 3 times in `ConfigLoader.load()`. Extracting a helper removes repetition and centralizes FileNotFoundError handling.

**Files modified:**
- `app/config.py` — Added `_load_yaml_file(path: Path) -> dict[str, Any]` helper. Replaced 3 inline blocks with helper calls. Removed redundant `.exists()` checks since the helper handles `FileNotFoundError` gracefully.

**Behavior preserved:**
- Missing schedules/rules files still fall through to check the main config dict.
- Missing main config file still raises `FileNotFoundError` at loader init.

### Verification
- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- `ruff check --fix .` sorted imports in `devices.py` and `devices_crud.py` automatically.

## 2026-07-12 — Wave 3: Split Oversized Backend Files

### A. Split `app/routes/lights.py` (1181 lines) into `app/routes/lights/` package

**Rationale:** 1181 lines with 16 endpoints across 4 concerns (DFR assignments, status queries, control actions, CRUD). Single file was hard to navigate and exceeded the 400 LOC target.

**New package structure:**
| File | Lines | Responsibility |
|------|-------|----------------|
| `app/routes/lights/__init__.py` | 81 | Router, dependency stubs, submodule imports |
| `app/routes/lights/dfr_assignments.py` | 175 | DFR0971 board/channel assignment endpoints |
| `app/routes/lights/light_status.py` | 373 | Light status/query endpoints (zone-status, per-device status, schedule) |
| `app/routes/lights/light_control.py` | 195 | Direct hardware control (set_intensity, set_voltage) |
| `app/routes/lights/light_target.py` | 200 | DB-backed target intensity updates (target, device_id intensity) |
| `app/routes/lights/light_crud.py` | 131 | Light CRUD (create, update, delete) |
| `app/routes/lights/light_test.py` | 133 | DFR test sweep endpoint |

**Key decisions:**
- Dependency stubs (`get_dfr0971_manager`, `get_config`, etc.) live in `__init__.py` so all submodules can import them from the package without circular imports.
- Shared helpers (`_sync_scheduler_light_intensities`, `_publish_schedule_changed`) moved to `light_target.py` since that's where they're consumed.
- `_read_light_status_payload` and `_schedule_row_active_for_device` moved to `light_status.py` where they're used.
- `_iter_all_dfr0971_lights` helper moved to `dfr_assignments.py` where it's used.
- Old `app/routes/lights.py` deleted. `app/routes/routes.py` imports `lights` as a package — no changes needed because `__init__.py` exports the same symbols (`router`, `get_database`, etc.).

### B. Split `config_cli.py` (825 lines) into `cli/` package

**Rationale:** 825 lines mixing PID, schedule, setpoint, and config-show concerns. CLI commands were tightly coupled in one file.

**New package structure:**
| File | Lines | Responsibility |
|------|-------|----------------|
| `cli/__init__.py` | 5 | Package init |
| `cli/config_cli.py` | 226 | Main entry point + argparse setup only |
| `cli/pid.py` | 126 | PID validation + get/set commands |
| `cli/schedules.py` | 268 | Schedule validators + list/create/delete commands |
| `cli/schedules_update.py` | 163 | Schedule update command (split out to stay under 400 LOC) |
| `cli/setpoints.py` | 74 | Setpoint validation + config_show command |
| `config_cli.py` (root) | 17 | Thin wrapper that delegates to `cli.config_cli` |

**Key decisions:**
- Root `config_cli.py` kept as a thin wrapper so existing workflows (`./config_cli.py ...`) continue to work.
- `validate_mode`, `validate_time`, and `check_schedule_conflicts` live in `cli/schedules.py` and are imported by `cli/schedules_update.py`.
- `SETPOINT_RANGES` and `validate_setpoint` live in `cli/setpoints.py`.
- `PID_RANGES` and `validate_pid` live in `cli/pid.py`.

### Verification
- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- No import errors from the split.
- All endpoint URLs and CLI command structures preserved exactly.

## 2026-07-12 — Split frontend api.ts into focused modules

### Problem
`frontend/src/services/api.ts` was 794 lines — a monolithic ApiClient class with ~60 methods spanning devices, sensors, schedules, PID, lights, modes, calendar, weather, system config, and notes.

### Approach: TypeScript Mixin Pattern
Used `Object.assign(ApiClient.prototype, ...)` to attach domain method objects to the class at runtime, with interface-based type contracts for compile-time safety.

**Key design decisions:**
1. **`ApiClientCore` interface** — Declares the 3 axios clients (`backendClient`, `automationClient`, `weatherClient`) as a structural type. Domain method modules use `this: ApiClientCore` instead of `this: ApiClient`, avoiding circular type dependencies.
2. **Domain method objects** — Each domain file exports a plain object of async methods with `this: ApiClientCore` annotations. Methods are attached to the prototype via `Object.assign`.
3. **Interface type contracts** — Each domain file exports an interface (e.g., `DeviceApi`, `SensorApi`, `ScheduleApi`, `PidApi`) declaring method signatures. The `apiClient` singleton is typed as `ApiClient & DeviceApi & SensorApi & ScheduleApi & PidApi` via a type assertion at export.
4. **No caller changes** — All 16 importing files still import `{ apiClient }` from `'../services/api'` (or `'../../services/api'`). Zero import changes needed.

### Files Created
| File | LOC | Contents |
|------|-----|----------|
| `services/api/sensors.ts` | 26 | 3 sensor methods (getLiveSensorData, getSensorDataBulk, getAllLiveSensorData) |
| `services/api/devices.ts` | 296 | 22 device methods (registry CRUD, control, channels, DFR, light CRUD, control history) |
| `services/api/schedules.ts` | 102 | 11 schedule methods (schedules CRUD, climate schedule, climate periods, room schedule) |
| `services/api/pid.ts` | 136 | 14 PID methods (parameters, modes, autotune, per-room v2 routes) |

### File Modified
- `services/api.ts` — Reduced from 794 to 359 lines. Contains: ApiClientCore interface, ApiClient class (constructor + axios clients + remaining methods for notes, modes, lights status/intensity, weather, system status/health, room modes, calendar, system config), Object.assign mixin wiring, and `apiClient` singleton export.

### Method Grouping Rationale
- **Devices** (22 methods): Device registry CRUD, device control (controlDevice, controlChannel, setDeviceMode), channels/relay state, DFR0971 dimming board assignment, light CRUD (createLight, updateLight, deleteLight, testLight), getLightsByRoom, getLightsForZone, control history.
- **Sensors** (3 methods): getLiveSensorData, getSensorDataBulk, getAllLiveSensorData.
- **Schedules** (11 methods): Schedules CRUD, climate schedule/periods, room schedule (lights photoperiod).
- **PID** (14 methods): Legacy device-type PID routes + per-room v2 routes, autotune, control modes.
- **Remaining in api.ts**: Notes, modes, light status/intensity/schedule, weather, system status/health, room modes, calendar, system config — these are smaller groups that don't warrant separate files.

### Issue Encountered: `getLightsForZone` self-call
`getLightsForZone` originally called `this.getDevicesForLocationClusterWithDetails()` — but with `this: ApiClientCore`, that method isn't visible. Fixed by inlining the axios call directly (`this.automationClient.get(...)`) instead of delegating to another mixed-in method.

### Issue Encountered: Private axios clients
The original class had `private backendClient` etc. The `ApiClientCore` interface requires these as public properties (domain methods access them via `this.backendClient`). Removed the `private` modifier — the axios clients are now public on the class, which is acceptable since they're only accessed by the mixed-in domain methods within the same package.

### Verification
- `npx tsc --noEmit` — **Zero errors**.
- `npm run build` — **Build succeeded** (✓ built in 10.35s).
- All 16 caller files verified — zero import changes needed.
- All files under 400 LOC (max: `api.ts` at 359).

## 2026-07-12 — Wave 4: Split 3 Oversized Backend Control Files

### A. Split `app/control/scheduler.py` (1183 lines) into `app/control/scheduler/` package

**Rationale:** 1183 lines mixing cache management, non-light schedules, photoperiod, light intensity, ramp calculation, and light program evaluation. Single file was hard to navigate and exceeded the 400 LOC target.

**New package structure:**
| File | Lines | Responsibility |
|------|-------|----------------|
| `app/control/scheduler/__init__.py` | 250 | Scheduler class core: __init__, cache updates, climate periods, device type lookup, utility methods |
| `app/control/scheduler/photoperiod.py` | 65 | Photoperiod calculation (`is_in_photoperiod`) — sun/moon window from mode_params |
| `app/control/scheduler/ramp_calculator.py` | 326 | Ramp calculation logic (`_compute_ramped_intensity`, `_compute_start_end_datetimes`, `_get_ramp_progress`) |
| `app/control/scheduler/light_programs.py` | 224 | Light program evaluation (`_find_matching_program`, `_evaluate_light_programs`, cycle/static) |
| `app/control/scheduler/schedules.py` | 178 | Non-light schedule methods (`is_schedule_active`, `get_schedule_state`, `get_active_schedule_details`) |
| `app/control/scheduler/light_intensity.py` | 217 | Light intensity calculation (`get_schedule_intensity`, `get_light_intensity_details`) |

**Key decisions:**
- Used mixin pattern: `Scheduler(PhotoperiodMixin, RampCalculatorMixin, LightProgramsMixin, SchedulesMixin, LightIntensityMixin)` — each submodule defines a mixin class with its methods.
- `LOCAL_TZ` and `MINIMUM_LIGHT_INTENSITY` remain in `__init__.py` for backward compatibility with existing imports (`from app.control.scheduler import LOCAL_TZ, Scheduler`).
- No caller import changes needed — `Scheduler` and `LOCAL_TZ` are still exported from `app.control.scheduler` package.

### B. Split `app/control/device_controller.py` (875 lines) into `app/control/device_controller/` package

**Rationale:** 875 lines mixing process_device orchestration, VPD calculations, rule-based control, dimmable light control, binary device control, logging, and state restore.

**New package structure:**
| File | Lines | Responsibility |
|------|-------|----------------|
| `app/control/device_controller/__init__.py` | 308 | DeviceController core: __init__, `process_device`, `_determine_control_mode`, `_calculate_control_output`, `_apply_control_output` |
| `app/control/device_controller/vpd.py` | 95 | VPD-related device control (`_calculate_vpd_based_output`, `_calculate_failsafe_output`) |
| `app/control/device_controller/rules.py` | 129 | Rule-based device control (`_calculate_rule_based_output`, sensor mapping, `_reason_for_device_type`) |
| `app/control/device_controller/dimmable_light.py` | 198 | DFR0971 dimmable light control with relay synchronization (`_control_dimmable_light`) |
| `app/control/device_controller/binary_device.py` | 82 | Binary relay device control with hysteresis (`_control_binary_device`) |
| `app/control/device_controller/logging.py` | 142 | Control action logging, state restore, telemetry (`_log_control_action`, `restore_device_states`, `write_light_telemetry`) |

**Key decisions:**
- Used mixin pattern: `DeviceController(VPDCalculatorMixin, RulesMixin, DimmableLightMixin, BinaryDeviceMixin, LoggingMixin)`.
- No caller import changes needed — `DeviceController` is still exported from `app.control.device_controller`.

### C. Split `app/background_tasks.py` (775 lines) into `app/background_tasks/` package

**Rationale:** 775 lines with 8 background task loops (control loop, heartbeat, auto-persist, setpoint history, batch flush, config events, calendar sync, calendar mode scheduler) plus startup/shutdown logic.

**New package structure:**
| File | Lines | Responsibility |
|------|-------|----------------|
| `app/background_tasks/__init__.py` | 144 | BackgroundTasks core: __init__, `start`, `stop`, task creation/cancellation |
| `app/background_tasks/control_loop.py` | 152 | Main control loop with degraded-mode handling (`_control_loop`, `_record_control_failure`, `_record_control_success`) |
| `app/background_tasks/state_sync.py` | 90 | State synchronization — loads scheduler data from DB (`_load_scheduler_data`) |
| `app/background_tasks/heartbeat.py` | 42 | Heartbeat loop for service liveness |
| `app/background_tasks/auto_persist.py` | 90 | Auto-persist loop for Redis PID params → DB |
| `app/background_tasks/setpoint_history.py` | 81 | Setpoint history logging loop |
| `app/background_tasks/batch_flush.py` | 40 | Batch flush loop for batched DB writes |
| `app/background_tasks/config_events.py` | 208 | Config event consumer loop (SCHEDULE_CHANGED, MODE_CHANGED, PID_PARAMS_CHANGED, etc.) |
| `app/background_tasks/calendar.py` | 45 | Calendar mode scheduler and sync loops |

**Key decisions:**
- Used mixin pattern: `BackgroundTasks(ControlLoopMixin, StateSyncMixin, HeartbeatMixin, AutoPersistMixin, SetpointHistoryMixin, BatchFlushMixin, ConfigEventsMixin, CalendarMixin)`.
- No caller import changes needed — `BackgroundTasks` is still exported from `app.background_tasks`.

### Verification
- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- All new files under 400 LOC (max: `ramp_calculator.py` at 326, `config_events.py` at 208).
- No control logic changed — pure code movement via mixin pattern.
- All imports updated automatically via package `__init__.py` re-exports.
- Pre-existing `app/repositories/devices/registry.py` missing imports (`LightDevice`, `_row_to_typed_device`) fixed as part of this wave to ensure ruff passes.

## 2026-07-12 — Wave 5: Split state/__init__.py and repositories/devices.py into Focused Modules

### A. Split `app/state/__init__.py` (1119 lines → 6 files, all <400 LOC)

**Problem:** `state/__init__.py` had grown to 1119 lines, mixing PID params, ramp state, alarm/failsafe state, Redis fallback, mode API, and core cache operations.

**Solution:** Used mixin pattern to preserve the `StateManager` API while splitting methods into focused modules:

| File | Lines | Responsibility |
|------|-------|----------------|
| `state/__init__.py` | 275 | Core StateManager: get, set, delete, exists, mode API, clear, cleanup_expired, get_stats, singletons |
| `state/pid.py` | 138 | PIDMixin: PIDParams TypedDict, get_pid_params, set_pid_params, get_autotune_state, set_autotune_state |
| `state/ramps.py` | 174 | RampMixin: RampState TypedDict, get_ramp_state, set_ramp_state, clear_ramp_state, persist_ramp, get_persisted_ramps, clear_persisted_ramp |
| `state/alarms.py` | 263 | AlarmMixin: AlarmDict/FailsafeState TypedDicts, get_alarms, write_alarm, acknowledge_alarm, clear_alarm, get_failsafe, set_failsafe, clear_failsafe |
| `state/redis_client.py` | 212 | RedisClientMixin: _get_from_redis, _get_from_redis_raw, _write_to_redis, _delete_from_redis, _evict_oldest, initialize_from_redis |
| `state/_types.py` | 28 | CacheEntry dataclass (extracted to avoid circular imports between core and alarm mixin) |

**Key design decisions:**
- Mixins use `self.get()`, `self.set()`, `self._redis_client`, etc. without importing from `app.state`, avoiding circular imports.
- `CacheEntry` moved to `state/_types.py` because both `state/__init__.py` and `state/alarms.py` need it.
- `StateManager` inherits from all mixins: `class StateManager(SchemaValidationMixin, PIDMixin, RampMixin, AlarmMixin, RedisClientMixin)`.
- All existing imports continue to work; only `PIDParams` import moved from `app.state` to `app.state.pid` in `app/control/pid_controller_manager.py`.

### B. Split `app/repositories/devices.py` (870 lines → 6 files, all <400 LOC)

**Problem:** `repositories/devices.py` had grown to 870 lines, mixing device state, mapping, hierarchy traversal, light registry, non-light registry, and relay binding.

**Solution:** Converted to a package `repositories/devices/` using mixin + helper pattern:

| File | Lines | Responsibility |
|------|-------|----------------|
| `devices/__init__.py` | 245 | DeviceRepository class: core CRUD (state, mapping, hierarchy query, flat list) + inherits all mixins |
| `devices/hierarchy.py` | 95 | HierarchyMixin: iter_all_devices_flat, by_location, to_hierarchy_dict; standalone iter_devices_flat, _find_displaced_device |
| `devices/registry.py` | 317 | RegistryMixin: non-light registry CRUD (get_device_id, create_device, update_device, delete_device, etc.) + relay binding |
| `devices/registry_lights.py` | 226 | LightRegistryMixin: light-specific registry CRUD (get_lights_by_room, create_light, update_light, delete_light, etc.) |
| `devices/_helpers.py` | 78 | Standalone helpers: _generate_light_device_name, _generate_device_name, _row_to_typed_device, _row_to_device, _row_to_light_device |
| `devices/base.py` | — | Already existed; BaseRepository with pool and logger |

**Key design decisions:**
- `_helpers.py` centralizes row-to-model conversion and name generation to avoid circular imports between core, registry, and hierarchy modules.
- `DeviceRepository` inherits from `BaseRepository, RegistryMixin, HierarchyMixin, LightRegistryMixin`.
- `__init__.py` re-exports `DeviceRepository`, `iter_devices_flat`, `_find_displaced_device` so existing imports like `from app.repositories.devices import DeviceRepository, iter_devices_flat` still work.
- `_find_displaced_device` uses `TYPE_CHECKING` import of `DeviceRepository` to avoid circular import at runtime.

### Import Updates

**Files modified for imports:**
- `app/control/pid_controller_manager.py` — Changed `from app.state import PIDParams` to `from app.state.pid import PIDParams`

**No other import changes needed** because `__init__.py` re-exports preserve the public API.

### Pre-existing Ruff Fixes (unrelated to split)

Fixed 4 pre-existing ruff errors discovered during verification:
- `app/background_tasks/__init__.py` — Removed unused `UTC` import
- `app/control/scheduler/__init__.py` — Removed unused `timedelta` import
- `app/routes/lights/__init__.py` — Moved `# noqa: E402` to the `from` line of the import block

### Verification

- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- Line counts verified: **no file >400 LOC**.
- No state management logic changed.
- No repository queries broken.

## 2026-07-12 — BaseRepository Standardization + CalendarRepository Fix

### A. Created `Infrastructure/shared/base_repository.py`

Unified `BaseRepository` combining features from both existing service-local base classes:
- Pool injection (`__init__(self, pool)`), `set_pool()`, `pool` property
- `_acquire()` async context manager (from backend)
- `_execute()` helper (from backend)
- Query caching helpers: `_get_cache_key`, `_get_cached_result`, `_set_cached_result`, `clear_cache` (from automation-service)
- `_cache_ttl = 30.0`

### B. Updated automation-service `app/repositories/base.py`

Replaced 48-line local implementation with a thin re-export from `shared.base_repository`, preserving `logger` export for backward compatibility:
```python
from shared.base_repository import BaseRepository
from shared.infra_logging import get_logger
logger = get_logger(__name__)
```

### C. Updated backend `app/repositories/base.py`

Replaced 52-line local implementation with the same re-export pattern. Removed the "trimmed for backend's read-only workload" docstring since the shared base is now canonical.

### D. Fixed `CalendarRepository` (`app/repositories/calendar.py`)

- Now extends `BaseRepository` (was standalone)
- Constructor calls `super().__init__(pool)`
- Replaced all 20+ direct `self._pool.fetch()` / `self._pool.fetchrow()` / `self._pool.execute()` calls with `async with self.pool.acquire() as conn:` pattern
- Added try/except error handling with `logger.error(...)` for consistency with other repos (e.g., `LightTargetIntensityRepository`, `ClimatePeriodRepository`)
- Preserved all query logic exactly — no SQL changes

### E. Added missing repos to `app/repositories/__init__.py`

Added 4 missing exports:
- `LightTargetIntensityRepository`
- `LightProgramsRepository`
- `ClimatePeriodRepository`
- `CalendarRepository`

### Verification

- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- `ruff check .` in `Infrastructure/backend` — **All checks passed**.
- `grep -rn "class.*Repository.*BaseRepository" app/repositories/*.py` — **11/11 repos inherit** in automation-service, **2/2 repos inherit** in backend.

### Notes

- LSP "Import could not be resolved" errors for `shared.*` modules are pre-existing and unrelated — the shared path is resolved at runtime via PYTHONPATH, not via LSP config.
- The `shared/__init__.py` intentionally does NOT re-export `BaseRepository` because it imports `asyncpg` (heavy dep), and the shared package's own `__init__.py` policy forbids re-exporting heavy deps. Services import directly: `from shared.base_repository import BaseRepository`.

## 2026-07-12 — AGENTS.md Route Inventory Update

### Changes Made

Updated `Infrastructure/automation-service/AGENTS.md` to reflect the current route structure after Waves 1-4:

1. **REPOSITORY PATTERN section** — Added 2 missing repositories:
   - `ClimatePeriod` → `repositories/climate_periods.py` — Climate period configuration
   - `Calendar` → `repositories/calendar.py` — Calendar events and grow plans

2. **API GROUPS section** — Replaced the 8-route table with a complete 18-route inventory:
   - Documented all active routes with router name, prefix, and purpose
   - Added "Wave 3 Splits" subsection noting `lights` router split into `routes/lights/` package (6 submodules)
   - Added "Removed Routes (Dead Code)" subsection documenting 3 deletions from Wave 1:
     - `rules.router` — stub endpoints, zero live callers
     - `schedules/climate.router` — legacy no-op endpoints
     - Legacy PID routes in `pid.py` — 8 endpoints hardcoding "Flower Room"/"main"

### Verification

- `ruff check .` in `Infrastructure/automation-service` — **All checks passed**.
- AGENTS.md renders correctly with all tables aligned.

