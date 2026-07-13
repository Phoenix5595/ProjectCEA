# architecture-refactor - Work Plan

## TL;DR (For humans)

**What you'll get:** Cleaner codebase with no dead code, no duplicated logic, oversized files split into focused modules, standardized repository pattern (including CalendarRepository fix), consolidated route handlers with dead routes removed, and all legacy/superseded code removed. Each wave deploys independently so you can verify it works before moving to the next.

**Why this approach:** The codebase has grown organically across 60+ plans. Legacy code accumulates (deprecated schemas, dead repos, stale comments, AI export module, stub route handlers, legacy PID routes), oversized files become unmaintainable (lights.py 1184 lines, scheduler.py 1183 lines, state/__init__.py 1109 lines), the relay steal logic is duplicated 7x, Redis keys are built with 30+ inline f-strings instead of schema helpers, and CalendarRepository doesn't even inherit BaseRepository. Safe execution = one area at a time + deploy each, so if something breaks you know exactly which wave caused it.

**What it will NOT do:**
- Will NOT touch `.opencode/` plugin files (external tooling)
- Will NOT remove `merge_schedules_with_config` (active validator with 7 callers)
- Will NOT remove `supplemental_light_intensity` from mode_parameters (independent light system, different purpose from light_programs)
- Will NOT drop DB columns for `main_light_intensity` (leave in DB, mark deprecated in code)
- Will NOT add tests (you deleted them all, verification is ruff + tsc + build)

**Effort:** Large
**Risk:** Medium — safe execution with deploy-after-each-wave mitigates risk

Your next move: approve, then `$start-work architecture-refactor`. Full execution detail follows below.

---

> TL;DR (machine): Large, Medium risk — 6 waves: (1) remove legacy/superseded code (6 original + 12 additional categories from explore agents), (2) deduplicate relay steal + hierarchy traversal + Redis keys + room prefix + YAML loading, (3) split 8 oversized files >500 LOC, (4) standardize BaseRepository + fix CalendarRepository, (5) consolidate route handlers + remove dead routes, (6) final verify + deploy. Safe: deploy after each wave. Exclude .opencode/.

## Scope
### Must have
- Remove ALL legacy/superseded code (18 categories total — see Wave 1 for full list)
- Extract shared relay steal logic from 7 duplicated instances (not 5 — found 2 more in lights.py and schedule_merge.py)
- Add missing Redis key helpers to schema.py and replace 30+ inline f-strings
- Move room prefix validation to shared/cluster_topology.py
- Add `_load_yaml_file()` helper for YAML loading duplication
- Split files exceeding 500 LOC into focused modules (8 files)
- Standardize BaseRepository: unify automation-service and backend, fix CalendarRepository (doesn't inherit), add missing __init__.py exports
- Consolidate route handlers: remove dead routes (rules.py, climate.py, legacy PID), document all routes
- Mark `main_light_intensity` as deprecated in schemas (keep DB columns)
- Clean up 13 commented-out code blocks across the codebase

### Must NOT have (guardrails)
- Do NOT touch `.opencode/` plugin files
- Do NOT remove `merge_schedules_with_config` (active validator)
- Do NOT remove `supplemental_light_intensity` from mode_parameters or schemas
- Do NOT drop DB columns
- Do NOT add tests
- Do NOT break any API endpoint contracts (except dead routes being removed)
- Do NOT change the 2-second control loop timing

## Verification strategy
> Safe: one area at a time + deploy each wave independently.
- No test suite (deleted). Verification = `ruff check .` (backend) + `npx tsc --noEmit && npm run build` (frontend) + `./deploy.sh` + GET endpoint verification
- Evidence: `.omo/evidence/task-<N>-architecture-refactor.<ext>`

## Execution strategy
### Parallel execution waves (deploy after EACH wave)
- **Wave 1 (Legacy removal):** Remove 18 categories of dead code. No new code, only deletion. Low risk.
- **Wave 2 (Deduplicate):** Extract relay steal, hierarchy traversal, Redis keys, room prefix, YAML loading. Medium risk.
- **Wave 3 (Split oversized files):** Split 8 files >500 LOC into focused modules. Medium-high risk (many callers).
- **Wave 4 (Standardize BaseRepository):** Unify shared base + fix CalendarRepository. Medium risk.
- **Wave 5 (Consolidate routes):** Remove dead routes, clean structure. Low-medium risk.
- **Wave 6 (Final verify + deploy):** Full verification.

### Dependency matrix
| Wave | Depends on | Blocks | Risk |
|------|-----------|--------|------|
| 1 | — | 2, 3 | Low (deletion only) |
| 2 | 1 | 3 | Medium (active devices_crud.py, schema.py, shared/) |
| 3 | 1, 2 | 4 | Medium-high (many callers) |
| 4 | 3 | 5 | Medium (repository pattern) |
| 5 | 4 | 6 | Low-medium (route structure) |
| 6 | 1-5 | — | — |

## Todos
> Implementation + verification = ONE todo. Deploy after each wave.

- [x] 1. Remove legacy/superseded code (18 categories)
  What to do / Must NOT do:
  - **Verify no callers** for each item before removing. Use `codegraph_callers` and `grep`.
  - **ORIGINAL 6 CATEGORIES:**
    - Remove `config.py:614` `_update_device_config_yaml()`: Check callers. If none, delete.
    - Remove `config.py:701` `_update_light_dimming_assignment_yaml()`: Check callers. If none, delete.
    - Remove `schemas/schedules.py:61` `ClimateScheduleUpdate`: Check imports. If none, delete.
    - Remove `repositories/setpoints.py` deprecated methods (`log_effective_setpoint`): Check callers. Keep live methods.
    - Remove `repositories/schedules.py:477` `get_room_schedule()`: Marked DEPRECATED. Verify no callers, remove.
    - Remove `models/config_schema.py:32` deprecated models: Verify no imports, remove.
  - **ADDITIONAL CATEGORIES (from explore agents):**
    - Delete `app/ai_export.py` ENTIRELY: No callers found for `export_training_data()` or `get_available_ranges()`.
    - Delete `app/routes/rules.py` ENTIRELY (66 lines): All 5 handlers are stubs returning empty/success. Comments say "For now, return empty."
    - Delete `app/routes/schedules/climate.py` ENTIRELY (79 lines): Legacy no-op endpoints. File header says "legacy - maintained for API compatibility." POST is a no-op. `climate_periods` is the canonical replacement.
    - Remove `routes/pid.py:571-653` legacy PID routes (8 endpoints): These duplicate v2 routes with hardcoded "Flower Room"/"main". Keep v2 routes only.
    - Remove unused re-exports in `app/control/__init__.py:22-27`: ControlEngine, DeviceController, PIDControllerManager, SetpointManager, VPDCascadeController, VPDController — re-exported but never imported by consumers.
    - Remove unused imports in `app/middleware/__init__.py`: APIError, ConflictError, NotFoundError, ValidationAPIError, exception_handler_middleware, profiling_middleware.
    - Remove `app/control/control_engine.py:get_performance_stats()` and `restore_ramp_state_from_database()`: No callers found.
    - Remove `app/background_tasks.py:set_update_interval()`: No callers found.
    - Remove `app/calendar/sync_worker.py:test_connection()`: No callers found.
    - Remove `allow_legacy_flower_main` config validation in `models/config_schema.py:73,118-136`: Legacy validation flag. Set to false permanently and remove.
    - Remove `alembic/staged/004_drop_legacy_setpoint_columns.py`: Staged migration never applied. Either apply or delete — delete since setpoints are deprecated.
    - Remove `frontend/src/components/LightIntensity.tsx:fetchLightsAndStatusLegacy()` (lines 38-142): Legacy fetch function replaced by zone-status API.
    - Clean up 13 commented-out code blocks (>5 lines each) in: `state/__init__.py:367`, `rules_engine.py:61`, `database.py:155`, `events/consumer.py:184`, `config.py:47,402`, `hardware_batch.py:30`, `control_engine.py:136,147`, `config_schema.py:156,174`, `pid.py:570`, `mcp23017.py:142`.
  - **Fix stale comments:**
    - `container.py:159`: Says "synthetic SUN rows from room_schedule" — update to reflect current behavior.
    - `services/schedule_state.py:98,104`: Comment referencing removed SchedulesMixin.
  - **Mark deprecated (do NOT remove):**
    - `schemas/room_modes.py:46-47,78-79`: Add `# DEPRECATED: use light_target_intensity table` comment to `main_light_intensity` fields.
  - **Run `ruff check .` + `npx tsc --noEmit && npm run build`** after all removals
  - **Deploy + verify**: `timeout 600 ./deploy.sh` + GET endpoints
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2, 3 | Can parallelize with: —
  References:
  - Original 6: config.py:614,701, schemas/schedules.py:61, setpoints.py:14,135, schedules.py:477, config_schema.py:32
  - Additional: ai_export.py (delete), routes/rules.py (delete), routes/schedules/climate.py (delete), pid.py:571-653, control/__init__.py:22-27, middleware/__init__.py, control_engine.py (2 methods), background_tasks.py (1 method), sync_worker.py (1 method), config_schema.py:73-136, alembic/staged/004, LightIntensity.tsx:38-142
  Acceptance criteria:
  - `ruff check .` passes
  - `npx tsc --noEmit && npm run build` passes
  - `timeout 600 ./deploy.sh` succeeds
  - All GET endpoints still return 200 (lights, devices, schedules — NOT rules or climate-schedule which are deleted)
  QA scenarios: happy — dead code removed, system still works. failure — something was still called, rollback. Evidence `.omo/evidence/task-1-architecture-refactor.txt`
  Commit: Y | refactor: remove 18 categories of legacy/superseded code

- [x] 2. Deduplicate: relay steal + hierarchy traversal + Redis keys + room prefix + YAML loading
  What to do / Must NOT do:
  - **Extract relay steal shared logic**: Create `_find_displaced_device()` helper in `devices_crud.py` or `DeviceRepository`. Replace 7 instances (devices_crud.py lines 95, 156, 229, 252, 302; lights.py line 963; schedule_merge.py lines 199, 255).
  - **Add `iter_all_devices_flat()` to DeviceRepository**: Async generator yielding `(location, cluster, device_name, device_info)` with optional filters. Replaces all 7 hierarchy traversal loops.
  - **Add missing Redis key helpers to `schema.py`**: `light_state_key()`, `automation_state_key()`, `schedule_cache_key()`, `effective_setpoint_light_key()`, `pid_key_with_location()`. Replace 30+ inline f-string constructions across ramps.py, setpoints.py, alarms.py, lighting.py, streams.py, state/__init__.py, schedules.py, devices.py, pid.py.
  - **Move `_ROOM_PREFIXES` and `_room_prefix()` to `shared/cluster_topology.py`**: Canonical source for room validation. Update imports in devices.py and devices_crud.py.
  - **Add `_load_yaml_file()` helper in `config.py`**: Replace 4+ `yaml.safe_load(f) or {}` calls.
  - Run `ruff check .` + `npx tsc --noEmit && npm run build`
  - Deploy + verify
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 3 | Can parallelize with: —
  References:
  - devices_crud.py:95,156,229,252,302 (hierarchy traversal)
  - lights.py:963 (DFR conflict check)
  - schedule_merge.py:199,255 (schedule validation)
  - redis/schema.py (add helpers here)
  - 30+ inline f-strings across: ramps.py:103,132,234, setpoints.py:410, alarms.py:39,61,89,101,160,172, lighting.py:38,61, streams.py:90, state/__init__.py:274,319,387,412,422,440,500,788,828,871, schedules.py:28,35,39, devices.py:795-802, pid.py:159,312,449,558
  - repositories/devices.py:27-31 (_room_prefix)
  - config.py:221,247,257 (YAML loading)
  Acceptance criteria:
  - `ruff check .` passes
  - `grep -c "for.*clusters.*items" devices_crud.py` returns ≤ 1
  - `grep -c "f\".*:.*:" Infrastructure/automation-service/app/redis/` returns 0 (all keys via helpers)
  - `timeout 600 ./deploy.sh` succeeds
  QA scenarios: happy — deduplication complete, system still works. Evidence `.omo/evidence/task-2-architecture-refactor.txt`
  Commit: Y | refactor: deduplicate relay steal, hierarchy traversal, Redis keys, room prefix, YAML loading

- [x] 3. Split oversized files (>500 LOC) into focused modules
  What to do / Must NOT do:
  - **Split `lights.py` (1184 lines)** into `routes/lights/` package: `__init__.py` (re-export), `dfr_assignments.py`, `light_status.py`, `light_control.py`. Keep `routes/lights.py` as thin re-export.
  - **Split `scheduler.py` (1183 lines)** into `control/scheduler/` package: `__init__.py` (Scheduler class core), `ramp_calculator.py`, `light_programs.py`, `photoperiod.py`.
  - **Split `state/__init__.py` (1109 lines)** into: `__init__.py` (StateManager core), `state/pid.py`, `state/ramps.py`, `state/alarms.py`.
  - **Split `device_controller.py` (875 lines)** into `control/device_controller/` package: `__init__.py`, `vpd.py`, `rules.py`.
  - **Split `devices.py` repo (831 lines)** into `repositories/devices/` package: `__init__.py` (core CRUD), `hierarchy.py`, `registry.py`.
  - **Split `config_cli.py` (825 lines)** into: `config_cli.py` (main + arg parsing), `cli/setpoints.py`, `cli/schedules.py`, `cli/pid.py`.
  - **Split `api.ts` (794 lines)** into: `services/api.ts` (ApiClient thin), `services/api/devices.ts`, `services/api/sensors.ts`, `services/api/schedules.ts`.
  - **Split `background_tasks.py` (775 lines)** into: `background_tasks.py` (core), `background_tasks/control_loop.py`, `background_tasks/state_sync.py`.
  - Run `ruff check .` + `npx tsc --noEmit && npm run build`
  - Deploy + verify
  Parallelization: Wave 3 | Blocked by: 1, 2 | Blocks: 4 | Can parallelize with: —
  References: All 8 files listed above.
  Acceptance criteria:
  - No refactored file exceeds 400 LOC
  - `ruff check .` passes, `tsc --noEmit && npm run build` passes
  - `timeout 600 ./deploy.sh` succeeds
  - All GET endpoints still return 200
  QA scenarios: happy — files split, everything works. failure — import cycle, rollback. Evidence `.omo/evidence/task-3-architecture-refactor.txt`
  Commit: Y | refactor: split 8 oversized files into focused modules

- [x] 4. Standardize BaseRepository + fix CalendarRepository
  What to do / Must NOT do:
  - **Create `Infrastructure/shared/base_repository.py`** with unified base: pool injection + `_acquire()` context manager + `_execute()` + query caching (`_get_cache_key`, `_get_cached_result`, `_set_cached_result`, `clear_cache`).
  - **Update automation-service `repositories/base.py`** to import from shared.
  - **Update backend `repositories/base.py`** to import from shared.
  - **Fix CalendarRepository**: Make it extend `BaseRepository`. Replace `self._pool.fetch()` with `async with self.pool.acquire() as conn:` pattern. Add try/except error handling for consistency.
  - **Add missing repos to `__init__.py` exports**: `LightTargetIntensityRepository`, `LightProgramsRepository`, `ClimatePeriodRepository`, `CalendarRepository`.
  - Run `ruff check .`
  - Deploy + verify
  Parallelization: Wave 4 | Blocked by: 3 | Blocks: 5 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/app/repositories/base.py` (48 lines)
  - `Infrastructure/backend/app/repositories/base.py` (52 lines)
  - `Infrastructure/automation-service/app/repositories/calendar.py:28` (class without base)
  - `Infrastructure/automation-service/app/repositories/__init__.py` (missing 4 exports)
  Acceptance criteria:
  - `grep -rn "class.*Repository.*BaseRepository" Infrastructure/automation-service/app/repositories/*.py` — all 12 repos (including Calendar) inherit
  - `ruff check .` passes
  - `timeout 600 ./deploy.sh` succeeds
  QA scenarios: happy — unified base, CalendarRepository fixed. Evidence `.omo/evidence/task-4-architecture-refactor.txt`
  Commit: Y | refactor: unify BaseRepository, fix CalendarRepository inheritance

- [x] 5. Consolidate route handlers + document all routes
  What to do / Must NOT do:
  - **Verify dead routes removed in Wave 1** (rules.py, climate.py, legacy PID routes) — no longer registered in `routes.py` or `routes/__init__.py`.
  - **Check `devices.py` (555 lines) vs `devices_crud.py` (400 lines)**: Ensure no overlapping endpoints. They should serve different domains (devices.py = state/control, devices_crud.py = registry CRUD).
  - **Verify no route file exceeds 400 LOC** after Wave 3 splits.
  - **Update AGENTS.md** with complete route inventory if any routes changed.
  - Run `ruff check .` + `npx tsc --noEmit && npm run build`
  - Deploy + verify
  Parallelization: Wave 5 | Blocked by: 4 | Blocks: 6 | Can parallelize with: —
  References: All 27+4 route files.
  Acceptance criteria:
  - All routes documented in AGENTS.md
  - No route file exceeds 400 LOC (except justified)
  - `timeout 600 ./deploy.sh` succeeds
  QA scenarios: happy — routes consolidated. Evidence `.omo/evidence/task-5-architecture-refactor.txt`
  Commit: Y | refactor: consolidate route handlers, document all routes

- [x] 6. Final verification + deploy
  What to do / Must NOT do:
  - Full verification: `ruff check .` + `npx tsc --noEmit && npm run build` + `timeout 600 ./deploy.sh`
  - Verify GET endpoints: `/api/devices/registry` (13 devices), `/api/lights/Veg Room/main/zone-status` (3+ lights), `/api/devices/channels` (13 channels)
  - Verify control loop running via journalctl
  - Update AGENTS.md if file structure changed
  Parallelization: Wave 6 | Blocked by: 1-5 | Blocks: —
  Acceptance criteria:
  - All checks pass, deploy succeeds, 13 devices + 3+ lights + 13 channels
  - No new errors in journalctl
  QA scenarios: happy — all refactoring complete. Evidence `.omo/evidence/task-6-architecture-refactor.txt`
  Commit: N | (final verification)

## Final verification wave
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Static checks + local verification
- [x] F4. Scope fidelity

## Commit strategy
- Wave 1: `refactor: remove 18 categories of legacy/superseded code`
- Wave 2: `refactor: deduplicate relay steal, hierarchy traversal, Redis keys, room prefix, YAML loading`
- Wave 3: `refactor: split 8 oversized files into focused modules`
- Wave 4: `refactor: unify BaseRepository, fix CalendarRepository inheritance`
- Wave 5: `refactor: consolidate route handlers, document all routes`
- Wave 6: (no commit — final verification)

## Success criteria
- Zero legacy/dead code remaining (18 categories removed)
- Relay steal logic deduplicated (1 helper instead of 7 copies)
- Redis keys use schema.py helpers (0 inline f-strings in redis/ directory)
- Room prefix validation in shared/cluster_topology.py
- No production file exceeds 500 LOC (8 files split)
- Unified BaseRepository in Infrastructure/shared/ (CalendarRepository fixed)
- All 12+ repos exported from __init__.py
- Dead routes removed (rules.py, climate.py, legacy PID)
- All routes documented in AGENTS.md
- `ruff check .`, `tsc --noEmit`, `npm run build` all pass
- 6 successful deploys (one per wave)
