# control-page-overhaul - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Your lights will show the correct intensity (not stuck at 10%), the light intensity slider will actually work, the relay matrix will show human-readable names for all devices (not just lights), the OFF/AUTO/ON badges will be 2.5x bigger, you can steal a relay from another device (the old one gets a red outline), the relay matrix updates in real-time when you change things, and the climate timeline will let you drag the moon period edges, right-click to edit times, and show ramps in the right place.

**Why this approach:** The root cause of the 10% intensity bug and the "Light target update failed" error is that two database tables (`light_target_intensity` and `light_programs`) were never actually created — the migration that was supposed to create them got skipped. Rather than fight with alembic again (which has failed repeatedly), we'll just create the tables directly via SQL. The relay matrix display name bug is a one-line fix in the view model. The relay steal requires both a backend change (allow it instead of blocking with 409) and a frontend change (red outline + real-time refresh).

**What it will NOT do:**
- Will NOT use alembic migrations — you explicitly rejected them, we're going direct SQL
- Will NOT touch the LightDevice model or schedules.yaml
- Will NOT add ErrorBoundaries or frontend hardening

**Effort:** Medium
**Risk:** Medium — creates production DB tables directly, changes relay conflict behavior
**Decisions to sanity-check:** Direct SQL instead of alembic (user's explicit choice). Relay steal NULLs the displaced device (user confirmed). Badge size 2.5x = 20px (from 8px).

Your next move: approve, then `$start-work`. Full execution detail follows below.

---

> TL;DR (machine): Medium, Medium risk — create light_target_intensity + light_programs tables via direct SQL (no alembic), seed from mode_parameters; fix relay matrix display_name for all devices; scale badges 2.5x; allow relay steal (NULL displaced device); real-time matrix update; fix ClimatePeriodTimeline moon editing + ramp positioning; one deploy

## Scope
### Must have
- Create `light_target_intensity` and `light_programs` tables directly via SQL (NO alembic)
- Seed `light_target_intensity` with one row per (light device, mode) from `mode_parameters.main_light_intensity`
- Delete alembic test files (`test_alembic_008.py`, `test_device_registry_repository.py` if it has alembic tests)
- Fix `getChannelDisplayName()` in relayViewModel.ts to return `display_name` for ALL devices (not just lights)
- Scale OFF/AUTO/ON badges 2.5x bigger in RelayChannelBox.tsx
- Allow relay steal: change 409 conflict to NULL the displaced device's channel and return success
- Add red outline indicator on displaced devices in DeviceTable
- Real-time relay matrix update: ZoneConfig reloads channels after DeviceTable changes
- Fix ClimatePeriodTimeline moon period: swapped edge handles, right-click time editing, photoperiod lock enforcement, ramp positioning

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Do NOT use alembic migrations — direct SQL only
- Do NOT touch the LightDevice Pydantic model
- Do NOT remove `GET /api/devices/channels` endpoint
- Do NOT change `config.get_devices()` return type
- Do NOT add ErrorBoundaries
- Do NOT touch schedules.yaml or rules.yaml

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + framework: pytest (backend), vitest (frontend)
- Evidence: .omo/evidence/task-<N>-control-page-overhaul.<ext>

## Execution strategy
### Parallel execution waves
- **Wave 1 (DB fix):** T1 (create tables + seed data via SQL, delete migration tests). Single task, blocks everything.
- **Wave 2 (frontend + backend fixes):** T2 (relay matrix display names), T3 (badge sizing), T4 (relay steal backend), T5 (relay steal frontend + red outline + real-time update), T6 (timeline moon/ramp fixes). T2-T6 are independent files, parallelize.
- **Wave 3 (deploy + verify):** T7 (deploy + verify all fixes).

### Dependency matrix
> T2, T3, T6 are pure frontend with no DB dependency — they can start immediately in parallel with T1.
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | — | 7 | 2, 3, 4, 6 |
| 2 | — | 7 | 1, 3, 4, 6 |
| 3 | — | 7 | 1, 2, 4, 6 |
| 4 | — | 5, 7 | 1, 2, 3, 6 |
| 5 | 4 | 7 | 1, 2, 3, 6 |
| 6 | — | 7 | 1, 2, 3, 4, 5 |
| 7 | 1-6 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Create light_target_intensity + light_programs tables via direct SQL + seed data + delete migration tests
  What to do / Must NOT do:
  - **Create `light_target_intensity` table** directly via `sudo -u postgres psql -d cea_sensors`:
    ```sql
    CREATE TABLE IF NOT EXISTS light_target_intensity (
        device_id INTEGER NOT NULL REFERENCES device_registry(device_id) ON DELETE CASCADE,
        mode_id INTEGER NOT NULL REFERENCES room_modes(id) ON DELETE CASCADE,
        target_intensity REAL NOT NULL DEFAULT 10.0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (device_id, mode_id),
        CONSTRAINT ck_light_target_intensity_range CHECK (target_intensity >= 0 AND target_intensity <= 100)
    );
    CREATE INDEX IF NOT EXISTS idx_light_target_intensity_device ON light_target_intensity(device_id);
    CREATE INDEX IF NOT EXISTS idx_light_target_intensity_mode ON light_target_intensity(mode_id);
    ```
  - **Create `light_programs` table** directly via psql:
    ```sql
    CREATE TABLE IF NOT EXISTS light_programs (
        id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        device_id INTEGER REFERENCES device_registry(device_id) ON DELETE CASCADE,
        location TEXT NOT NULL,
        cluster TEXT NOT NULL DEFAULT 'main',
        mode_id INTEGER REFERENCES room_modes(id) ON DELETE SET NULL,
        name TEXT NOT NULL,
        program_type TEXT NOT NULL,
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        cycle_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        cycle_on_seconds INTEGER,
        cycle_off_seconds INTEGER,
        target_intensity REAL NOT NULL,
        ramp_up_minutes INTEGER NOT NULL DEFAULT 0,
        ramp_down_minutes INTEGER NOT NULL DEFAULT 0,
        day_of_week INTEGER,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        priority INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        CONSTRAINT ck_light_programs_type CHECK (program_type IN ('supplemental', 'override')),
        CONSTRAINT ck_light_programs_intensity_range CHECK (target_intensity IS NULL OR (target_intensity >= 0 AND target_intensity <= 100))
    );
    CREATE INDEX IF NOT EXISTS idx_light_programs_lookup ON light_programs(location, cluster, enabled);
    CREATE INDEX IF NOT EXISTS idx_light_programs_device ON light_programs(device_id, enabled);
    ```
  - **Seed `light_target_intensity`** from `mode_parameters.main_light_intensity`:
    ```sql
    INSERT INTO light_target_intensity (device_id, mode_id, target_intensity, created_at, updated_at)
    SELECT DISTINCT ON (d.device_id, mp.mode_id)
        d.device_id,
        mp.mode_id,
        mp.main_light_intensity::REAL,
        NOW(),
        NOW()
    FROM device_registry d
    JOIN mode_parameters mp ON d.location = mp.location AND d.cluster = mp.cluster
    WHERE d.device_type = 'light'
    ORDER BY d.device_id, mp.mode_id
    ON CONFLICT (device_id, mode_id) DO UPDATE SET target_intensity = EXCLUDED.target_intensity, updated_at = NOW();
    ```
  - **Verify the seed worked:** `SELECT * FROM light_target_intensity JOIN device_registry ON ... ORDER BY ...` should return rows for all 6 lights × their modes
  - **Restart automation-service** so the scheduler loads the new tables immediately (fixes the 10% bug right away):
    ```bash
    sudo systemctl restart automation-service && sleep 5
    ```
    Note: `deploy.sh` in T7 will also restart the service — this early restart is intentional to fix the 10% bug before the deploy.
  - **Verify the 10% bug is fixed:** `curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/Veg%20Room/main/zone-status` should show `target_intensity` and `scheduler_effective_intensity` matching `mode_parameters.main_light_intensity` (not 10.0)
  - **Delete alembic test file:**
    - `Infrastructure/automation-service/tests/test_alembic_008.py` — DELETE entirely (tests alembic CLI migration, which is broken)
    - Do NOT delete `test_device_registry_repository.py` — it tests DeviceRepository CRUD, not alembic
  - **Must NOT** run `alembic upgrade` — direct SQL only
  - **Must NOT** touch the `alembic_version` table (already at 010)
  - **Must NOT** delete `test_device_registry_repository.py` (only the alembic parts, or leave if non-alembic tests exist)
  Parallelization: Wave 1 | Blocked by: — | Blocks: 2, 3, 4, 5, 6, 7 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py:29-63` (table DDL for light_target_intensity)
  - `Infrastructure/automation-service/alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py:68-121` (table DDL for light_programs)
  - `Infrastructure/automation-service/alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py:128-146` (seed INSERT)
  - `Infrastructure/automation-service/tests/test_alembic_008.py` (DELETE)
  - `Infrastructure/automation-service/app/control/scheduler.py:454` (where MINIMUM_LIGHT_INTENSITY fallback happens)
  Acceptance criteria:
  - `sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM light_target_intensity;"` returns > 0
  - `sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM light_programs;"` returns 0 (empty, created)
  - `curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/Veg%20Room/main/zone-status | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['lights'][0]['target_intensity'])"` returns the mode_parameters.main_light_intensity value (NOT 10.0)
  - `journalctl -u automation-service --since "1 minute ago" | grep "light_target_intensity.*does not exist"` returns empty
  QA scenarios: happy — tables created, seed data loaded, lights show correct intensity. failure — table already exists (IF NOT EXISTS handles it). Evidence `.omo/evidence/task-1-control-page-overhaul.txt`
  Commit: Y | fix(db): create light_target_intensity + light_programs tables directly via SQL, seed from mode_parameters

- [x] 2. Fix relay matrix to use display_name for ALL devices (not just lights)
  What to do / Must NOT do:
  - **In `Infrastructure/frontend/src/components/devices/relayViewModel.ts:114-124`** (`getChannelDisplayName`):
    - Change the function so it returns `display_name` for ALL devices when available, falling back to `device_name`:
    ```typescript
    export function getChannelDisplayName(channel: ChannelInfo): string | null {
      if (!channel.device_name) {
        return null
      }
      // Prefer display_name (human-readable) for all devices, not just lights
      if (channel.display_name) {
        return channel.display_name
      }
      // Lights: fall back to light_name then device_name
      if (channel.device_type === 'light') {
        return channel.light_name || channel.device_name
      }
      return channel.device_name
    }
    ```
  - **Must NOT** change `assignedDeviceName` in `buildRelayChannelViewModels` (line 199) — that uses `channel.device_name` (canonical) because it's used for API calls, not display
  - **Must NOT** change `getChannelDisplayType` or `getReadableDeviceType`
  - Run `cd Infrastructure/frontend && npx tsc --noEmit` to verify no type errors
  - Run `cd Infrastructure/frontend && npx vitest run` to verify existing tests pass
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 7 | Can parallelize with: 3, 4, 5, 6
  References:
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:114-124` (getChannelDisplayName — the bug)
  - `Infrastructure/frontend/src/components/devices/relayViewModel.ts:177-209` (buildRelayChannelViewModels — deviceName field uses getChannelDisplayName)
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:84` (uses channel.deviceName for display)
  - `Infrastructure/frontend/src/types/relay.ts:13-21` (ChannelInfo has display_name field)
  - `Infrastructure/automation-service/app/routes/devices.py:530` (get_all_channels returns display_name)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes
  - `cd Infrastructure/frontend && npx vitest run` passes
  - Manual: relay matrix shows "Heater Flower" not "heater_f_1" for non-light devices
  QA scenarios: happy — relay matrix shows human-readable names for all devices. failure — display_name is null, falls back to device_name. Evidence `.omo/evidence/task-2-control-page-overhaul.txt`
  Commit: Y | fix(frontend): relay matrix uses display_name for all devices, not just lights

- [x] 3. Scale OFF/AUTO/ON badges 2.5x bigger in RelayChannelBox
  What to do / Must NOT do:
  - **In `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:164`**:
    - Change the badge button from `text-[8px]` to `text-[20px]` (2.5x of 8px = 20px)
    - Also increase padding from `px-1 py-px` to `px-2 py-1`
    - Adjust the badge container if needed to prevent overflow
  - **Must NOT** change the LED dot size (line 142 `h-2 w-2`) — only the badge
  - **Must NOT** change other text sizes in the component
  - Run `cd Infrastructure/frontend && npx tsc --noEmit && npm run build`
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 7 | Can parallelize with: 2, 4, 5, 6
  References:
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:164` (badge button className with text-[8px])
  - `Infrastructure/frontend/src/components/devices/RelayChannelBox.tsx:22-67` (resolveBadgeState returns outlineClass, ledClass)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes
  - `cd Infrastructure/frontend && npm run build` passes
  QA scenarios: happy — badges are visibly 2.5x bigger. Evidence `.omo/evidence/task-3-control-page-overhaul.txt`
  Commit: Y | style(frontend): scale relay matrix badges 2.5x bigger (8px → 20px)

- [x] 4. Backend: allow relay steal (NULL displaced device's channel instead of 409)
  What to do / Must NOT do:
  - **In `Infrastructure/automation-service/app/routes/devices_crud.py:289-305`** (non-light relay conflict):
    - Replace the 409 raise with: find the device that currently has that channel, NULL its channel using the EXISTING `clear_relay_binding_only()` method (already at `devices.py:699-710`), then proceed with the update
    ```python
    # Instead of raising 409, steal the relay
    if device_update.channel is not None:
        hierarchy = await device_repo.get_all_as_hierarchy()
        for loc, clusters in hierarchy.items():
            for clu, devices in clusters.items():
                for dev_name, dev_info in devices.items():
                    if (
                        dev_info.get("channel") == device_update.channel
                        and dev_info.get("device_id") != device_id
                    ):
                        displaced_id = dev_info.get("device_id")
                        if displaced_id is not None:
                            ok = await device_repo.clear_relay_binding_only(displaced_id)
                            if not ok:
                                raise HTTPException(status_code=500, detail=f"Failed to clear relay binding for displaced device {displaced_id}")
                            config.invalidate_device_cache()
                        logger.info(
                            f"Relay steal: device {device_id} took channel {device_update.channel} "
                            f"from device {displaced_id} ({loc}/{clu}/{dev_name})"
                        )
    ```
  - **In `Infrastructure/automation-service/app/routes/devices_crud.py:225-241`** (light relay conflict — uses `relay_channel` field):
    - Same logic but the field is `relay_channel` not `channel`:
    ```python
    if "relay_channel" in update_fields and update_fields["relay_channel"] is not None:
        hierarchy = await device_repo.get_all_as_hierarchy()
        for loc, clusters in hierarchy.items():
            for clu, devices in clusters.items():
                for dev_name, dev_info in devices.items():
                    if (dev_info.get("channel") == update_fields["relay_channel"]
                        and dev_info.get("device_id") != device_id):
                        displaced_id = dev_info.get("device_id")
                        if displaced_id is not None:
                            ok = await device_repo.clear_relay_binding_only(displaced_id)
                            if not ok:
                                raise HTTPException(status_code=500, detail=f"Failed to clear relay binding for displaced device {displaced_id}")
                            config.invalidate_device_cache()
                        logger.info(f"Relay steal: light {device_id} took channel {update_fields['relay_channel']} from device {displaced_id}")
    ```
  - **IMPORTANT: `DeviceRepository.update_device()` at `devices.py:626-637` has its OWN conflict check** that raises `ValueError`. The `clear_relay_binding_only()` call MUST happen BEFORE `update_device()` so the displaced device's channel is NULL when the repository check runs. The code above does this correctly (null-first, then the existing `updated = await device_repo.update_device(...)` at line 307 runs after).
  - **Response format:** The endpoint currently returns `Device | LightDevice` directly. Change the return to include `displaced_device_id`:
    ```python
    # Replace `return updated` at the end of the non-light branch (line 312) with:
    response = updated.model_dump()
    response["displaced_device_id"] = displaced_id  # None if no displacement
    return response
    ```
  - **Must NOT** create a new `null_channel()` method — use the existing `clear_relay_binding_only()` at `devices.py:699-710`
  - **Must NOT** change the DFR channel conflict logic (lines 243-262) — only relay channel conflicts
  - Run `cd Infrastructure/automation-service && ruff check . && pytest tests/ -q`
  Parallelization: Wave 2 | Blocked by: — | Blocks: 5, 7 | Can parallelize with: 1, 2, 3, 6
  References:
  - `Infrastructure/automation-service/app/routes/devices_crud.py:289-312` (non-light relay conflict + return — change)
  - `Infrastructure/automation-service/app/routes/devices_crud.py:225-278` (light relay conflict — change)
  - `Infrastructure/automation-service/app/repositories/devices.py:699-710` (clear_relay_binding_only — EXISTING, use it)
  - `Infrastructure/automation-service/app/repositories/devices.py:626-637` (repository-level conflict check — MUST null before calling update_device)
  - `Infrastructure/automation-service/app/models/device_registry.py:134` (DeviceUpdate.channel field)
  Acceptance criteria:
  - `cd Infrastructure/automation-service && ruff check .` passes
  - `cd Infrastructure/automation-service && pytest tests/ -q` passes
  - `grep -n "clear_relay_binding_only" Infrastructure/automation-service/app/routes/devices_crud.py` returns matches (relay steal code is present)
  QA scenarios: happy — relay steal works, displaced device gets NULL channel, response includes displaced_device_id. failure — no other device has the channel (displaced_device_id is None, proceed normally). Evidence `.omo/evidence/task-4-control-page-overhaul.txt`
  Commit: Y | feat(backend): allow relay steal — NULL displaced device's channel instead of 409

- [x] 5. Frontend: relay steal UI (red outline on displaced devices + real-time matrix update)
  What to do / Must NOT do:
  - **In `Infrastructure/frontend/src/services/api.ts:162`** (`updateDevice` method):
    - Change the return type to include `displaced_device_id`:
    ```typescript
    async updateDevice(device_id: number, body: Record<string, unknown>): Promise<DeviceRegistryEntry & { displaced_device_id?: number | null }> {
      const response = await this.automationClient.put(`/api/devices/registry/${device_id}`, body)
      return response.data
    }
    ```
  - **In `Infrastructure/frontend/src/components/devices/DeviceTable.tsx`**:
    - After `apiClient.updateDevice()` succeeds (line 283), check the response for `displaced_device_id`
    - If present and non-null, store it in state: `const [displacedDeviceId, setDisplacedDeviceId] = useState<number | null>(null)`
    - In `submitEdit()` (around line 252-292): `const result = await apiClient.updateDevice(...); if (result.displaced_device_id) setDisplacedDeviceId(result.displaced_device_id)`
    - In the row rendering (line 478+): if `device.device_id === displacedDeviceId`, add `ring-2 ring-status-danger` to the `<tr>` className
    - Also add a red border to the relay channel cell: `border-2 border-status-danger`
    - The outline clears when the user edits that device or clicks refresh
  - **In `Infrastructure/frontend/src/components/DeviceManager.tsx`** (NOT ZoneConfig — DeviceManager renders DeviceTable):
    - DeviceTable already has `onRefresh?: () => void` (called after submitEdit at line 283) and DeviceManager already passes `onRefresh={handleSharedRefresh}` which increments `refreshKey`
    - BUT: DeviceManager does NOT have a `useEffect` watching `refreshKey` to reload channel assignments. Add:
    ```typescript
    useEffect(() => { void loadChannels(false) }, [refreshKey])
    ```
    - This makes the relay matrix reload channel assignments immediately after a device table change
  - **Must NOT** add polling — use the event-driven callback pattern
  - **Must NOT** change the DeviceTable form structure
  - **Must NOT** modify ZoneConfig.tsx for this task
  - Run `cd Infrastructure/frontend && npx tsc --noEmit && npm run build`
  Parallelization: Wave 2 | Blocked by: 4 | Blocks: 7 | Can parallelize with: 1, 2, 3, 6
  References:
  - `Infrastructure/frontend/src/services/api.ts:162` (updateDevice return type — change)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:43-49` (props with onRefresh)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:252-292` (submitEdit function)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:283` (onRefresh?.() call)
  - `Infrastructure/frontend/src/components/devices/DeviceTable.tsx:478+` (non-editing row rendering)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx` (renders DeviceTable, has handleSharedRefresh + refreshKey)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx` (loadChannels function + where to add useEffect)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes
  - `cd Infrastructure/frontend && npm run build` passes
  - `grep -c "displaced_device_id" Infrastructure/frontend/src/components/devices/DeviceTable.tsx` returns ≥ 1
  - `grep -c "displaced_device_id" Infrastructure/frontend/src/services/api.ts` returns ≥ 1
  QA scenarios: happy — relay steal response carries displaced_device_id, DeviceTable shows red outline, DeviceManager reloads channels. failure — no device displaced (displaced_device_id is null, no red outline). Evidence `.omo/evidence/task-5-control-page-overhaul.txt`
  Commit: Y | feat(frontend): relay steal UI with red outline + real-time matrix update

- [x] 6. Fix ClimatePeriodTimeline: moon period editing, ramp positioning, photoperiod lock
  What to do / Must NOT do:
  - **Fix edge handle visibility** in `ClimatePeriodTimeline.tsx:326-337`:
    - The handles are NOT swapped — the wiring is conceptually correct (left edge of moon = end of sun/day, calls `handleEdgeMouseDown('end')`; right edge = start of sun/day, calls `handleEdgeMouseDown('start')`)
    - THE BUG: handles are nearly invisible — `w-5` (20px) wide with `opacity-60`. Fix to `w-8` (32px) with `opacity-100` and a visible drag cursor
    - Add a `title` attribute: "Drag to adjust photoperiod boundary"
  - **Add right-click editing** for moon start/end time:
    - Currently right-click on moon band opens ramp popover (line 311-317)
    - Add a context menu with options: "Edit night start time", "Edit night end time", "Edit ramp times"
    - When user selects a time edit, show an input field in the popover
  - **Fix photoperiod lock**: verify `lockedPhotoperiodHours` is properly passed from ZoneConfig. In ZoneConfig.tsx:384: `const lockedPhotoperiod = currentModeName === 'flower' ? 12 : currentModeName === 'veg' ? 18 : null` — this should be the SUN duration. The lock should enforce that sun + moon = 24h and moon = 24 - lockedPhotoperiodHours. Verify the lock logic in `handleEdgeMouseDown` and `handleMouseMove` correctly maintains this.
  - **Fix ramp positioning**: The ramp-up and ramp-down gradients are currently rendered on the MOON band (lines 339-358). They should be on the SUN band:
    - Ramp-up gradient: at the LEFT edge of the SUN band (= start of sun = where intensity increases from 0 to target)
    - Ramp-down gradient: at the RIGHT edge of the SUN band (= end of sun = where intensity decreases from target to 0)
    - NOTE: The current ramp-up is at the LEFT edge of moon (= end of sun = where ramp-DOWN should be) and ramp-down is at the RIGHT edge of moon (= start of sun = where ramp-UP should be). When moving them to the sun band, place ramp-up at left and ramp-down at right.
    - Move the gradient divs from inside `moonSegments.map()` (lines 339-358) to inside `sunSegments.map()` (lines 362-372)
  - **Must NOT** change the data model or API calls
  - **Must NOT** remove the ramp popover
  - Run `cd Infrastructure/frontend && npx tsc --noEmit && npx vitest run`
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 7 | Can parallelize with: 2, 3, 4, 5
  References:
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:98-124` (handleEdgeMouseDown)
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:126-186` (drag handlers)
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:299-360` (moon band rendering + handles + ramps)
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:362-372` (sun band rendering)
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx:311-317` (onContextMenu — currently only ramp popover)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:384` (lockedPhotoperiodHours)
  - `Infrastructure/frontend/src/pages/ZoneConfig.tsx:399-406` (onDayStartChange, onDayEndChange, ramp change handlers)
  - `Infrastructure/frontend/src/components/ClimatePeriodTimeline.interaction.test.tsx` (existing tests to keep passing)
  Acceptance criteria:
  - `cd Infrastructure/frontend && npx tsc --noEmit` passes
  - `cd Infrastructure/frontend && npx vitest run` passes (including ClimatePeriodTimeline tests)
  - `grep -c "w-8" Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx` returns ≥ 2 (handle visibility fix)
  - `grep -c "timeline-ramp-up-gradient\|timeline-ramp-down-gradient" Infrastructure/frontend/src/components/ClimatePeriodTimeline.tsx` — verify gradients exist in the sun band section, not the moon band section
  - Add a vitest test that verifies veg mode (18h) enforces 18h sun / 6h moon when dragging
  QA scenarios: happy — all timeline editing works, ramps positioned correctly. failure — locked photoperiod prevents invalid changes. Evidence `.omo/evidence/task-6-control-page-overhaul.txt`
  Commit: Y | fix(frontend): ClimatePeriodTimeline moon editing, ramp positioning, photoperiod lock

- [x] 7. Deploy + verify all fixes
  What to do / Must NOT do:
  - **Pre-deploy verification:**
    ```bash
    cd Infrastructure/automation-service && ruff check . && pytest tests/ -q
    cd Infrastructure/frontend && npx tsc --noEmit && npm run build
    ```
  - **Deploy (10-minute timeout to prevent deploy.sh from being killed):**
    ```bash
    timeout 600 ./deploy.sh
    ```
  - **Restart automation-service** (to pick up DB table creation from T1):
    ```bash
    sudo systemctl restart automation-service
    sleep 5
    sudo systemctl is-active automation-service
    ```
  - **Verify light intensity fix:**
    ```bash
    API_KEY="0e1e28754260f4e810ff8a9503336ad6008e8d30d837b7e2e9819e54fa3479e9"
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/Veg%20Room/main/zone-status | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{l[\"device\"]}: target={l.get(\"target_intensity\")} effective={l.get(\"scheduler_effective_intensity\")}') for l in d['lights']]"
    # Expected: target_intensity matches mode_parameters value (NOT 10.0)
    ```
  - **Verify light target set works (via pytest, NOT production POST):**
    ```bash
    cd Infrastructure/automation-service && pytest tests/ -q -k "light_target" --tb=short
    # Expected: test passes (set_target_intensity works against test DB with new tables)
    ```
  - **Verify relay matrix display names:**
    ```bash
    curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/channels | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'ch{ch}: name={info.get(\"display_name\")} type={info.get(\"device_type\")}') for ch,info in d['channels'].items() if info.get('device_name')]"
    # Expected: display_name is human-readable for ALL devices
    ```
  - **Verify relay steal works (via pytest against cea_sensors_test, NOT production):**
    ```bash
    cd Infrastructure/automation-service && pytest tests/test_device_registry_fix.py -q -k "relay_steal" --tb=short
    # Expected: test passes (relay steal logic verified against test DB)
    ```
  - Must NOT send PUT/POST/DELETE against production endpoints (AGENTS.md permanent ban)
  - Only GET requests allowed against production for read-only verification
  - **Log verification:**
    ```bash
    journalctl -u automation-service --since "2 minutes ago" | grep -i "error\|light_target.*does not exist" | head -5
    # Expected: empty (no errors)
    ```
  - Must NOT send PUT/POST/DELETE against production endpoints (AGENTS.md permanent ban — only GET allowed)
  Parallelization: Wave 3 | Blocked by: 1, 2, 3, 4, 5, 6 | Blocks: — | Can parallelize with: —
  References:
  - `deploy.sh` (project root)
  - All changed files from T1-T6
  Acceptance criteria:
  - `deploy.sh` exits 0 (with 600s timeout)
  - `curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/lights/Veg%20Room/main/zone-status | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{l[\"device\"]}: target={l.get(\"target_intensity\")} effective={l.get(\"scheduler_effective_intensity\")}') for l in d['lights']]"` shows target_intensity > 10.0 for lights
  - `pytest tests/ -q -k "light_target"` passes (set_target_intensity works against test DB)
  - `pytest tests/ -q -k "relay_steal"` passes (relay steal works against test DB)
  - `curl -s -H "X-API-Key: $API_KEY" http://127.0.0.1:8001/api/devices/channels | python3 -c "import sys,json; d=json.load(sys.stdin); [print(f'{ch}: {info.get(\"display_name\")}') for ch,info in d['channels'].items() if info.get('device_name')]"` shows display_name for all devices
  - `journalctl -u automation-service --since "2 minutes ago" | grep -c "light_target_intensity.*does not exist"` returns 0
  QA scenarios: happy — all fixes deployed and working. failure — rollback. Evidence `.omo/evidence/task-7-control-page-overhaul.txt`
  Commit: N | (deploy + verification, no code commit)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
- [x] F2. Code quality review
- [x] F3. Static checks + local verification (AGENTS.md compliant — NO production HTTP)
- [x] F4. Scope fidelity

## Commit strategy
- Wave 1: T1 `fix(db): create light_target_intensity + light_programs tables directly via SQL, seed from mode_parameters`
- Wave 2: T2 `fix(frontend): relay matrix uses display_name for all devices`; T3 `style(frontend): scale relay badges 2.5x`; T4 `feat(backend): allow relay steal`; T5 `feat(frontend): relay steal UI + real-time matrix update`; T6 `fix(frontend): ClimatePeriodTimeline moon editing + ramp positioning`
- Wave 3: T7 (no commit — deploy + verification)

## Success criteria
- `light_target_intensity` and `light_programs` tables exist in `cea_sensors`
- Lights show correct target intensity (NOT 10%) in the frontend
- Setting light target intensity via the slider works (no 500 error)
- Relay matrix shows `display_name` (human-readable) for ALL devices, not just lights
- OFF/AUTO/ON badges are 2.5x bigger
- Changing a relay # in DeviceTable to an in-use relay succeeds, displaces the old device (NULL channel), shows red outline
- Relay matrix updates in real-time after device table changes
- Climate period timeline: can drag moon start/end handles, right-click to edit times, ramp gradients on sun band edges, photoperiod lock enforced
- `ruff check .`, `tsc --noEmit`, `pytest`, `npm run build` all pass
- One successful deploy
