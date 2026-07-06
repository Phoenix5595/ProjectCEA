---
slug: light-registry-dfr-master
status: delivered
intent: clear (OVERRIDE — user explicitly asked to be interviewed; high-accuracy Momus review SKIPPED per user choice)
pending-action: completed — plan written at .omo/plans/light-registry-dfr-master.md (10 todos + 4 deploy steps, Metis gap-analysis folded in)
prior-draft: settings-relay-matrix-dfr-cleanup.md — UNRELATED. User confirmed 2026-07-05.
---

## Decision summary (ALL forks RESOLVED)
- Q1 (source-of-truth): DB-backed registry.
- Q2 (test button): 5s sweep 100→10→100, hard interlock, restore prior state, 30s deadman, blocked in failsafe.
- Q3 (scope): ALL devices → DB-backed, typed-domain end-state (Option C). Git evidence confirms non-light YAML edits exist.
- Q4 (DFR create/create-flow): create-on-empty-slot + auto-suggested per-room index (max+1, user-overridable).
- Q5 (test strategy): TDD per repo rule.
- Q6 (plan shape): Bundle into ONE plan, internally-staged deploys (no prod half-state between deploys).
- User intent (decisive): "I do not want to have to edit the YAML all the time when playing with devices."

## APPROACH (final, for approval)
ONE plan executed as 4 internally-staged deploys with 30s rollback between each. End-state: fully DB-backed typed-domain device model; YAML becomes first-run-bootstrap only; lights/DFR feature ships; deploy no longer touches device config.

Deploy 1 — Schema + Repository + Migration (NO control-loop path change)
- alembic 008_device_registry: new `device_registry` table. PK `device_id SERIAL`. Columns: location TEXT NOT NULL, cluster TEXT NOT NULL (always 'main' for devices), device_name TEXT NOT NULL, display_name TEXT, device_type TEXT NOT NULL, channel INTEGER (nullable — a device can exist without a relay binding), dimming_enabled BOOL, dimming_type TEXT, dimming_board_id INTEGER REFERENCES hardware boards, dimming_channel SMALLINT, safety_level INTEGER, pid_enabled BOOL, interlock_with JSONB, pid_setpoints JSONB, per_room_index INTEGER, created_at, updated_at. UNIQUE(board_id, dimming_channel) WHERE dimming_board_id IS NOT NULL. UNIQUE(location, cluster, device_name). UNIQUE(location, per_room_index, device_type='light') — per-room light index uniqueness.
- DeviceRegistryRepository (extend devices.py or new file): CRUD methods (get_all_as_hierarchy returning the dict shape the control loop expects, create_light, update_light, delete_light, clear_relay_binding_only, get_lights_by_room, get_unbound_lights_by_room).
- Idempotent seed migration: INSERT ... ON CONFLICT DO NOTHING the 6 existing lights + 6-8 non-light devices from current YAML HEAD. Per-room_index auto-derived from light_N suffix.
- LightDevice + Device Pydantic domain models (app/models/device_registry.py) for API contract + runtime type safety.
- Tests pin: migration idempotency, seed correctness for all 6 lights, repo CRUD returns expected shape.
- Control loop UNCHANGED — still reads YAML via get_devices.
- Deploy 1 ships schema+repo only, no behavior change.

Deploy 2 — Flip the reader: DB becomes source of truth for device config
- `config.get_devices()` rewritten to read from `device_registry` (via DeviceRepository) instead of YAML. Returns SAME dict shape `{location:{cluster:{device_name:{...}}}}` — zero call-site change downstream.
- EngineConfigCache TTL (30s) unchanged.
- `_validate_config` extended to validate DB-backed registry fields too.
- YAML becomes first-run-bootstrap: on startup, if `device_registry` is empty, seed from automation_config.yaml ONCE (idempotent). After first boot, YAML `devices:` block is ignored for device identity (hardware/sensors/control sections still YAML — only `devices:` moves).
- Tests pin: get_devices() returns DB content in correct dict shape; first-run bootstrap seeds empty DB only; subsequent run ignores YAML.
- Deploy 2: deploy-reset bug ELIMINATED for all devices — every deploy now reads/writes the DB, which is never rsync'd.

Deploy 3 — Lights/DFR feature surface (the visible UX)
- Backend: new endpoints — POST /api/lights (create on DFR empty slot), PUT /api/lights/{id} (rename/room/index), DELETE /api/lights/{id} (remove), PUT /api/lights/{id}/relay-binding (bind/unbind relay channel; greyout invariant enforced here), POST /api/lights/{id}/test (5s sweep). All defer to DeviceRegistryRepository; touch no YAML.
- Backend: clear_channel_device (devices.py:638) REWRITTEN to set `channel=NULL` on the light's registry row instead of deleting it — ONLY when the device is a light. Non-light clear behavior preserved per DR3.
- Backend: GET /api/devices/channels light_names list derived from device_registry filtered by (room match for greyout).
- Frontend: DfrBoardsPanel.tsx redesigned — empty slot shows "Add light" form (room dropdown, display_name, per_room_index pre-filled max+1); occupied slot shows editable display_name/room/index + "Test" button + "Remove light" with warning. Per-room index conflict check.
- Frontend: DeviceManager.tsx channel-table dropdown redesigned — when device_type=light + location selected, options = lights from device_registry WHERE room=selected_location; already-bound-to-another-relay lights DISABLED (greyed out). Strict 1:1.
- Frontend: clearing a relay row (double-click) sets device to null-binding state for lights (light survives unbound), deletes for non-lights (per DR3).
- Test backend: 5s sweep endpoint acquires bus-wide lock (asyncio.Lock), saves prior intensity+mode, sets device to manual, holds relay ON, sweeps 100→10→100 over ~5s, restores prior state in finally, 30s deadman, blocked if room in failsafe.
- Tests pin: clear-only-nulls-binding for lights (root cause #1 regression test); greyout filters by room; test-button acquires lock + restores prior even on exception; 1:1 binding DB constraint; per-room index conflict rejection.
- Deploy 3: ships the user-visible fix.

Deploy 4 — Typed-Domain refactor (the clean end-state)
- Replace `dict[str, dict[str, dict[str, dict[str, Any]]]]` with typed `Device` / `LightDevice` Pydantic objects across EngineConfigCache (engine_config_cache.py:14-54), the ~12 scheduler call sites (scheduler.py + device_processor.py + light_ramp_calculator.py + light_effective_setpoint_logging.py + debug route + status route + control_engine.py), DeviceController, and all device-reading routes.
- get_devices() returns `dict[str, list[Device]]` keyed by location (or similar typed shape — finalized at todo detail).
- pyright strict mode maintained (0 errors).
- Tests pin every call site reads the new typed shape correctly; no behavior change observable (pure refactor).
- Deploy 4: pure code quality, no UX change, full Option-C coherence.

## Derived requirements (NOT forks — already determined)
- DR1: Relay↔light strict 1:1. Greyout invariant = `channel IS NOT NULL AND != current`.
- DR2: Clearing a light's relay binding NULLs channel only; NEVER deletes the light row.
- DR3: Non-light clear behavior unchanged for now (still deletes the row); non-light clear becomes "delete registry row" once Deploy 2 ships DB-reads.
- DR4: Light `room` ∈ {Flower Room, Veg Room, Lab, Outside}. cluster = always 'main'.
- DR5: Migration auto-derives per-room index from existing `light_N` suffix for the 6 baseline lights.
- DR6: UNIQUE(board_id, dimming_channel) — preserves existing DFR global-uniqueness invariant, moves to DB constraint.
- DR7: Preserve Pydantic validation (config_schema.py) for the YAML bootstrap; add LightDevice/Device Pydantic models for DB rows.
- DR8: New alembic = `008_device_registry.py` (head is 007).
- DR9: TDD per repo rule (>80% coverage).
- DR10: Test-button: 5s sweep 100→10→100, hard interlock (bus lock + manual mode + relay ON + restore prior + 30s deadman + blocked-in-failsafe).
- DR11: Deploy 1 ships nothing user-visible; Deploy 2 eliminates deploy-reset; Deploy 3 ships the feature; Deploy 4 is pure refactor. Each deploy bounded by 30s rollback.

# Draft: light-registry-dfr-master

## User request (verbatim intent)
"we need a better way to deal with light. i just tried to reasing to different relays the lights. i cleared the light
relays and now the light are gone from the DFR and the relay board. i cant add them back. the DFR should be where lights
are put in the system. i should be able to assign a light to a room, and change its display name, and its # inside the
room. The display name should then be available in the channel assignment table: for example, when i select a relay,
select light as a type and flower room for location, all light from DFR channels that are labeled as flower room lights
should appear as options. an already configured light to another relay from this light should be greyed out. the DFR
section is the master for lights in a kind of way. There should be a test button that appears on the DFR, per channel,
that will for 5m cycle the light up down from 100 to 10 to 100 in order for the user to accurately know which light is
which. after a deploy, the old state seems to reset."

Explicit user instruction: "explore the code so you understand the project, check online for best implementation
practices and ask questions instead of assuming."

## Components (topology lock)
| ID | Outcome (independent success/failure) | Status | Evidence |
|----|--------------------------------------|--------|----------|
| C1 | Light device IDENTITY lives on DFR side (board_id, channel) + room + display_name + per-room index | active | DFR assignment response already returns dimming_board_id/dimming_channel per light: lights.py:165-194 |
| C2 | Relay channel-assignment table consumes DFR-defined lights as filtered, greyout-aware dropdown options | active | DeviceManager.tsx channel table + light_names list (devices.py:496-511) + DfrBoardsPanel.tsx:79-90 lightOptions |
| C3 | Per-DFR-channel TEST button sweeps dimmer 100→10→100 for ~duration; safe vs control loop + schedule + I2C contention | active | DFR0971Manager.set_intensity (dfr0971.py:332-352); no test endpoint exists yet |
| C4 | Device/light config PERSISTS across `./deploy.sh` (rsync --delete no longer nukes runtime YAML) | active | deploy.sh:134 rsync + :169 symlink swap; config.py:184 loads from release path |
| C5 | Migration preserves 6 existing lights (Flower light_1/2/3 + Veg light_1/2/3) already in git HEAD YAML | active | automation_config.yaml:26-103 |

## Verified findings (independently confirmed by Prometheus from primary source — not from subagent claims)

### Root cause #1 — "cleared relay → light gone from BOTH panels and can't re-add" (CONFIRMED)
- `clear_channel_device` (devices.py:638-672) does `del devices[device_name]` on the ENTIRE YAML device row.
- That single row carries BOTH the relay binding (`channel`) AND the DFR binding (`dimming_board_id`, `dimming_channel`) AND `display_name` AND `device_type` AND `dimming_enabled`/`dimming_type`/`safety_level`.
- So clearing the relay cascade-deletes the DFR-side light definition. ✗ root cause.
- `assign_dfr_channel` (lights.py:236-249) REFUSES to write a DFR binding unless the underlying device row already exists ("Device not found" 404). So after `clear_channel_device` nukes the row, there is NO UI to recreate the light from the DFR side. ✗ "can't add them back" confirmed.
- Existing devices UI flow REQUIRES creating the device via `update_channel_device` (relay side) FIRST, with `light_name` provided so a display_name gets set; only THEN can the DFR panel see it and bind board+channel. Order is exactly backwards vs "DFR is master".

### Root cause #2 — "after a deploy, the old state seems to reset" (CONFIRMED)
- `ConfigLoader.__init__` loads from `Path(__file__).parent.parent / "automation_config.yaml"` (config.py:184) = `/opt/projectcea/current/Infrastructure/automation-service/automation_config.yaml` (the symlinked release tree).
- `deploy.sh:134`: `sudo rsync -a --delete "$SOURCE/Infrastructure/" "$TARGET/Infrastructure/"` copies the git-checked-in YAML (with all 6 pre-defined lights) into the new release dir.
- `deploy.sh:169`: `sudo ln -sfn "$TARGET" /opt/projectcea/current` swaps the symlink.
- Net: every deploy overwrites runtime-edited device YAML with git HEAD's version. ✗ confirmed.
- `schedules.yaml` / `rules.yaml` sit sibling to the YAML (config.py:195-196) and would have the same problem if they were runtime-edited (they appear to be DB-backed for schedules via the schedule_repo, so less acute — TBD).

### Existing DFR endpoint behaviour (CONFIRMED)
- `GET /api/lights/dfr/assignments` (lights.py:197-232) returns `{boards, assignments{board_id:{0,1}}, lights[]}`; the `lights[]` list = every device whose `device_type==light` AND `dimming_enabled==true` AND `dimming_type==dfr0971` (lights.py:177-193). Filtered ON the existing device rows — there is no separate "DFR light" table.
- `PUT /api/lights/dfr/assign` (lights.py:235-308) enforces GLOBAL uniqueness of `(board_id, dimming_channel)` per light (lights.py:280-296, 409 on conflict). This invariant must be PRESERVED.
- `update_device_config` (devices.py:396-471) updates display_name/device_type on the SAME device row.

### Existing channel-assignment dropdown source (CONFIRMED)
- `GET /api/devices/channels` returns `light_names[]` (devices.py:496-511) = every light device's `display_name` + `device_name` + `location` + cluster. Consumed by DeviceManager.tsx as `uniqueLightNames` for the `light_name` dropdown when device_type=light. Currently NOT filtered by selected location, NOT greyed-out for already-bound lights. ✗ both features are net-new.

### Existing lights in git HEAD YAML (the migration baseline)
- Flower Room/main: light_1 (Chilled Front, ch10, board2/ch0), light_2 (Apache, ch11, board1/ch1), light_3 (Chilled Back, ch12, board2/ch1).
- Veg Room/main: light_1 (Eyefinity Top, ch3, board0/ch0), light_2 (Ridgetop Bottom Right, ch4, board0/ch1), light_3 (Ridgetop Bottom Left, ch5, board1/ch0).
- Per-room index is ALREADY implicit via `light_N` device_name suffix — can promote to an explicit `room_index` column. Migration can auto-derive.

### Hardware/Clearance constraints (from AGENTS.md — non-negotiable)
- MCP23017 (relays) on I2C bus 0, addr 0x27, channels 0-15. DFR0971 (dimming) on I2C bus 1, addr 0x88/0x89/0x90. NEVER swap buses/roles.
- Cluster topology: `main` is device-cluster only; `front`/`back` are Flower sensor sub-clusters only. A LIGHT device's cluster is ALWAYS `main`. Location ∈ {Flower Room, Veg Room, Lab, Outside}.
- Config validation via Pydantic (app/models/config_schema.py) — `dimming_board_id` must reference an existing board in `hardware.dfr0971_boards`. Must keep validating.
- No `sleep()` in control loop. Control loop tick 1-5s. Redis <1ms. Don't break these.

## RESOLVED forks (user answered turn 1)

### Q1 — Source-of-truth location → ANSWERED: Option B (Migrate device registry to TimescaleDB)
Identity = (board_id, dimming_channel) immutable; display_name/room/index mutable; survives deploys by construction.

### Q2 — TEST-button → ANSWERED: 5 seconds, hard interlock, restore prior state
Sweep ~5s (100%→10%→100%); during test: device=manual, relay held ON, bus-wide lock, prior intensity+mode restored after (even on failure), 30s deadman cap, blocked in safety/failsafe state.

## Major finding (turn 2 — reshapes Option C effort estimate)
The repo ALREADY has a DB-backed device model — Option C is NOT greenfield:
- `device_states` table (devices.py:17-92): runtime state (state, mode, channel, updated_at) — UNIQUE on (location, cluster, device_name). DB-backed.
- `device_mappings` table (devices.py:94-155): HW mapping (channel, active_high, safe_state, mcp_board_id) — UNIQUE on (location, cluster, device_name). DB-backed.
- `DeviceRepository` (devices.py:11, BaseRepository subclass): asyncpg-based, follows the repo's documented Repository Pattern (AGENTS.md "Repository Pattern Architecture" section).
- ALSO: `get_latest_light_intensity` (devices.py:157-175) already reads `effective_setpoints.effective_light_intensity` — DB already tracks per-light intensity.

The MISSING piece = a `device_registry` (or `light_registry`) table owning the CONFIG that currently lives in YAML: device_type, display_name, dimming_board_id, dimming_channel, dimming_enabled, dimming_type, safety_level, pid_enabled, interlock_with, pid_setpoints.

So:
- Option A = add `light_registry` table ONLY for lights; merge into `config.get_devices()`. DeviceRepository unchanged.
- Option B = add `device_registry` table for ALL devices (lights + heaters + fans + dehumidifiers); `config.get_devices()` reads DB. Completes the DB-backed model the repo is ALREADY halfway toward.
- Option C = extend DeviceRepository to own registry too + refactor 12+ scheduler call sites to typed `Device` domain objects (replace the dict shape). Biggest blast radius — a SEPARATE modernize-device-access refactor bundled into a light-registry feature = scope creep + crop-safety regression risk.

Repo trajectory: device_states + device_mappings + DeviceRepository all already DB-backed → the YAML `devices:` block is the legacy. Option B aligns with the trajectory; Option A is a pragmatic detour; Option C is a worthwhile SEPARATE refactor, not bundled here.

## User intent — DECISIVE (turn 3)
"i do not want to have to edit the yaml all the time when playing with devices"

Combined with:
- Q3 guiding-Q1 (trajectory): "Need more evidence" — but see decisive statement above, which resolves it.
- Q3 guiding-Q2 (deploy-reset scope): "Fix deploy-reset for ALL devices".
- Q3 guiding-Q3 (effort/risk): "leaning towards option c since it seems the more solid".
- Q3 main: "Option C — full typed-Domain refactor (do later, separate plan)" — BUT the "do later" framing is in tension with the decisive YAML-exit statement. The user wants OUT of YAML now, not later.

RESOLUTION: User wants the fully DB-backed, typed-domain end-state for ALL devices, in a way that permanently removes YAML device editing. This is Option C's end-state, applied to the FULL device plane, scoped to the lights/DFR feature request as the trigger.

Git evidence (Q1 evidence): automation_config.yaml has 15 commits since Dec 2025, including non-light edits like "heater -> heating" canonicalization (226b048), "reroute Flower lights to ch10/11/12" (9535f69). Non-light devices HAVE been runtime/operationally edited via YAML git commits — deploy-reset drift is not purely theoretical. Confirms Option-B-or-C scope, not just A.

## Remaining fork before approval gate

### Q6 — Bundle C into this single plan vs. stage it (FINAL fork)
Unblocks: plan shape, deploy count, risk surface, whether this is 1 plan or 2.
Explored: Option C end-state touches ~12 scheduler call sites + DeviceController + all routes + EngineConfigCache, replacing the dict shape with typed `Device` objects. TDD + staged deploy + 30s rollback per AGENTS.md. User wants out of YAML now.
Why unresolved: bundle-now = single plan, 2-3 deploys in one sequence, higher per-deploy risk but no interim half-state. Stage = plan 1 (DB registry + lights/DFR feature, no control-loop path change — lights still served via merge layer), plan 2 (typed-Domain refactor across the 12 call sites), lower per-deploy risk but means a temporary merge-layer half-state sits in prod between plans.

### Q4 — DFR create-flow + per-room index model → ANSWERED: Create-on-empty-slot + auto-suggested index
On an empty (board, channel) slot: "Add light" → form with room dropdown, display_name, per-room index pre-filled = max-in-room + 1 (user can override). On save → INSERT into light_device_registry with (board_id, dimming_channel) = that slot. Occupied slot: edit display_name/room/index + "Remove light" (warns any relay binding also unbinds). Per-room index stored, user-editable (with conflict-check).

### Q5 — Test strategy → ANSWERED: TDD per repo rule

## Derived requirements (NOT forks — stated fixes; recorded so user can correct)
[unchanged — see below]

## Derived requirements (NOT forks — stated fixes; recorded so user can correct)

These are NOT questions because the user's intent + root-cause findings already determine them:
- DR1: Relay↔light strict 1:1 cardinality (one device row holds one channel; existing invariant in clear_channel_device + update_channel_device). Greyout = `assigned_relay_channel IS NOT NULL AND != current`.
- DR2: Clearing a relay (double-click row → clear) NULLs ONLY the light's `relay_channel` binding in the registry; NEVER deletes the light row. This IS the fix for root cause #1.
- DR3: Non-light device types (heater/fan/dehumidifier/co2/vent/pump) keep the existing free-text device_name entry on the relay table; the greyout/filtered-dropdown model applies to lights ONLY.
- DR4: Light `room` ∈ {Flower Room, Veg Room, Lab, Outside} (verified cluster topology); cluster is ALWAYS `main` for a light device.
- DR5: Migration auto-derives per-room index from existing `light_N` suffix for the 6 baseline lights (Flower light_1/2/3, Veg light_1/2/3); all have distinct (board_id, dimming_channel) pairs (verified).
- DR6: Preserve existing DFR global-uniqueness invariant on (board_id, dimming_channel) (lights.py:280-296, 409 on conflict) — moves to a UNIQUE constraint on the DB table.
- DR7: Preserve Pydantic config validation (config_schema.py) for the YAML bootstrap; add Pydantic models for the new DB-backed light registry rows.
- DR8: New alembic migration = `008_device_registry.py` (head is 007_add_pid_parameters_per_room).

## Owner-decisions to surface at approval gate (will list after Q3-Q5 land)
- (to be filled)

## Approach placeholder (finalized at gate)
- (to be filled — depends on Q3-Q5 outcome)
