# Production Safety - Learnings

## 2026-07-12 - Plan Execution Start
- Plan: production-safety.md (716 lines, 11 todos, 6 waves)
- Boulder: production-safety-92d90761, status=active
- Wave 1 starting: T1 (alembic), T2 (DELETE guard), T3 (F3 ban) — all independent, parallel dispatch

## Key Conventions
- All acceptance criteria use `cea_sensors_test`, NEVER `cea_sensors` (production)
- Do NOT deploy between waves — only T11 deploys
- F3 permanently banned from production HTTP
- 10% hardcoded default for missing light_target_intensity rows
- mode_parameters.main_light_intensity/supplemental_light_intensity DEPRECATED
- Zero new Redis keys — all new caches are in-memory Python dicts
- Atomic reference swaps in all update_*() methods
- asyncio.Event startup gate prevents control loop from ticking before data loaded
- Mid-ramp target change recalculation MUST be preserved in T5 rewrite

## 2026-07-12 - T2 DELETE Guard Implementation
- Added `X-Confirm-Destructive: true` header requirement to `DELETE /api/devices/registry/{device_id}` in production only (`is_production()` gate)
- Files modified: `Infrastructure/automation-service/app/routes/devices_crud.py`, `Infrastructure/frontend/src/services/api.ts`
- Backend: `delete_registry_device()` now checks `request.headers.get("X-Confirm-Destructive") != "true"` and raises HTTP 403 in production
- Frontend: `deleteDevice()` and `deleteLight()` in `api.ts` both send the header unconditionally
- No UI confirm dialog added; no cascade behavior changed; dev/test mode unaffected

## 2026-07-12 - T3 Complete: Subagent QA Safety Section Added to AGENTS.md
- Added "Subagent QA Safety (Critical — Permanent Ban)" under NON-NEGOTIABLE SYSTEM RULES
- F3 permanently banned from production HTTP; replaced with static checks (ruff, pytest, tsc, build, vitest, grep)
- All subagents banned from DELETE/POST/PUT against production endpoints
- Exception: guard-verification probe with non-existent device ID (e.g., 999) allowed
- Production endpoints 8000/8001/8003: GET read-only only
- File modified: AGENTS.md (project root)
- Verification: grep confirms "Subagent QA Safety" and "PERMANENTLY BANNED" present

## 2026-07-12 - T4 Starting: Repository Layer for light_target_intensity and light_programs
- New repositories: `app/repositories/light_target_intensity.py` and `app/repositories/light_programs.py`
- Added `get_mode_by_name(name: str) -> dict | None` to `RoomModeRepository`; replaces duplicated list-filter patterns
- Both repos registered lazily in `DatabaseManager.__init__` and exposed as properties
- Target intensity fallback: rows default to 10% via DB schema; callers use default if no row exists
- Tests required: `tests/test_light_target_intensity_repo.py` and `tests/test_light_programs_repo.py`; must use `cea_sensors_test`

## 2026-07-12 - T6 Complete: Simplify save_room_schedule + Rewrite POST /target + Add PUT /intensity
- Files modified:
  - `Infrastructure/automation-service/app/routes/schedules/room.py` — `save_room_schedule()` simplified
  - `Infrastructure/automation-service/app/routes/lights.py` — POST `/target` rewritten, PUT `/intensity` added
  - `Infrastructure/automation-service/app/routes/room_modes.py` — deprecation warnings added
  - `Infrastructure/automation-service/app/schemas/lights.py` — `LightIntensityUpdate` schema added
  - `Infrastructure/frontend/src/services/api.ts` — `updateLightIntensity()` method added
- `save_room_schedule()` changes:
  - No longer creates SUN/MOON rows for lights (T10 will delete existing ones)
  - No longer creates/updates `room_schedule` row
  - Still creates DAY/NIGHT rows for non-light devices
  - Updates `mode_parameters` directly with `day_start_time`, `night_start_time`, `light_ramp_up_minutes`, `light_ramp_down_minutes`
  - Publishes `MODE_CHANGED` event (was `SCHEDULE_CHANGED`)
- `sync_room_schedule_from_mode_parameters()` automatically uses simplified `save_room_schedule()`
- POST `/api/lights/{loc}/{cluster}/{device}/target` changes:
  - Looks up `device_id` from `device_registry`
  - Gets active mode (fallback `veg`), resolves `mode_id`
  - Writes to `light_target_intensity` table via `light_target_intensity_repo.set_intensity()`
  - Synchronous `scheduler.update_light_intensities()` cache refresh
  - Publishes `SCHEDULE_CHANGED` event
- PUT `/api/lights/{device_id}/intensity` (new):
  - Device-id-based endpoint
  - Same pattern as POST `/target`: lookup device, resolve mode, write to `light_target_intensity`, sync scheduler, publish event
- `update_room_parameters()` deprecation:
  - Logs warning when `main_light_intensity` or `supplemental_light_intensity` present in request
  - Directs callers to new PUT/POST intensity endpoints
- Frontend `api.ts`:
  - `updateLightIntensity(deviceId: number, intensity: number)` calls PUT `/api/lights/{deviceId}/intensity`
- Verification: ruff check passes, `tsc --noEmit` passes

## 2026-07-12 - T5 Complete: Scheduler Rewrite (is_in_photoperiod + get_schedule_intensity + light_programs)
- File modified: `Infrastructure/automation-service/app/control/scheduler.py` (full rewrite, ~770 lines)
- Test file: `Infrastructure/automation-service/tests/test_scheduler_rewrite.py` (24 tests, all pass)
- ruff check passes on both files
- 291 existing tests pass; 4 pre-existing failures in test_device_crud_endpoint.py (from T2 DELETE guard, NOT T5)

### What changed in scheduler.py:
1. **`__init__`**: Added 6 new in-memory cache fields:
   - `_mode_params: dict[tuple[str, str], dict]` — {(location, cluster): {mode_id, day_start, night_start, ramp_up, ramp_down}}
   - `_light_intensities: dict[tuple[int, int], float]` — {(device_id, mode_id): target_intensity}
   - `_light_programs: list[dict]` — all enabled programs
   - `_light_programs_by_room: dict[tuple[str, str], list[dict]]` — pre-indexed by (location, cluster) for O(1) lookup
   - `_device_lookup: dict[tuple[str, str, str], dict]` — {(location, cluster, device_name): {device_id, device_type, ...}}
   - `_ready: bool` — set True after first update_*() call
2. **4 atomic reference swap update methods**: `update_mode_parameters()`, `update_light_intensities()`, `update_light_programs()` (also pre-indexes by room), `update_device_lookup()`. All build new dict/list fully then assign — NEVER mutate in-place.
3. **`is_in_photoperiod()`**: Reads from `_mode_params` cache. Handles overnight wrap (day_start > night_start). Returns True (failsafe) when mode_params missing — NOT darkness.
4. **`get_schedule_intensity()`**: Signature UNCHANGED. Evaluation order:
   - Check light programs first (priority DESC, created_at ASC tiebreak, cycle mode, overnight wrap)
   - If no program and is_sun: look up `_light_intensities[(device_id, mode_id)]`. Missing → 10.0 default. Missing mode_params → 10.0 failsafe.
   - Apply FULL ramp logic (ramp-up, ramp-down, mid-ramp recalc, steady state)
   - If not is_sun → 0.0
5. **`_compute_ramped_intensity()`**: Extracted helper preserving ALL existing ramp logic verbatim (lines 355-557 of original). Includes mid-ramp target change recalculation.
6. **`_find_matching_program()`**: Filters by device_id (or None for room-level), mode_id (or None for all modes), time window (overnight wrap), day_of_week. Sorts by priority DESC, created_at ASC.
7. **`_evaluate_cycle_program()`**: On/off phase calculation within program window.
8. **`_evaluate_static_program()`**: Non-cycle programs with ramp logic using SEPARATE state key `(location, cluster, device_name, program_id)`.
9. **`get_light_intensity_details()`**: Returns dict with effective_intensity, nominal_intensity, ramp_progress.
10. **`get_schedule_state()`**: Light devices use `is_in_photoperiod()` → 1/0. Non-light devices still use `self.schedules` (unchanged).
11. **`_is_light_device()`**: Checks `_device_lookup` for `device_type == "light"`. Falls back to False (schedule-based) if device not in lookup.
12. **`_compute_start_end_datetimes()`**: Extracted helper for overnight-aware datetime computation (used by both photoperiod and program paths).
13. **`_get_ramp_progress()`**: Extracted helper for ramp progress calculation (used by `get_light_intensity_details`).

### Key design decisions:
- `MINIMUM_LIGHT_INTENSITY` (10.0) promoted to module-level constant (was local variable in `get_schedule_intensity`)
- Program ramp state key is `(location, cluster, device_name, program_id)` — does NOT share photoperiod ramp state key `(location, cluster, device_name)`
- When a program ends, photoperiod ramp resumes from its own state unchanged
- `self.schedules` retained for non-light DAY/NIGHT rows (heaters, fans, etc.)
- `get_schedule_intensity()` signature unchanged — callers (device_processor.py, light_effective_setpoint_logging.py, light_ramp_calculator.py) need NO changes
- `is_in_photoperiod()` signature unchanged — callers (control_engine.py, climate_resolver.py, lights.py) need NO changes

### Test coverage (24 tests in test_scheduler_rewrite.py):
1. is_in_photoperiod reads from mode_params cache, not self.schedules ✓
2. get_schedule_intensity returns light_target_intensity value when is_sun ✓
3. get_schedule_intensity returns 10.0 when no light_target_intensity row ✓
4. get_schedule_intensity returns 0.0 when not is_sun ✓
5. Override program replaces intensity during sun ✓
6. Supplemental program adds intensity during dark ✓
7. Cycle mode pulses on/off ✓
8. Higher priority wins; ties broken by created_at ASC ✓
9. Ramp logic uses mode_parameters ramp durations ✓
10. Non-light get_schedule_state still works from self.schedules ✓
11. is_in_photoperiod returns True (failsafe) when mode_params missing ✓
12. get_schedule_intensity returns 10.0 when mode_params missing ✓
13. Overnight program matches at 23:00 and 01:00 ✓
14. Mid-ramp target change recalculation ✓
- Bonus: Program ramp state uses separate key ✓
- Bonus: get_light_intensity_details returns correct dict ✓
- Bonus: Steady state has no ramp_progress ✓
- Bonus: Dark period returns zero effective/nominal ✓

## 2026-07-12 - T10 Complete: Remove Old Code References + Documentation Cleanup

### Files Modified
- `Infrastructure/automation-service/alembic/versions/04fbbb9b5ba4_remove_obsolete_light_schedule_rows.py` — new migration
- `Infrastructure/automation-service/app/routes/lights.py` — get_zone_lights_status reads from light_target_intensity
- `Infrastructure/automation-service/app/routes/schedules/room.py` — get_room_schedule reads from mode_parameters
- `Infrastructure/automation-service/app/routes/schedules/base.py` — removed write_schedule_state calls
- `Infrastructure/automation-service/app/routes/schedules/utils.py` — _build_schedule_state uses mode_parameters + light_target_intensity
- `Infrastructure/automation-service/app/routes/devices_crud.py` — removed schedule cascade for lights
- `Infrastructure/automation-service/app/routes/redis_state.py` — removed Redis read/write, DB-only
- `Infrastructure/automation-service/app/redis/redis_operations.py` — removed SchedulesMixin import + delegates
- `Infrastructure/automation-service/app/redis/__init__.py` — removed write/read_schedule_state delegates
- `Infrastructure/automation-service/app/repositories/schedules.py` — deprecated get_room_schedule, removed _cache_key_room_schedule
- `Infrastructure/automation-service/app/services/schedule_state.py` — load_schedule_state_to_redis is now no-op
- `Infrastructure/shared/redis_keys.py` — removed schedule_state_infix
- `AGENTS.md` — added Schedule Architecture (3-Concept Model) section
- `ARCHITECTURE.md` — updated control loop description + light schedule section
- `ARCHITECTURE_SCHEMATIC.md` — updated control loop + added schedule tables
- `tests/test_device_crud_endpoint.py` — updated test_delete_light_with_cascade

### Key Changes
1. **Migration 04fbbb9b5ba4**: Deletes per-device SUN/MOON rows for lights and room_schedule rows. Includes pre-flight check to abort if any light lacks a light_target_intensity row. Downgrade is no-op (deleted data cannot be restored).
2. **get_zone_lights_status**: Now queries light_target_intensity joined with device_registry to build a device_name→intensity map, replacing the old schedules_list-based SUN/DAY row lookup.
3. **get_room_schedule**: Simplified to read day_start_time/night_start_time directly from mode_parameters for the active mode.
4. **Dead Redis code removed**: SchedulesMixin class deleted entirely. All write_schedule_state/read_schedule_state callers updated to no longer write dead keys.
5. **delete_registry_device**: No longer cascade-deletes schedules for lights (there are none to delete). effective_setpoints cascade remains.

### Verification
- `ruff check .` — passes
- `pytest tests/ -q` — 295 passed, 3 warnings (pre-existing)
- `tsc --noEmit` — passes
- `grep -rn "expand_light_schedules_for_control" Infrastructure/` — returns nothing
- `grep -rn "SchedulesMixin" Infrastructure/automation-service/app/redis/` — returns nothing (file deleted)
- `grep -rn "schedule_state_infix" Infrastructure/` — returns nothing
- `grep -rn "_cache_key_room_schedule" Infrastructure/automation-service/app/repositories/schedules.py` — returns nothing
