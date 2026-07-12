# production-safety - Work Plan

## TL;DR (For humans)

**What you'll get:** The schedule system is completely redesigned with a single source of truth for each concept. Photoperiod comes from mode_parameters (room-level). Per-light intensity lives in a new light_target_intensity table (mode-specific), replacing the now-deprecated `main_light_intensity`/`supplemental_light_intensity` fields in mode_parameters. Supplemental light programs (far-red, UV bursts, cycle mode) use a new light_programs table. No more duplicated data, no runtime synthesis band-aid, no lights going dark because of missing schedule rows. If a light somehow has no config, it defaults to 10% (not 0%) so you can see something's wrong. If a room is missing mode_parameters entirely, lights go to failsafe: relay ON + 10% intensity + CRITICAL alarm — never darkness. No subagent can ever hard-delete production devices again — the DELETE endpoint requires a confirmation header. The QA reviewer that destroyed production is permanently banned. All old code and documentation references to the removed systems are cleaned up.

**Why this approach:** The old architecture duplicated the photoperiod across three places (mode_parameters, room_schedule rows, per-device SUN/MOON rows), causing synchronization bugs that left lights dark for 36+ hours. The new architecture has exactly one source per concept and adds light programs (inspired by professional CEA controllers like Tri-Chan and Spider Farmer GGS) for future far-red/UV use cases.

**What it will NOT do:**
- Will NOT change non-light device schedules (DAY/NIGHT rows for heaters, fans, etc. stay in schedules table — they control ON/OFF enable for those devices).
- Will NOT change the climate_periods system (setpoints for non-light devices — already works correctly).
- Will NOT change the review-work skill itself (shared/builtin skill). The ban is enforced via AGENTS.md.
- Will NOT change safety_level (hardware EEPROM cap — stays as-is, 0=100% no limit).

**Effort:** Large
**Risk:** Medium-High — Scheduler rewrite is critical control-loop code; changes must be tested carefully.
**Decisions to sanity-check:** 10% hardcoded default for missing light_target_intensity rows; light_programs support both time-slot and cycle mode; override programs replace normal intensity during sun; supplemental programs add light during dark.

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> TL;DR (machine): Large, Medium-High risk — redesign schedule architecture: photoperiod from mode_parameters, per-light intensity in new light_target_intensity table, light_programs for supplemental/override; remove runtime synthesis; add DELETE guard; ban F3; clean up old code/docs; deploy

## Scope
### Must have
- New `light_target_intensity` table: device_id + mode_id → target_intensity. Mode-specific per-light intensity. Replaces the deprecated `main_light_intensity`/`supplemental_light_intensity` fields in mode_parameters. Default 10% if no row exists (hardcoded failsafe, not safety_level).
- New `light_programs` table: device-level or room-level supplemental/override programs with time-slot and cycle mode support. Mode-specific (nullable = all modes). Priority-based conflict resolution.
- Scheduler rewrite: `is_in_photoperiod()` reads from mode_parameters cache. `get_schedule_intensity()` reads from light_target_intensity + mode_parameters. Light programs evaluated per-tick per-light.
- `save_room_schedule()` simplified: only updates mode_parameters + creates DAY/NIGHT rows for non-light devices. Stops creating per-device SUN/MOON rows for lights and room_schedule row.
- `create_light()` / `create_registry_device()` create light_target_intensity row for active mode (intensity=10% default).
- New intensity update endpoint: `PUT /api/lights/{device_id}/intensity` updates light_target_intensity.
- `expand_light_schedules_for_control()` removed from schedule_merge.py. `merge_schedules_with_config()` simplified (keep validation functions).
- AlarmManager: CRITICAL when room has no mode_parameters for active mode. WARNING when light has no light_target_intensity row.
- `DELETE /api/devices/registry/{id}` requires `X-Confirm-Destructive: true` header in production (403 without it).
- F3 (Real Manual QA) permanently banned from production HTTP. Documented in AGENTS.md.
- All old code references to removed systems cleaned up. Documentation updated to reflect new architecture.
- Deploy dfr-panel-cleanup frontend + backend changes + production safety + schedule architecture redesign.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT change non-light device DAY/NIGHT schedule rows in the `schedules` table — these control ON/OFF enable for heaters, fans, dehumidifiers. They stay as-is.
- Do NOT change the `climate_periods` system — it provides setpoints for non-light devices and already works correctly.
- Do NOT change the `room_light_schedule` table — it's a legacy table that ClimatePeriodResolver reads. Leave it alone; it's not part of the new architecture but removing it could break existing code.
- Do NOT change the `safety_level` field in device_registry — it's a hardware EEPROM cap (0=100% no limit). The 10% default is a separate hardcoded value in the Scheduler.
- Do NOT change the review-work skill files — they're shared/builtin. The ban is enforced via AGENTS.md documentation.
- Do NOT change `mode_parameters` table schema — only read from it. The `save_room_schedule()` endpoint updates it.
- Do NOT auto-create room_schedule rows — photoperiod comes from mode_parameters directly.
- Do NOT add soft-delete to device_registry — the X-Confirm-Destructive header guard is sufficient.
- Do NOT trigger failsafe for individual lights missing light_target_intensity rows — the 10% default keeps them visibly low; only CRITICAL when a room has no mode_parameters at all.
- Do NOT leave `main_light_intensity`/`supplemental_light_intensity` fields in mode_parameters active — they are deprecated by `light_target_intensity`. Document them as deprecated; do NOT remove the columns (avoid migration risk), but stop reading them in the Scheduler.
- Do NOT deploy intermediate commits between waves — only T11 deploys. Intermediate commits are for version control only.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + framework: pytest (backend), vitest (frontend)
- Evidence: .omo/evidence/task-<N>-production-safety.<ext>

## Execution strategy
### Parallel execution waves
- **Wave 1 (parallel):** T1 (alembic migration), T2 (DELETE guard), T3 (F3 ban). Independent files.
- **Wave 2 (depends on T1):** T4 (repositories). Needs tables to exist.
- **Wave 3 (depends on T4):** T5 (Scheduler rewrite). Needs repositories.
- **Wave 4 (parallel, depends on T5):** T6 (background_tasks data loading), T7 (create_light intensity), T8 (save_room_schedule cleanup + intensity endpoint), T9 (remove synthesis + AlarmManager).
- **Wave 5 (depends on T9 + T5):** T10 (old code cleanup + documentation).
- **Wave 6 (depends on all):** T11 (deploy + verify).
- **CRITICAL GUARDRAIL:** Do NOT deploy intermediate commits between waves. Each wave produces a commit, but only T11 runs `deploy.sh`. Deploying after Wave 3 (T5 Scheduler rewrite) without T6 (data loading) would leave the Scheduler with empty caches — lights go to failsafe (10% + relay ON). Deploying after Wave 4 without T10 (cleanup) leaves old rows in the DB (harmless but messy). Only T11 is a safe deploy point.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 4 | 2, 3 |
| 2 | — | 11 | 1, 3 |
| 3 | — | 11 | 1, 2 |
| 4 | 1 | 5 | — |
| 5 | 4 | 6, 7, 8, 9 | — |
| 6 | 5 | 11 | 7, 8, 9 |
| 7 | 5 | 11 | 6, 8, 9 |
| 8 | 5 | 11 | 6, 7, 9 |
| 9 | 5 | 10 | 6, 7, 8 |
| 10 | 9 | 11 | — |
| 11 | 1-10 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Alembic migration: create light_target_intensity + light_programs tables + migrate existing light intensities
  What to do / Must NOT do:
  - **Create `light_target_intensity` table:**
    ```sql
    CREATE TABLE light_target_intensity (
        device_id INTEGER NOT NULL REFERENCES device_registry(device_id) ON DELETE CASCADE,
        mode_id INTEGER NOT NULL REFERENCES room_modes(id),
        target_intensity REAL NOT NULL DEFAULT 10.0 CHECK (target_intensity >= 0 AND target_intensity <= 100),
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (device_id, mode_id)
    );
    ```
  - **Create `light_programs` table:**
    ```sql
    CREATE TABLE light_programs (
        id SERIAL PRIMARY KEY,
        device_id INTEGER REFERENCES device_registry(device_id) ON DELETE CASCADE,
        location TEXT NOT NULL,
        cluster TEXT NOT NULL DEFAULT 'main',
        mode_id INTEGER REFERENCES room_modes(id),
        name TEXT NOT NULL,
        program_type TEXT NOT NULL CHECK (program_type IN ('supplemental', 'override')),
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        cycle_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        cycle_on_seconds INTEGER,
        cycle_off_seconds INTEGER,
        target_intensity REAL NOT NULL CHECK (target_intensity >= 0 AND target_intensity <= 100),
        ramp_up_minutes INTEGER NOT NULL DEFAULT 0,
        ramp_down_minutes INTEGER NOT NULL DEFAULT 0,
        day_of_week INTEGER,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        priority INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX idx_light_programs_lookup ON light_programs (location, cluster, enabled);
    CREATE INDEX idx_light_programs_device ON light_programs (device_id, enabled);
    ```
  - **Data migration:** Read existing per-device SUN rows from `schedules` table by joining `device_registry` on `device_type = 'light'` (NOT `device_name LIKE 'light_%'` — naming conventions are not guaranteed). For each light, look up the device_id from device_registry and create `light_target_intensity` rows for ALL modes that exist for that room in mode_parameters. Use `mode_parameters.main_light_intensity` (mode-specific, already per-mode) as the intensity value per mode — NOT the per-device SUN row intensity (which is mode-agnostic and wrong for veg vs flower). Use `ON CONFLICT (device_id, mode_id) DO UPDATE SET target_intensity = EXCLUDED.target_intensity, updated_at = NOW()`.
  - **Migration order:** Due to room modes having mode_id referencing room_modes, and the existing schedules not having explicit mode_id, create rows for ALL modes per room using `mode_parameters.main_light_intensity` as the per-mode intensity value. This ensures mode-specific intensities are correct (e.g., veg 60% vs flower 100%).
  - **Must NOT** delete any existing schedules rows in the migration — that cleanup happens later.
  - **Must NOT** change device_registry schema.
  - **Must NOT** change room_modes schema.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 4 | Can parallelize with: 2, 3
  References:
  - `Infrastructure/automation-service/app/models/device_registry.py:45-73` (LightDevice model — has device_id, location, cluster)
  - Existing schedules data: `SELECT device_name, location, cluster, target_intensity FROM schedules WHERE device_name LIKE 'light_%' AND mode = 'SUN' AND enabled = true`
  - `Infrastructure/automation-service/app/repositories/devices.py:370-386` (get_light_by_id — used to look up device_id by name)
  - `Infrastructure/automation-service/alembic/versions/` (existing migration directory)
  Acceptance criteria:
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT * FROM light_target_intensity ORDER BY device_id, mode_id;"` returns rows for all lights × modes.
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT count(*) FROM light_programs;"` returns 0 (empty, ready for future use).
  - Existing schedules table UNCHANGED (migration doesn't delete anything).
  - Migration used `mode_parameters.main_light_intensity` per mode (verify: `SELECT lti.device_id, lti.mode_id, lti.target_intensity, mp.main_light_intensity FROM light_target_intensity lti JOIN mode_parameters mp ON lti.mode_id = mp.mode_id WHERE lti.target_intensity != mp.main_light_intensity;` returns nothing — all migrated intensities match mode_parameters).
  QA scenarios: happy — migration creates intensity rows for existing lights × modes with correct target_intensity values. failure — migration idempotent (running twice doesn't create duplicates). Evidence `.omo/evidence/task-1-production-safety.txt`
  Commit: Y | feat(db): add light_target_intensity + light_programs tables, migrate existing intensities

- [x] 2. Require X-Confirm-Destructive header for DELETE on device_registry in production
  What to do / Must NOT do:
  - **Backend:** In `delete_registry_device()` (devices_crud.py:305-372), add a production guard:
    ```python
    from shared.fastapi_helpers import is_production
    from fastapi import Request

    @router.delete("/api/devices/registry/{device_id}")
    async def delete_registry_device(
        device_id: int,
        request: Request,
        ...
    ) -> dict[str, Any]:
        if is_production() and request.headers.get("X-Confirm-Destructive") != "true":
            raise HTTPException(status_code=403, detail="Destructive operation on device_registry requires X-Confirm-Destructive: true header in production.")
        # ... existing logic
    ```
  - **Frontend:** In `api.ts`, `deleteDevice()` (line 167-170) and `deleteLight()` (line 242-245) must send the header:
    ```typescript
    async deleteDevice(device_id: number): Promise<{ success: boolean }> {
      const response = await this.automationClient.delete(`/api/devices/registry/${device_id}`, {
        headers: { 'X-Confirm-Destructive': 'true' }
      });
      return response.data;
    }
    ```
  - **Must NOT** block DELETE in development/test mode.
  - **Must NOT** add a confirm dialog in the UI — that's a UX concern, not a backend concern.
  - **Must NOT** change the cascade behavior (schedules + effective_setpoints still cascade-delete).
  Parallelization: Wave 1 | Blocked by: — | Blocks: 11 | Can parallelize with: 1, 3
  References:
  - `Infrastructure/automation-service/app/routes/devices_crud.py:305-372` (delete_registry_device — add guard)
  - `Infrastructure/shared/fastapi_helpers.py:14-21` (is_production — returns True when ENV=production)
  - `Infrastructure/frontend/src/services/api.ts:167-170` (deleteDevice — add header)
  - `Infrastructure/frontend/src/services/api.ts:242-245` (deleteLight — add header)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/routes/devices_crud.py` passes.
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes.
  - `grep -n "X-Confirm-Destructive" Infrastructure/frontend/src/services/api.ts` returns at least 2 matches.
  - New pytest `tests/test_delete_guard.py`: blocked in production without header (403), allowed with header, allowed in dev without header.
  QA scenarios: happy — frontend DELETE works in production with header; dev works without. failure — subagent curl without header gets 403 in production. Evidence `.omo/evidence/task-2-production-safety.txt`
  Commit: Y | feat(safety): require X-Confirm-Destructive header for device registry DELETE in production

- [x] 3. Permanently ban F3 from production HTTP in AGENTS.md + define static-checks-only QA protocol
  What to do / Must NOT do:
  - Add "Subagent QA Safety" section under "NON-NEGOTIABLE SYSTEM RULES" in AGENTS.md:
    - F3 (Real Manual QA) is PERMANENTLY BANNED from making HTTP requests to production endpoints.
    - F3 is replaced with: `cd Infrastructure/automation-service && ruff check . && pytest tests/ -q` (must use cea_sensors_test), `cd Infrastructure/frontend && npx tsc --noEmit && npm run build && npx vitest run`, plus grep verification checks from the plan.
    - NO subagent may call DELETE, POST, or PUT against production endpoints. Only GET is allowed for read-only verification (e.g., checking API responses, alarm states, device states).
    - **Exception:** Subagents MAY send `curl -X DELETE` with a non-existent device ID (e.g., 999) to verify the `X-Confirm-Destructive` guard returns 403. This tests the guard without touching real data. No subagent may DELETE a real device ID.
    - Production endpoints at ports 8001/8000/8003: GET is allowed for read-only verification. DELETE/POST/PUT are banned (except the guard-verification exception above).
  - **Must NOT** modify the review-work skill files themselves.
  - **Must NOT** remove F1, F2, or F4 from the Final Verification Wave.
  Parallelization: Wave 1 | Blocked by: — | Blocks: 11 | Can parallelize with: 1, 2
  References:
  - `AGENTS.md` (project root — add Subagent QA Safety section)
  - `AGENTS.md` (CRITICAL SAFETY RULE section at top — reference and extend it)
  Acceptance criteria:
  - `grep -n "Subagent QA Safety" AGENTS.md` returns a match.
  - `grep -n "PERMANENTLY BANNED" AGENTS.md` returns a match.
  QA scenarios: happy — AGENTS.md has the rule. failure — rule missing. Evidence `.omo/evidence/task-3-production-safety.txt`
  Commit: Y | docs(agents): ban F3 from production HTTP, define static-checks-only QA protocol

- [x] 4. Create repositories for light_target_intensity + light_programs CRUD
  What to do / Must NOT do:
  - **Add `get_mode_by_name(name: str) -> dict | None` to `RoomModeRepository`** (`repositories/room_modes.py`): The existing code in `room_modes.py:184-187` and `room_modes.py:238-242` duplicates a list-filter pattern: `modes = await db.room_mode_repo.get_room_modes(); mode_info = next((m for m in modes if m["name"] == mode_name), None)`. Replace with a clean repository method: `SELECT * FROM room_modes WHERE name = $1 LIMIT 1`. Update the 2 existing call sites to use the new method. This is needed by T7 and T8 which both need mode-by-name lookup for the "veg" fallback.
  - Create `Infrastructure/automation-service/app/repositories/light_target_intensity.py`:
    - `class LightTargetIntensityRepository(BaseRepository)`
    - `get_intensity(device_id: int, mode_id: int) -> float | None` — returns target_intensity or None
    - `get_intensities_for_room(location: str, cluster: str, mode_id: int) -> dict[int, float]` — returns {device_id: target_intensity} for all lights in a room for a mode
    - `set_intensity(device_id: int, mode_id: int, target_intensity: float) -> bool` — INSERT ... ON CONFLICT DO UPDATE
    - `get_all_intensities() -> dict[tuple[int, int], float]` — for bulk loading at startup
  - Create `Infrastructure/automation-service/app/repositories/light_programs.py`:
    - `class LightProgramsRepository(BaseRepository)`
    - `get_active_programs(location: str, cluster: str, mode_id: int) -> list[dict]` — returns enabled programs matching location/cluster/mode (or mode_id IS NULL)
    - `get_programs_for_device(device_id: int) -> list[dict]`
    - `create_program(...) -> dict`
    - `update_program(id: int, ...) -> dict | None`
    - `delete_program(id: int) -> bool`
    - `get_all_programs() -> list[dict]` — for bulk loading at startup
  - Register both repositories in DatabaseManager (database.py) alongside existing repos.
  - **Must NOT** inherit from the legacy ScheduleRepository pattern — these are new, clean repos.
  - **Must NOT** add caching — the Scheduler caches the data it loads; repos just read/write.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 5 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/app/repositories/devices.py:98-103` (BaseRepository pattern)
  - `Infrastructure/automation-service/app/repositories/schedules.py:16-62` (ScheduleRepository — reference for pool.acquire pattern)
  - `Infrastructure/automation-service/app/database.py` (DatabaseManager — register new repos)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/repositories/light_target_intensity.py app/repositories/light_programs.py app/database.py` passes.
  - New pytest `tests/test_light_target_intensity_repo.py`: get/set/get_all intensities.
  - New pytest `tests/test_light_programs_repo.py`: CRUD + get_active_programs.
  - `grep -n "light_target_intensity_repo\|light_programs_repo" Infrastructure/automation-service/app/database.py` returns matches.
  QA scenarios: happy — repos correctly read/write new tables. failure — non-existent device_id/mode_id raises FK error. Evidence `.omo/evidence/task-4-production-safety.txt`
  Commit: Y | feat(repos): add light_target_intensity + light_programs repositories

- [x] 5. Rewrite Scheduler: is_in_photoperiod + get_schedule_intensity + light_programs evaluation
  What to do / Must NOT do:
  - **Add cached data to Scheduler.__init__():**
    - `self._mode_params: dict[tuple[str, str], dict]` — {(location, cluster): {mode_id, day_start, night_start, ramp_up, ramp_down}} — **must include mode_id** so `get_schedule_intensity()` can look up `self._light_intensities[(device_id, mode_id)]` without changing its signature.
    - `self._light_intensities: dict[tuple[int, int], float]` — {(device_id, mode_id): target_intensity}
    - `self._light_programs: list[dict]` — all enabled programs (pre-indexed by (location, cluster) at load time for O(1) lookup per tick — do NOT iterate the full list for every light)
    - `self._device_lookup: dict[tuple[str, str, str], dict]` — {(location, cluster, device_name): {device_id, ...}} for quick device → device_id mapping
    - `self._ready: bool = False` — set to True after first `update_*()` call; control loop checks this before making light decisions (see T6 startup gate)
  - **Add update methods (all must use atomic reference swap — build new dict/list fully, then assign to self._*; NEVER mutate in-place):**
    - `update_mode_parameters(self, params: dict[tuple[str, str], dict])` — set cached mode_parameters (atomic swap: build new dict, then `self._mode_params = new_dict`)
    - `update_light_intensities(self, intensities: dict[tuple[int, int], float])` — set cached intensities (atomic swap)
    - `update_light_programs(self, programs: list[dict])` — set cached programs (atomic swap; pre-index by (location, cluster) into `self._light_programs_by_room`)
    - `update_device_lookup(self, devices: dict)` — set cached device lookup (atomic swap)
  - **Rewrite `is_in_photoperiod()`:** Read from `self._mode_params[(location, cluster)]` instead of iterating self.schedules. Check if current_time is within [day_start, night_start). Handle overnight wrap (if day_start > night_start, it's an overnight photoperiod). If no mode_params for (location, cluster), return True (failsafe: treat as "in photoperiod" so lights go to 10% + relay ON, NOT darkness). The CRITICAL alarm fires from T9. This prevents the original 36-hour darkness incident — a missing config produces visible-low light, not darkness.
  - **Rewrite `get_schedule_intensity()`:**
    1. Check light programs: find enabled programs where (device_id matches OR device_id IS NULL) AND (mode_id matches active mode OR mode_id IS NULL) AND current_time in window (handle overnight wrap: if start_time > end_time, match if current_time >= start_time OR current_time < end_time) AND day_of_week matches or is NULL. Sort by priority DESC (ties broken by created_at ASC — oldest first). If a program matches:
       - If cycle_enabled: calculate on/off phase within window. If in on-phase → program.target_intensity. If in off-phase → 0.0.
       - If not cycle: return program.target_intensity (with ramp logic using program.ramp_up_minutes/ramp_down_minutes).
    2. If no program matches and is_sun: **First check if mode_params exists for (location, cluster). If `self._mode_params.get((location, cluster))` is None → return 10.0 immediately (failsafe — do NOT access `["mode_id"]` on a missing entry, that would KeyError).** Otherwise, look up `active_mode_id = self._mode_params[(location, cluster)]["mode_id"]` then `self._light_intensities.get((device_id, active_mode_id))`. If found → use that as `target_intensity` and apply the **FULL ramp logic from the existing code (scheduler.py:355-557)**, including:
       - **Ramp-up detection**: `time_since_start < ramp_up_duration` → ramp from MINIMUM_LIGHT_INTENSITY to target
       - **Ramp-down detection**: `time_until_end < ramp_down_duration` → ramp from target to effective minimum
       - **CRITICAL — Mid-ramp target change recalculation** (lines 404-416 for ramp-up, lines 498-510 for ramp-down): When `target_intensity` from `_light_intensities` differs from what's stored in `ramp_state["target_intensity"]`, the ramp MUST recalculate: `start_intensity = current_effective`, `target_intensity = new_target`, `ramp_start_timestamp = now`, `ramp_duration = remaining_time`. This lets operators change the target mid-ramp and the ramp adapts — speeds up/slows down to reach the new target within the remaining scheduled window. **This logic MUST be preserved in the rewrite — do NOT simplify it away.**
       - **Steady state** (lines 547-557): `return clamped target_intensity` when not in ramp window
       If not found in `_light_intensities` → return 10.0 (hardcoded safety default).
    3. If not is_sun → return 0.0.
  - **Rewrite `get_light_intensity_details()`:** Same as get_schedule_intensity but returns dict with effective_intensity, nominal_intensity, ramp_progress. The nominal is the non-ramped target.
  - **Rewrite `get_schedule_state()`:** For non-light devices, still use self.schedules to find DAY/NIGHT rows (unchanged behavior). For light devices, use is_in_photoperiod() → return 1 if is_sun else 0.
  - **Must NOT** change the ramp state mechanism (`_light_ramp_state`) — keep the existing ramp logic for photoperiod ramps, just change where target_intensity and durations come from. Program ramps use a SEPARATE state key: `(location, cluster, device_name, program_id)` — do NOT share the photoperiod ramp state key `(location, cluster, device_name)`. When a program ends, the photoperiod ramp resumes from its own state unchanged.
  - **Must NOT** change `get_schedule_intensity()` signature — callers (device_processor.py, light_effective_setpoint_logging.py) should not need changes.
  - **Must NOT** remove the `self.schedules` list entirely — non-light DAY/NIGHT rows are still needed. The Scheduler still holds self.schedules for non-light device scheduling.
  Parallelization: Wave 3 | Blocked by: 4 | Blocks: 6, 7, 8, 9 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/app/control/scheduler.py:23-36` (Scheduler.__init__ — add cached data)
  - `Infrastructure/automation-service/app/control/scheduler.py:71-122` (is_schedule_active — keep for non-light devices)
  - `Infrastructure/automation-service/app/control/scheduler.py:566-620` (is_in_photoperiod — rewrite to use mode_params cache)
  - `Infrastructure/automation-service/app/control/scheduler.py:218-564` (get_schedule_intensity — rewrite to use light_intensities + light_programs)
  - `Infrastructure/automation-service/app/control/scheduler.py:625-700` (get_light_intensity_details — rewrite)
  - `Infrastructure/automation-service/app/control/scheduler.py:124-151` (get_schedule_state — update light path)
  - `Infrastructure/automation-service/app/control/device_processor.py:354-413` (_build_light_decision — calls get_light_intensity_details, should NOT need changes)
  - `Infrastructure/automation-service/app/control/control_engine.py:369-391` (calculates is_sun — calls is_in_photoperiod)
  - Mode parameters data: mode_parameters.day_start_time, night_start_time, light_ramp_up_minutes, light_ramp_down_minutes
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/control/scheduler.py` passes.
  - New pytest `tests/test_scheduler_rewrite.py`:
    1. is_in_photoperiod reads from mode_params cache, not self.schedules.
    2. get_schedule_intensity returns light_target_intensity value when is_sun and no program active.
    3. get_schedule_intensity returns 10.0 when is_sun and no light_target_intensity row exists.
    4. get_schedule_intensity returns 0.0 when not is_sun and no supplemental program.
     5. Override program replaces intensity during sun period.
     6. Supplemental program adds intensity during dark period.
     7. Cycle mode pulses on/off within window.
     8. Higher priority program wins when multiple match. Ties (same priority) broken by created_at ASC.
     9. Ramp logic uses mode_parameters ramp durations.
     10. Non-light device get_schedule_state still works from self.schedules.
     11. is_in_photoperiod returns True (failsafe) when mode_params missing — NOT False/dark.
     12. get_schedule_intensity returns 10.0 when mode_params missing (failsafe: relay ON + 10%).
     13. Overnight light_program (start_time=22:00, end_time=02:00) matches at 23:00 and 01:00.
     14. Mid-ramp target change: during ramp-up from 10%→30%, when target changes to 65%, the ramp recalculates from current effective intensity to 65% within the remaining scheduled window. The ramp_state["target_intensity"] updates and the ramp speeds up.
   - Existing tests pass (update any that depended on old synthesis).
  QA scenarios: happy — light_v_1 gets 30% from light_target_intensity during sun; light at 0% during dark; override program replaces intensity. failure — no mode_params → is_in_photoperiod returns True, get_schedule_intensity returns 10.0 + relay ON + CRITICAL alarm (NOT darkness). Evidence `.omo/evidence/task-5-production-safety.txt`
  Commit: Y | feat(scheduler): rewrite is_in_photoperiod + get_schedule_intensity for new architecture

- [x] 6. Load mode_parameters + light_target_intensity + light_programs into Scheduler at startup and on config changes
  What to do / Must NOT do:
  - **Startup gate:** Add an `asyncio.Event` (`self._scheduler_ready`) in the Scheduler or background_tasks. The control loop MUST `await self._scheduler_ready.wait()` before its first tick. Set the event after all four `update_*()` calls complete. This prevents the control loop from running with empty caches (which would cause the failsafe path or stale data).
  - In `background_tasks.py` startup (after database + config initialized, before first control loop tick):
    1. Load mode_parameters: `SELECT location, cluster, mode_id, day_start_time, night_start_time, light_ramp_up_minutes, light_ramp_down_minutes FROM mode_parameters` → filter to ONLY the active mode per room (query `room_mode_repo.get_active_mode(location, cluster)` for each room) → build dict `{(location, cluster): {mode_id, day_start, night_start, ramp_up, ramp_down}}` and call `scheduler.update_mode_parameters(params)`.
    2. Load light_target_intensity: `repo.get_all_intensities()` → `scheduler.update_light_intensities(intensities)`.
    3. Load light_programs: `repo.get_all_programs()` → pre-index by (location, cluster) → `scheduler.update_light_programs(programs)`.
    4. Load device lookup from config: `config.get_devices()` → `scheduler.update_device_lookup(devices)`.
    5. Set `self._scheduler_ready.set()` — control loop can now start ticking.
  - On config change events (`SCHEDULE_CHANGED`, device registry changes):
    1. Reload light_target_intensity (might have changed).
    2. Reload light_programs (might have changed).
    3. Reload device lookup (devices might have been added/removed).
    4. Call `scheduler.update_*()` with new data.
  - On `MODE_CHANGED` events (mode transition — already exists in `ConfigEventType`):
    1. Reload mode_parameters (filter to the NEW active mode per room).
    2. Reload light_target_intensity (mode-specific, so values may differ).
    3. Call `scheduler.update_mode_parameters()` + `scheduler.update_light_intensities()`.
    4. Also call `scheduler.update_light_programs()` (programs may be mode-specific).
  - **Modify `ModeTransitionService._trigger_scheduler_refresh()`** (`mode_transition_service.py:248-261`): After calling `merge_schedules_with_config()` + `scheduler.update_schedules(merged)`, ALSO call `scheduler.update_mode_parameters()`, `scheduler.update_light_intensities()`, `scheduler.update_light_programs()` with freshly loaded data. This is the mode-transition code path that the old `update_schedules()` call didn't cover. Publish a `MODE_CHANGED` event so `background_tasks` also reloads (belt-and-suspenders).
  - The existing `merge_schedules_with_config()` call in background_tasks stays for non-light device schedules. It just no longer does light synthesis (that's removed in T9).
  - **Must NOT** reload mode_parameters on every `SCHEDULE_CHANGED` event — only on `MODE_CHANGED` events. `SCHEDULE_CHANGED` triggers intensity/programs/device reload, not mode_params reload.
  - **Must NOT** block the control loop on data loading — the `asyncio.Event` gate holds the FIRST tick only; subsequent ticks proceed without waiting.
  Parallelization: Wave 4 | Blocked by: 5 | Blocks: 11 | Can parallelize with: 7, 8, 9
  References:
  - `Infrastructure/automation-service/app/background_tasks.py:70-80` (start method — add data loading + asyncio.Event gate)
  - `Infrastructure/automation-service/app/background_tasks.py` (config event handler — reload on SCHEDULE_CHANGED + MODE_CHANGED)
  - `Infrastructure/automation-service/app/container.py` (initialization — alternative call site)
  - `Infrastructure/automation-service/app/database.py` (database — has repos, pools)
  - `Infrastructure/automation-service/app/services/mode_transition_service.py:248-261` (_trigger_scheduler_refresh — MUST also call update_mode_parameters/intensities/programs)
  - `Infrastructure/automation-service/app/events/__init__.py` (ConfigEventType enum — MODE_CHANGED already exists here, NOT in config.py)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/background_tasks.py app/services/mode_transition_service.py` passes.
  - New pytest `tests/test_scheduler_data_loading.py`:
    1. Startup loads all 4 data sources into Scheduler + sets _scheduler_ready.
    2. Config change event (SCHEDULE_CHANGED) triggers reload of intensities + programs + device lookup (NOT mode_params).
    3. MODE_CHANGED event triggers reload of mode_parameters + intensities + programs.
    4. Control loop does NOT tick before _scheduler_ready is set (startup gate works).
    5. _trigger_scheduler_refresh() calls all four update_*() methods (not just update_schedules).
  QA scenarios: happy — after startup, Scheduler has mode_params, intensities, programs, device_lookup cached; after mode transition, all caches refreshed. failure — DB query fails → log ERROR, continue with empty cache, _scheduler_ready still set (to avoid deadlock), alarm fires from T9, failsafe kicks in (10% + relay ON). Evidence `.omo/evidence/task-6-production-safety.txt`
  Commit: Y | feat(control): load mode_parameters + intensities + programs into Scheduler at startup

- [x] 7. Auto-create light_target_intensity row when a light is created
  What to do / Must NOT do:
  - Create shared helper in `Infrastructure/automation-service/app/services/schedule_auto_create.py`:
    ```python
    async def create_default_intensity_for_light(
        device_id: int, location: str, cluster: str, database: DatabaseManager
    ) -> None:
        # Get active mode for the room
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if active_mode:
            mode_id = active_mode["mode_id"]
        else:
            # Fallback: query room_modes by name 'veg' via repository method added in T4
            veg_mode = await database.room_mode_repo.get_mode_by_name("veg")
            mode_id = veg_mode["id"] if veg_mode else None
        if mode_id is None:
            logger.error("No active mode and no 'veg' mode for %s/%s — skipping intensity creation", location, cluster)
            return
        # Create intensity row with 10% default
        await database.light_target_intensity_repo.set_intensity(device_id, mode_id, 10.0)
        logger.info("Created default light_target_intensity for device %d, mode %d: 10%%", device_id, mode_id)
    ```
  - In `create_registry_device()` (devices_crud.py:122-128): after `created = await device_repo.create_light(...)`, call `create_default_intensity_for_light(created.device_id, created.location, created.cluster, database)`. Add `database: DatabaseManager = Depends(get_database)` to route signature. After creating intensity rows, publish a `SCHEDULE_CHANGED` event so `background_tasks` (T6) reloads `_device_lookup` + `_light_intensities` + `_light_programs` in the Scheduler — without this, the Scheduler caches are stale (the 10% hardcoded default masks it, but if the operator changes intensity before the cache reloads, the old default would be used).
  - In `create_light()` (lights.py:847-853): same call after `light = await device_repo.create_light(...)`. Add `database: DatabaseManager = Depends(get_database)` to route signature. Also publish `SCHEDULE_CHANGED` after creating intensity rows.
  - Also create intensity rows for ALL modes that exist for the room (not just active mode) — query `mode_parameters` for all modes for this location/cluster and create rows for each with 10% default.
  - **Must NOT** fail light creation if intensity creation fails — log ERROR, continue.
  - **Must NOT** create intensity rows for non-light devices.
  - **Must NOT** create schedule rows (SUN/MOON) — that's the old architecture.
  Parallelization: Wave 4 | Blocked by: 5 | Blocks: 11 | Can parallelize with: 6, 8, 9
  References:
  - `Infrastructure/automation-service/app/routes/devices_crud.py:62-128` (create_registry_device)
  - `Infrastructure/automation-service/app/routes/lights.py:818-854` (create_light)
  - `Infrastructure/automation-service/app/repositories/room_modes.py` (get_active_mode — existing; get_mode_by_name — added in T4)
  - `Infrastructure/automation-service/app/repositories/light_target_intensity.py` (set_intensity — created in T4)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/routes/devices_crud.py app/routes/lights.py app/services/schedule_auto_create.py` passes.
  - New pytest `tests/test_light_auto_intensity.py`:
    1. POST /api/devices/registry creates light_target_intensity row for active mode.
    2. POST /api/lights creates light_target_intensity row.
    3. Creates rows for ALL modes for the room, not just active.
    4. Default intensity is 10.0.
    5. Light creation succeeds even if intensity creation fails.
  QA scenarios: happy — new light gets 10% intensity for all modes immediately. failure — intensity repo fails → light still created, ERROR logged. Evidence `.omo/evidence/task-7-production-safety.txt`
  Commit: Y | feat(lights): auto-create light_target_intensity row when a light is created

- [ ] 8. Simplify save_room_schedule + add intensity update endpoint
  What to do / Must NOT do:
  - **save_room_schedule() in room.py:** Remove per-device SUN/MOON row creation for lights (lines 404-450). Remove room_schedule row creation (lines 362-402). Keep DAY/NIGHT row creation for non-light devices (lines 452-490). Instead of creating schedule rows, update mode_parameters:
    ```python
    # Update mode_parameters day_start_time and night_start_time
    await conn.execute(
        "UPDATE mode_parameters SET day_start_time = $1, night_start_time = $2, "
        "light_ramp_up_minutes = $3, light_ramp_down_minutes = $4, updated_at = NOW() "
        "WHERE location = $5 AND cluster = $6",
        day_start_time, night_start_time, ramp_up, ramp_down, location, cluster
    )
    ```
    Then publish a `MODE_CHANGED` event (already exists in `ConfigEventType`) so `background_tasks` reloads mode_params + intensities into Scheduler (T6 handles `MODE_CHANGED` specifically — do NOT reuse `SCHEDULE_CHANGED` which only reloads intensities/programs/devices).
  - **Rewrite existing `POST /api/lights/{location}/{cluster}/{device_name}/target` endpoint** (lights.py:730-774): The current `set_target_intensity()` calls `schedule_repo.update_light_schedule_target()` which updates the SUN row's `target_intensity` in the `schedules` table. After T10 deletes SUN/MOON rows, this endpoint returns 404. Rewrite it to write to `light_target_intensity` instead:
    ```python
    @router.post("/api/lights/{location}/{cluster}/{device_name}/target")
    async def set_target_intensity(
        location: str, cluster: str, device_name: str,
        control: TargetIntensityControl,
        config=Depends(get_config),
        database=Depends(get_database),
        scheduler=Depends(get_scheduler),
    ) -> dict[str, Any]:
        if control.target_intensity < 0 or control.target_intensity > 100:
            raise HTTPException(status_code=400, detail="Target intensity must be between 0 and 100")
        devices = await config.get_devices()
        device_info = devices.get(location, {}).get(cluster, {}).get(device_name)
        if not device_info:
            raise HTTPException(status_code=404, detail=f"Device not found: {location}/{cluster}/{device_name}")
        if device_info.get("device_type") != "light":
            raise HTTPException(status_code=400, detail=f"Device {device_name} is not a light")
        # Look up device_id from device_registry
        device_id = await database.device_repo.get_device_id(location, cluster, device_name)
        if device_id is None:
            raise HTTPException(status_code=404, detail=f"No device_id for {device_name}")
        # Get active mode
        active_mode = await database.room_mode_repo.get_active_mode(location, cluster)
        if active_mode:
            mode_id = active_mode["mode_id"]
        else:
            veg_mode = await database.room_mode_repo.get_mode_by_name("veg")
            if veg_mode is None:
                raise HTTPException(status_code=400, f"No active mode and no 'veg' mode for {location}/{cluster}")
            mode_id = veg_mode["id"]
        # Write to light_target_intensity (NOT schedules table)
        await database.light_target_intensity_repo.set_intensity(device_id, mode_id, control.target_intensity)
        # SYNCHRONOUS Scheduler cache update — do NOT rely solely on the async SCHEDULE_CHANGED event
        # for the in-process Scheduler. The current code (lights.py:762-764) updates the Scheduler
        # synchronously so the next control loop tick sees the new target immediately. Preserve this:
        if scheduler:
            fresh_intensities = await database.light_target_intensity_repo.get_all_intensities()
            scheduler.update_light_intensities(fresh_intensities)
        # Also publish SCHEDULE_CHANGED for other consumers (Redis state, frontend refresh)
        ...publish SCHEDULE_CHANGED...
        return { "success": True, "location": location, "cluster": cluster, "device": device_name,
                 "target_intensity": control.target_intensity, "device_id": device_id, "mode_id": mode_id }
    ```
    The frontend keeps calling the same URL — only the backend changes underneath.
  - **Add new `PUT /api/lights/{device_id}/intensity` endpoint** (lights.py) for device-id-based access (used by future API consumers and direct device management):
    ```python
    @router.put("/api/lights/{device_id}/intensity")
    async def update_light_intensity(
        device_id: int,
        body: dict[str, Any],
        device_repo: DeviceRepository = Depends(get_device_repo),
        database: DatabaseManager = Depends(get_database),
    ) -> dict[str, Any]:
        light = await device_repo.get_light_by_id(device_id)
        if light is None:
            raise HTTPException(404, f"Light {device_id} not found")
        target_intensity = float(body.get("target_intensity", 10.0))
        active_mode = await database.room_mode_repo.get_active_mode(light.location, light.cluster)
        if active_mode:
            mode_id = active_mode["mode_id"]
        else:
            # Fallback: query room_modes by name 'veg' via repository method added in T4
            veg_mode = await database.room_mode_repo.get_mode_by_name("veg")
            if veg_mode is None:
                raise HTTPException(400, f"No active mode and no 'veg' mode for {light.location}/{light.cluster}")
            mode_id = veg_mode["id"]
        await database.light_target_intensity_repo.set_intensity(device_id, mode_id, target_intensity)
        # SYNCHRONOUS Scheduler cache update (same pattern as POST /target above)
        from app.main import container
        scheduler = container.get_control_engine().scheduler
        fresh_intensities = await database.light_target_intensity_repo.get_all_intensities()
        scheduler.update_light_intensities(fresh_intensities)
        # Also publish SCHEDULE_CHANGED for other consumers
        ...publish SCHEDULE_CHANGED...
        return {"success": True, "device_id": device_id, "mode_id": mode_id, "target_intensity": target_intensity}
    ```
  - **Update frontend api.ts:** Add `updateLightIntensity(device_id, target_intensity)` method that calls the new `PUT /api/lights/{device_id}/intensity` endpoint. The existing `setLightTarget()` method keeps using `POST /target` (now backed by `light_target_intensity`). Both work.
  - **Update `sync_room_schedule_from_mode_parameters()`** (room.py:581): This function is called during mode transitions (by `ModeTransitionService`). Simplify it the same way as `save_room_schedule` — only update mode_parameters + create DAY/NIGHT rows for non-light devices. Remove any per-device SUN/MOON row creation. This function is in the same file, immediately after `save_room_schedule`.
  - **Deprecate `main_light_intensity`/`supplemental_light_intensity` fields in mode_parameters:** Update `update_room_parameters()` (room_modes.py:298-361) to stop writing these fields as active config — add a deprecation log warning when they're set via the API. Document in AGENTS.md (T10) that `light_target_intensity` supersedes them. Do NOT remove the columns (avoid migration risk).
  - **Must NOT** delete existing per-device SUN/MOON rows in this todo — that cleanup happens in T10.
  - **Must NOT** change how save_room_schedule creates DAY/NIGHT rows for non-lights.
  - **Must NOT** remove the `room_schedule` row lookup logic in `get_room_schedule()` endpoint — it reads old data for backward compatibility until cleanup.
  Parallelization: Wave 4 | Blocked by: 5 | Blocks: 11 | Can parallelize with: 6, 7, 9
  References:
  - `Infrastructure/automation-service/app/routes/schedules/room.py:228-577` (save_room_schedule — simplify)
  - `Infrastructure/automation-service/app/routes/schedules/room.py:581+` (sync_room_schedule_from_mode_parameters — simplify same way)
  - `Infrastructure/automation-service/app/routes/schedules/room.py:38-80` (sync_all_room_schedules — update to use mode_parameters only)
  - `Infrastructure/automation-service/app/routes/schedules/room.py:83-225` (get_room_schedule — backward compat, cleaned up in T10)
  - `Infrastructure/automation-service/app/routes/room_modes.py:298-361` (update_room_parameters — deprecate main_light_intensity fields)
  - `Infrastructure/automation-service/app/routes/lights.py:730-774` (set_target_intensity — REWRITE to use light_target_intensity instead of schedules)
  - `Infrastructure/automation-service/app/routes/lights.py` (add new PUT /api/lights/{device_id}/intensity endpoint)
  - `Infrastructure/frontend/src/services/api.ts:522-530` (setLightTarget — keeps same URL, backend changes underneath)
  - `Infrastructure/frontend/src/services/api.ts:228-240` (updateLight — extend or add new method for device-id-based intensity)
  - `Infrastructure/frontend/src/services/api.ts:167-170` (deleteDevice — add X-Confirm-Destructive header, from T2)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check app/routes/schedules/room.py app/routes/lights.py` passes.
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes.
  - `grep -n "create_schedule.*Sun\|create_schedule.*Moon" Infrastructure/automation-service/app/routes/schedules/room.py` returns nothing in the light section (DAY/NIGHT for non-lights can still use create_schedule).
  - `grep -n "update_light_intensity\|/intensity" Infrastructure/automation-service/app/routes/lights.py` returns a match.
  - New pytest `tests/test_save_room_schedule_simplified.py`: save_room_schedule updates mode_parameters, does NOT create per-device SUN/MOON rows for lights, still creates DAY/NIGHT for non-lights.
  - New pytest `tests/test_update_light_intensity.py`: PUT intensity updates light_target_intensity table.
  QA scenarios: happy — saving room schedule updates mode_parameters; lights inherit automatically. Changing light intensity calls new endpoint. failure — old per-device rows still exist (cleanup in T10). Evidence `.omo/evidence/task-8-production-safety.txt`
  Commit: Y | feat(schedules): simplify save_room_schedule + add light intensity endpoint

- [x] 9. Remove expand_light_schedules_for_control + integrate AlarmManager for missing schedule/mode_parameters
  What to do / Must NOT do:
  - **Remove from schedule_merge.py:**
    - `expand_light_schedules_for_control()` (lines 229-358) — DELETE entirely
    - `has_sun_or_day_row()` (lines 261-271) — DELETE (only used by synthesis)
    - `fallback_target_for_room()` (lines 273-283) — DELETE (only used by synthesis)
    - `_moon_window_from_sun_times()` (lines 65-70) — DELETE (only used by synthesis)
    - `_format_schedule_time()` (lines 59-62) — DELETE (only used by synthesis)
    - `_parse_schedule_time_value()` (lines 42-56) — CHECK if validation still uses it; if not, DELETE
    - `_time_to_minutes()` (lines 141-142) — CHECK if validation still uses it; if not, DELETE
    - `_mark_minutes()` (lines 145-153) — CHECK if validation still uses it; if not, DELETE
    - `_daily_schedule_has_gaps()` (lines 156-186) — CHECK if validation still uses it; if not, DELETE
  - **Update `merge_schedules_with_config()`:** Remove the call to `expand_light_schedules_for_control()` at line 36. Keep `validate_dimmable_light_schedule_coverage()` and `validate_light_config_against_schedules()` calls — but update them to check for light_target_intensity rows instead of per-device SUN/MOON rows.
  - **Update validation functions:**
    - `validate_dimmable_light_schedule_coverage()`: Instead of checking self.schedules for per-device SUN/MOON rows, check if the light has a light_target_intensity row for the active mode. If not, log WARNING (the light will get 10% default). Still log ERROR for completely missing coverage.
    - `validate_light_config_against_schedules()`: Remove the "no schedule rows in DB" check for lights (replaced by intensity row check). Keep it for non-light devices.
  - **AlarmManager integration:**
    - **IMPORTANT side effect:** `raise_alarm()` with `severity="critical"` calls `_trigger_failsafe()` which sets `mode="failsafe"` in Redis. This is EXPECTED behavior — the failsafe mode IS the relay ON + 10% design. When the CRITICAL alarm for missing mode_parameters fires, the room's mode changes to "failsafe" in Redis. This is the intended escalation path. No code change needed — just be aware this happens.
    - Pass `alarm_manager: AlarmManager | None = None` to `merge_schedules_with_config()`.
    - CRITICAL alarm: "room_missing_mode_parameters" — **Trigger point:** in `merge_schedules_with_config()`, after loading mode_parameters for each room, check if the active mode has a mode_parameters row. Query `room_mode_repo.get_active_mode(location, cluster)` to get the active mode_id, then check if mode_parameters has a row for `(location, cluster, active_mode_id)`. If not, `alarm_manager.raise_alarm(location, cluster, "room_missing_mode_parameters", "critical", ...)`. ALSO: in T6's startup data loading, if the mode_parameters query returns no rows for a room, raise this alarm directly (the validation in merge_schedules_with_config may not run if the DB is down).
    - WARNING alarm: "light_missing_intensity" — when a light has no light_target_intensity row. `alarm_manager.raise_alarm(location, cluster, "light_missing_intensity", "warning", ...)`.
    - Clear alarm when coverage is restored: `alarm_manager.clear_alarm(location, cluster, alarm_name)`.
  - **Update callers of merge_schedules_with_config:**
    - `background_tasks.py:13` — pass alarm_manager
    - `container.py:14` — pass alarm_manager
    - `routes/schedules/base.py:14` — pass alarm_manager
    - `routes/lights.py:13` — pass alarm_manager
    - `services/mode_transition_service.py:251` — pass alarm_manager
  - **Must NOT** remove the validation functions themselves — they become the alarm detection layer.
  - **Must NOT** break non-light device validation — DAY/NIGHT rows are still checked.
  - **Must NOT** remove `has_moon_or_night_row()` if it's used by validation functions — check first.
  Parallelization: Wave 4 | Blocked by: 5 | Blocks: 10 | Can parallelize with: 6, 7, 8
  References:
  - `Infrastructure/automation-service/app/control/schedule_merge.py:229-358` (expand_light_schedules_for_control — REMOVE)
  - `Infrastructure/automation-service/app/control/schedule_merge.py:24-39` (merge_schedules_with_config — remove expand call, add alarm_manager param)
  - `Infrastructure/automation-service/app/control/schedule_merge.py:90-138` (validate_dimmable_light_schedule_coverage — update to check intensity rows)
  - `Infrastructure/automation-service/app/control/schedule_merge.py:189-226` (validate_light_config_against_schedules — update for lights)
  - `Infrastructure/automation-service/app/background_tasks.py:13` (update import + call)
  - `Infrastructure/automation-service/app/container.py:14` (update import + call)
  - `Infrastructure/automation-service/app/routes/schedules/base.py:14` (update import + call)
  - `Infrastructure/automation-service/app/routes/lights.py:13` (update import + call)
  - `Infrastructure/automation-service/app/services/mode_transition_service.py:251` (update import + call)
  - `Infrastructure/automation-service/app/alarm_manager.py:30-69` (raise_alarm)
  - `Infrastructure/automation-service/app/alarm_manager.py:71-89` (clear_alarm)
  Acceptance criteria:
  - `grep -n "expand_light_schedules_for_control" Infrastructure/automation-service/app/` returns nothing (function + all callers removed).
  - `cd Infrastructure/automation-service && ruff check app/control/schedule_merge.py app/background_tasks.py app/container.py app/routes/schedules/base.py app/routes/lights.py app/services/mode_transition_service.py` passes.
  - New pytest `tests/test_schedule_alarms.py`:
    1. CRITICAL alarm when room has no mode_parameters for active mode.
    2. WARNING alarm when light has no light_target_intensity row.
    3. No alarm when everything is configured.
    4. Alarm cleared when coverage restored.
    5. AlarmManager is None — doesn't crash, still logs ERROR.
  - Existing tests updated: any test that imported `expand_light_schedules_for_control` or depended on synthesis is updated.
  QA scenarios: happy — validation detects missing config and raises alarms. failure — AlarmManager None → doesn't crash. Evidence `.omo/evidence/task-9-production-safety.txt`
  Commit: Y | fix(control): remove runtime synthesis, integrate AlarmManager for missing schedule detection

- [ ] 10. Remove old code references + documentation cleanup (AGENTS.md, ARCHITECTURE.md, SCHEMATIC, old tests)
  What to do / Must NOT do:
  - **Remove old per-device SUN/MOON rows for lights from the schedules table:**
    ```sql
    -- Pre-flight check: verify EVERY light with a SUN row has at least one light_target_intensity row.
    -- If this query returns ANY rows, ABORT the delete — migration was incomplete.
    SELECT d.device_name FROM device_registry d
      JOIN schedules s ON s.device_name = d.device_name
      WHERE d.device_type = 'light' AND s.mode = 'SUN'
      AND d.device_id NOT IN (SELECT device_id FROM light_target_intensity);
    -- Only if the above returns ZERO rows, proceed with the delete:
    DELETE FROM schedules s USING device_registry d
      WHERE s.device_name = d.device_name
      AND d.device_type = 'light'
      AND s.mode IN ('SUN', 'MOON');
    ```
    (Run as a migration or as part of the alembic migration. Use `device_type = 'light'` from device_registry, NOT `device_name LIKE 'light_%'` — naming conventions are not guaranteed. Non-light DAY/NIGHT rows are NOT deleted.)
  - **Remove room_schedule rows:**
    ```sql
    DELETE FROM schedules WHERE device_name = 'room_schedule';
    ```
    (Photoperiod now comes from mode_parameters.)
  - **Update `delete_registry_device()` cascade:** Remove the `delete_schedules_by_device_name` cascade for light devices — there are no more per-device SUN/MOON rows to delete. Keep the effective_setpoints cascade. For non-light devices, keep the schedule cascade (DAY/NIGHT rows still exist). Add `delete from light_target_intensity` and `delete from light_programs` cascade instead (ON DELETE CASCADE handles this at the DB level, so no code change needed).
  - **Update `get_zone_lights_status()` (lights.py:446):** This endpoint reads `sun_day_target` from `schedules_list` (lines 573-594) by iterating SUN/DAY rows. After T10 deletes old per-device SUN/MOON rows, this lookup returns None, and the ZoneConfig slider falls back to showing current hardware intensity instead of configured target. Update lines 573-594 to read target from `light_target_intensity` table instead: query `database.light_target_intensity_repo.get_intensity(device_id, active_mode_id)` for each light. Call `scheduler.is_in_photoperiod()` for the active_mode_id (or use the cached mode_id from `_mode_params`). The `sun_day_target` should come from the intensity repo, not from schedules rows.
  - **Clean up `get_room_schedule()` endpoint (room.py:83-225):** Simplify — read from mode_parameters directly instead of inferring from schedules. Return day_start_time/night_start_time from mode_parameters for the active mode.
  - **Update AGENTS.md:**
    - Add "Schedule Architecture" section documenting the 3-concept design (photoperiod, intensity, programs).
    - Document that photoperiods are **overnight-capable**: `day_start_time` can be > `night_start_time` (e.g., veg mode day_start=16:00, night_start=10:00 → 18h overnight photoperiod from 16:00 to 10:00 next day). `is_in_photoperiod()` handles overnight wrap: `current_time >= day_start OR current_time < night_start`.
    - Remove references to "per-device SUN/MOON rows" and "room_schedule" as concepts.
    - Remove references to `expand_light_schedules_for_control`.
    - Document `light_target_intensity` table as the intensity source.
    - Document `light_programs` table for supplemental/override programs.
    - Document the 10% hardcoded default for missing intensity rows.
    - Document `X-Confirm-Destructive` header guard.
    - Document the startup self-heal (data loading in background_tasks).
    - Document that `mode_parameters.main_light_intensity`/`supplemental_light_intensity` are DEPRECATED — superseded by `light_target_intensity`. Do NOT remove the columns but stop reading them in the Scheduler.
  - **Update ARCHITECTURE.md:**
    - Update data flow diagram: photoperiod from mode_parameters → Scheduler.is_in_photoperiod → is_sun → get_schedule_intensity (from light_target_intensity) → light_programs check → DFR0971 output.
    - Add schedule lifecycle section: creation → startup data load → validation + alarm.
    - Document new tables: light_target_intensity, light_programs.
    - Document that photoperiods are **overnight-capable**: `day_start_time` can be > `night_start_time` (e.g., veg mode: day_start=16:00, night_start=10:00 → 18h overnight photoperiod 16:00→10:00). The Scheduler handles overnight wrap in both `is_in_photoperiod()` and `light_programs` time window matching.
    - Document that `mode_parameters.main_light_intensity`/`supplemental_light_intensity` are DEPRECATED — superseded by `light_target_intensity`. Do NOT remove the columns but stop reading them in the Scheduler.
    - Remove references to per-device SUN/MOON rows and runtime synthesis.
  - **Update ARCHITECTURE_SCHEMATIC.md:**
    - Add Mermaid diagram: schedule flow (mode_parameters → is_in_photoperiod → light_target_intensity → light_programs → output).
    - Add table: schedule-related tables and relationships.
    - Update control loop diagram: show data loading at startup.
    - Document overnight photoperiod handling: `day_start_time > night_start_time` means overnight window (e.g., 16:00→10:00). Both `is_in_photoperiod()` and `light_programs` time-window matching handle overnight wrap.
    - Remove references to expand_light_schedules_for_control.
  - **Update old tests:** Any test file that imports or references removed functions is updated or deleted.
  - **Must NOT** delete DAY/NIGHT rows from the schedules table — those control non-light device ON/OFF.
  - **Must NOT** remove the `schedules` table itself — it's still used for DAY/NIGHT rows.
  - **Must NOT** document the YAML restore as a mechanism.
  - **Remove dead Redis code — `SchedulesMixin` class** (`redis/schedules.py:22-76`): Both `write_schedule_state()` and `read_schedule_state()` have **zero callers** (verified via codegraph). The entire class is dead code. It wrote THREE duplicate Redis key forms for the same data that no one reads. Remove:
    - `Infrastructure/automation-service/app/redis/schedules.py` — DELETE entire file (SchedulesMixin class)
    - `Infrastructure/automation-service/app/redis/redis_operations.py` — remove `from .schedules import SchedulesMixin`, remove `self.schedules = SchedulesMixin()` and its setup in `__init__`, remove the `write_schedule_state` / `read_schedule_state` delegate methods (lines 400-408)
    - `Infrastructure/automation-service/app/redis/__init__.py` — remove `write_schedule_state` / `read_schedule_state` delegate methods (lines 461-469) and their docstrings
    - `Infrastructure/shared/redis_keys.py` — remove `schedule_state_infix()` (line 210) — only used by the dead SchedulesMixin
  - **Remove obsolete StateManager cache key** — `_cache_key_room_schedule()` in `ScheduleRepository` (`repositories/schedules.py:43`): The `room_schedule` rows it caches are deleted by T10 (the `DELETE FROM schedules WHERE device_name = 'room_schedule'` above). This cache key will always return empty. Remove:
    - The `_cache_key_room_schedule()` method (schedules.py:43)
    - All `delete` calls referencing it: `schedules.py:547` (`await s.delete(self._cache_key_room_schedule(location, cluster))`) and `schedules.py:549` area
    - Keep `_cache_key_schedules()` (still used for non-light DAY/NIGHT rows) and `_cache_key_light_schedule()` (method stays but won't be populated for lights anymore — no code change needed, cache just stays empty)
  - **Delete stale Redis keys during deploy** (one-time cleanup, run after `deploy.sh` succeeds):
    ```bash
    # Remove the three dead schedule-state key forms for all rooms
    redis-cli --scan --pattern "schedule:state:*" | xargs -r redis-cli DEL
    redis-cli --scan --pattern "cea:schedule:*:state" | xargs -r redis-cli DEL
    redis-cli --scan --pattern "cea:schedule:state:*" | xargs -r redis-cli DEL
    ```
    These keys were written by the dead `SchedulesMixin.write_schedule_state()` and are now orphaned. The StateManager in-memory caches are cleared on service restart (part of deploy), so no explicit cleanup needed for those.
  Parallelization: Wave 5 | Blocked by: 9 | Blocks: 11 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/app/routes/devices_crud.py:332-335` (delete_schedules_by_device_name cascade — remove for lights)
  - `Infrastructure/automation-service/app/routes/schedules/room.py:83-225` (get_room_schedule — simplify)
  - `Infrastructure/automation-service/app/routes/lights.py:446-612` (get_zone_lights_status — update sun_day_target lookup to use light_target_intensity)
  - `Infrastructure/automation-service/app/redis/schedules.py` (SchedulesMixin — DELETE entire file, zero callers)
  - `Infrastructure/automation-service/app/redis/redis_operations.py:400-408` (write/read_schedule_state delegates — remove)
  - `Infrastructure/automation-service/app/redis/__init__.py:461-469` (write/read_schedule_state delegates — remove)
  - `Infrastructure/shared/redis_keys.py:210` (schedule_state_infix — remove, only used by dead SchedulesMixin)
  - `Infrastructure/automation-service/app/repositories/schedules.py:43` (_cache_key_room_schedule — remove method + call sites at lines 547-549)
  - `AGENTS.md` (update schedule architecture section)
  - `ARCHITECTURE.md` (update data flow + schedule lifecycle)
  - `ARCHITECTURE_SCHEMATIC.md` (update diagrams)
  - All test files in `Infrastructure/automation-service/tests/` that reference removed functions
  Acceptance criteria:
  - `grep -rn "expand_light_schedules_for_control" Infrastructure/` returns nothing (function + all callers removed).
  - `grep -rn "room_schedule" Infrastructure/automation-service/app/routes/schedules/room.py` only in get_room_schedule (which now reads from mode_parameters).
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT count(*) FROM schedules s JOIN device_registry d ON d.device_name = s.device_name WHERE d.device_type = 'light' AND s.mode IN ('SUN','MOON');"` returns 0.
  - `sudo -u postgres psql -d cea_sensors_test -c "SELECT count(*) FROM schedules WHERE device_name = 'room_schedule';"` returns 0.
  - `grep -n "Schedule Architecture\|light_target_intensity\|light_programs\|10%.*default\|DEPRECATED.*main_light_intensity" AGENTS.md` returns matches.
  - `grep -n "light_target_intensity\|light_programs" ARCHITECTURE.md` returns matches.
  - `grep -n "DEPRECATED\|deprecated.*main_light_intensity" AGENTS.md` returns a match (deprecation documented).
  - `grep -rn "SchedulesMixin" Infrastructure/automation-service/app/redis/` returns nothing (dead code removed).
  - `grep -rn "schedule_state_infix" Infrastructure/` returns nothing (dead code removed).
  - `grep -rn "_cache_key_room_schedule" Infrastructure/automation-service/app/repositories/schedules.py` returns nothing (obsolete cache key removed).
  - `redis-cli --scan --pattern "schedule:state:*" | wc -l` returns 0 (stale Redis keys deleted during deploy).
  - `redis-cli --scan --pattern "cea:schedule:*:state" | wc -l` returns 0 (stale Redis keys deleted during deploy).
  - `cd Infrastructure/automation-service && pytest tests/ -q` passes with no references to removed functions.
  QA scenarios: happy — all old references removed, documentation updated, no broken imports, dead Redis code gone, stale keys purged. failure — grep finds stale references, tests fail on missing imports. Evidence `.omo/evidence/task-10-production-safety.txt`
  Commit: Y | refactor: remove old schedule references, clean up documentation and tests

- [ ] 11. Final: deploy all changes + verify schedule architecture, DELETE guard, alarms, frontend, dfr-panel-cleanup
  What to do / Must NOT do:
  - Run `./deploy.sh` from project root.
  - After deploy, verify:
    1. **Schedule architecture:** `sudo -u postgres psql -d cea_sensors_test -c "SELECT * FROM light_target_intensity;"` returns rows. `journalctl -u automation-service | grep "mode_params\|intensit\|program"` shows data loaded at startup.
    2. **Light intensity:** Verify DFR0 CH0 (Eyefinity) at correct intensity during sun period (should be 30% from light_target_intensity, not 0%) by checking `curl -s http://127.0.0.1:8001/api/devices/registry | python3 -c "import sys,json; ..."` or Redis state — NOT manual physical inspection.
    3. **DELETE guard:** `curl -X DELETE http://127.0.0.1:8001/api/devices/registry/999 -H "X-API-Key: $API_KEY"` returns 403 (per T3 exception: subagent may test guard with non-existent ID 999). With `X-Confirm-Destructive: true` returns 404 (device doesn't exist, guard passed).
    4. **Frontend dfr-panel-cleanup:** `grep -n "applyAssignment" /opt/projectcea/current/Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` returns nothing.
    5. **Alarms:** When a light_target_intensity row is deleted, a WARNING alarm fires in `/api/alarms`.
    6. **All tests pass:** `cd Infrastructure/automation-service && ruff check . && pytest tests/ -q` and `cd Infrastructure/frontend && npx tsc --noEmit && npm run build && npx vitest run`.
  - **Must NOT** run F3 (Real Manual QA) — use static checks only per T3. The DELETE guard verification in step 3 is the explicit exception carved out in T3 (non-existent ID 999 only).
  - **Must NOT** skip the deploy health checks.
  Parallelization: Wave 6 | Blocked by: 1-10 | Blocks: —
  References:
  - `deploy.sh` (project root)
  - All changed files from T1-T10
  Acceptance criteria: deploy.sh exits 0, health checks pass, all verification points green.
  QA scenarios: happy — all checks green. failure — deploy health fail → auto-rollback. Evidence `.omo/evidence/task-11-production-safety.txt`
  Commit: N | (verification + deploy only; code commits in T1-T10)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
- [ ] F2. Code quality review
- [~] F3. Static verification only (no production HTTP) — permanently banned per T3
- [ ] F4. Scope fidelity

## Commit strategy
- Wave 1: T1 `feat(db): add light_target_intensity + light_programs tables, migrate existing intensities`; T2 `feat(safety): require X-Confirm-Destructive header for device registry DELETE in production`; T3 `docs(agents): ban F3 from production HTTP, define static-checks-only QA protocol`
- Wave 2: T4 `feat(repos): add light_target_intensity + light_programs repositories`
- Wave 3: T5 `feat(scheduler): rewrite is_in_photoperiod + get_schedule_intensity for new architecture`
- Wave 4: T6 `feat(control): load mode_parameters + intensities + programs into Scheduler at startup`; T7 `feat(lights): auto-create light_target_intensity row when a light is created`; T8 `feat(schedules): simplify save_room_schedule + add light intensity endpoint`; T9 `fix(control): remove runtime synthesis, integrate AlarmManager for missing schedule detection`
- Wave 5: T10 `refactor: remove old schedule references, clean up documentation and tests`
- Wave 6: T11 (no commit — verify + deploy)

## Success criteria
- Every light's photoperiod comes from `mode_parameters` (room-level, single source of truth) — no per-device SUN/MOON rows needed.
- Every light's intensity comes from `light_target_intensity` table (mode-specific, per-device). Default 10% if no row exists. `mode_parameters.main_light_intensity`/`supplemental_light_intensity` are DEPRECATED.
- Light programs support both time-slot mode (start_time + end_time, including overnight wrap) and cycle mode (on/off duration within window).
- Override programs replace normal intensity during sun period. Supplemental programs add light during dark period.
- `expand_light_schedules_for_control()` is completely removed — no runtime synthesis.
- Mode transitions refresh ALL Scheduler caches (mode_params + intensities + programs), not just `self.schedules`.
- Startup gate (`asyncio.Event`) prevents control loop from ticking before data is loaded — no empty-cache race condition.
- If a room has no mode_parameters: failsafe = relay ON + 10% intensity + CRITICAL alarm (NOT darkness). If a light has no intensity row: 10% default + WARNING alarm.
- `DELETE /api/devices/registry/{id}` returns 403 in production without `X-Confirm-Destructive: true` header.
- F3 is permanently replaced with static checks — documented in AGENTS.md. DELETE guard may be tested with non-existent ID (999) only.
- All old code references to removed systems are cleaned up. Documentation reflects the new architecture.
- Dead Redis code removed: `SchedulesMixin` (zero callers), `schedule_state_infix()`, `_cache_key_room_schedule()`. Stale `schedule:state:*`, `cea:schedule:*:state`, `cea:schedule:state:*` keys purged from Redis during deploy.
- dfr-panel-cleanup frontend changes are deployed.
- `ruff check .`, `tsc --noEmit`, `npm run build`, `vitest`, and all new pytest tests pass.
- All acceptance criteria use `cea_sensors_test`, NEVER `cea_sensors` (production).
