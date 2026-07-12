# centralized-device-table - Work Plan

## TL;DR (For humans)

**What you'll get:** A single device table on the Devices & Relays page where you add any device (heater, fan, dehumidifier, light) in one place. Click "Add device," pick a room, pick a device type, enter a name, pick which relay (R1-R16) it's on. If it's a light, also pick which DFR board and DFR channel. Save. The device appears in the table, the relay matrix, and the DFR panel instantly. The old "Add light" button on the DFR panel is gone.

**Why this approach:** The current system has two broken device surfaces — the relay table writes to neither YAML nor DB (the YAML writer strips the devices section), and the DFR panel only creates lights. A unified table that writes to the `device_registry` DB table fixes both: one creation surface, one persistence layer, and the control loop sees every device you create because it reads from the same DB.

**What it will NOT do:** It will not modify the relay matrix visual (it stays as-is showing live ON/OFF state). It will not change the DFR panel's assignment/test/remove functionality (only the "Add light" button is removed). It will not touch the YAML config file (devices live in DB only now). It will not auto-migrate existing YAML devices to DB (that's a separate plan — the user enters them manually via the new table).

**Effort:** Medium
**Risk:** Medium — new backend endpoint + new frontend component + DFR panel modification; touches the device creation path but not the control loop.
**Decisions to sanity-check:** New `POST /api/devices/registry` path (not touching existing `GET /api/devices`). Two create models: new `DeviceCreate` for non-lights, reused `LightDeviceCreate` for lights. device_name pattern `{canonical_type}_{room_prefix}_{n}` for non-lights (e.g. `heating_f_1`). New `_CANONICALIZE_DEVICE_TYPES` mapping function (not `_canonicalize_device_types`). Relay channel conflict is GLOBAL (not per-room). `config.update_device_config()` fixed to route non-lights to new `update_device()`.

Your next move: approve, or run a high-accuracy review. Full execution detail follows below.

---

> TL;DR (machine): Medium, Medium risk — unified device table replacing relay table, new POST/PUT/DELETE /api/devices/registry writing to device_registry, DFR panel Add light removed.

## Scope
### Must have
1. New `POST /api/devices/registry` backend endpoint creating ANY device type (light or non-light) in `device_registry` DB table. Uses `/registry` suffix to avoid collision with existing `GET /api/devices` (devices.py:78-101, returns relay states — must NOT be touched).
2. New `PUT /api/devices/registry/{device_id}` and `DELETE /api/devices/registry/{device_id}` endpoints for updates and removal.
3. New `GET /api/devices/registry` endpoint returning ALL devices (light and non-light) as a flat list from `device_registry` DB. Separate from existing `GET /api/devices` (relay states) and `GET /api/devices/channels` (channel-centric view).
4. Two create models: new `DeviceCreate` for non-lights (fields: device_type, room, display_name, channel, pid_enabled?, interlock_with?, pid_setpoints?), and existing `LightDeviceCreate` (device_registry.py:57-68) reused for lights (fields: board_id, dimming_channel, room, display_name, per_room_index?). The `POST /api/devices/registry` endpoint dispatches based on device_type: if "light", validate as `LightDeviceCreate` + call existing `create_light()`; if non-light, validate as `DeviceCreate` + call new `create_device()`.
5. New `DeviceUpdate` Pydantic model — partial update fields for non-light devices (channel?, display_name?, pid_enabled?, interlock_with?, pid_setpoints?). Lights use existing `LightDeviceUpdate` (device_registry.py:71-85).
6. New `DeviceRepository.create_device()`, `update_device()`, `delete_device()` methods for non-light devices (lights already have `create_light()` etc.).
7. Device name auto-generation: lights = `light_{prefix}_{index}` (existing pattern, repositories/devices.py:28-30), non-lights = `{canonical_type}_{room_prefix}_{n}` (e.g. `heating_f_1`) where n = max+1 for that type+location. Add a regex pattern `r"^[a-z]+_[fvlo]_\d+$"` to the `Device` model's `device_name` field (device_registry.py:20) to match the light pattern format.
8. New `_UI_TO_DB_DEVICE_TYPES` mapping dict in `app/models/device_registry.py` (NOT using `_canonicalize_device_types` from config.py — it only aliases heater→heating). Exact mapping:
   | UI type (frontend) | DB canonical type |
   |---|---|
   | heater | heating |
   | dehumidifier | dehumidifier |
   | extraction fan | exhaust |
   | fan | cooling |
   | humidifier | humidifier |
   | co2 tank | co2 |
   | light | light |
9. Relay channel conflict checking: GLOBALLY reject duplicate relay channel assignments (MCP23017 has 16 physical channels — a channel conflict is global, not per-room). Reference: existing `bind_relay()` (repositories/devices.py:491-515) checks globally. When updating a device's relay channel, the old channel is freed.
10. DFR channel conflict checking: reject duplicate (board_id, dimming_channel) for lights. Return HTTP 409 (matching existing lights.py:833-834 status code, NOT 400).
11. `EngineConfigCache` invalidation: add `invalidate_device_cache()` method to `ConfigLoader` (config.py, which owns `self._device_cache` at line 209). The method sets `self._device_cache._device_hierarchy_cache = None` and `self._device_cache._cache_timestamp = None`. Routes call `config.invalidate_device_cache()` after every create/update/delete.
12. `cascade_device_name_change` side effect: when a light's room or per_room_index changes, cascade updates effective_setpoints + Redis keys (existing logic in `DeviceRepository.cascade_device_name_change`). Non-light devices: room is NOT updatable (update_device only allows channel, display_name, pid_enabled, interlock_with, pid_setpoints — NOT room), so device_name can't change and no cascade is needed.
13. Fix `config.update_device_config()` (config.py:537-590): currently calls `update_light()` for ALL devices (line 583), which returns `_row_to_light_device()` that crashes for non-lights (`LightDevice(device_type="heating")` fails `Literal["light"]`). Fix: route non-light updates to new `update_device()` instead.
14. Fix `_row_to_light_device()` bug: add `_row_to_typed_device()` dispatcher (repositories/devices.py) that returns `LightDevice` for lights and `Device` for non-lights based on the `device_type` column. Modify `update_light()` to call `_row_to_typed_device()` instead of `_row_to_light_device()` at lines 454 and 461.
15. New `DeviceTable.tsx` frontend component replacing the relay channel table. Device-centric rows, "Add device" button at bottom, inline editing, conditional DFR fields for lights.
16. New frontend type `DeviceRegistryEntry` in `types/device.ts` with fields: `{ device_id, device_type, device_name, display_name, location, cluster, channel (relay), board_id?, dimming_channel?, per_room_index?, pid_enabled?, interlock_with?, pid_setpoints? }`. The existing `Device` type (device.ts:3-11) is relay-state-shaped and must NOT be modified.
17. `apiClient` methods: `createDevice()`, `updateDevice()`, `deleteDevice()`, `getDeviceRegistry()` — all hitting `/api/devices/registry`. Do NOT modify existing `getAllDevices()` (api.ts:146, calls `GET /api/devices` for relay states, consumed by `useSensorPolling.ts:79`).
18. DFR panel: remove "Add light" button + `openAddForm` + `submitAddLight` + `addDraftBySlot` state. Keep: DFR channel assignment dropdown, light rename, light edit, light test, light remove.
19. Old relay table endpoints: `GET /api/devices/channels` remains functional (relay matrix depends on it). `POST /api/devices/channels/{channel}` is already broken (write_full_config strips devices) and is deprecated — the new `POST /api/devices/registry` replaces it. `DELETE /api/devices/channels/{channel}` remains for now (relay matrix uses it).

### Must NOT have (guardrails, anti-slop, scope boundaries)
- MUST NOT modify or replace the existing `GET /api/devices` endpoint (devices.py:78-101, returns relay states) — it is consumed by `useSensorPolling.ts:79`.
- MUST NOT modify or replace the existing `apiClient.getAllDevices()` (api.ts:146-147) — it is consumed by `useSensorPolling.ts:79`.
- MUST NOT modify the existing `Device` type (device.ts:3-11) — it is the relay-state type. Create a new `DeviceRegistryEntry` type instead.
- MUST NOT modify the relay matrix visual component (`RelayChannelMatrix.tsx`) — it continues to receive `relayChannels` + `statusByChannel` props.
- MUST NOT change the `/api/hardware/relays/state` endpoint or its polling.
- MUST NOT write to `automation_config.yaml` — devices are DB-only.
- MUST NOT modify the control loop, PID controllers, or scheduler.
- MUST NOT allow user-editable `device_name` — it's auto-generated for all device types.
- MUST NOT remove `GET /api/devices/channels` — the relay matrix and DFR panel still depend on it.
- MUST NOT use `_canonicalize_device_types` (config.py:65-124) for the UI→DB type mapping — it only aliases heater→heating and warns on unknowns. Define a new `_UI_TO_DB_DEVICE_TYPES` dict.
- MUST NOT set relay channel conflict scope to per-location+cluster — conflicts are GLOBAL (physical MCP23017 channels).
- MUST NOT set DFR conflict response to 400 — use 409 (matches existing lights.py:833-834).

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD — write failing tests first, then implement.
- Evidence: .omo/evidence/task-<N>-centralized-device-table.{txt,md}

## Execution strategy
### Parallel execution waves
- **Wave 0 (first):** Seed device_registry from old YAML (Task 1) — fixes lights being off immediately after deploy.
- **Wave A (parallel):** Backend endpoints + repository methods (Tasks 2-3).
- **Wave B (parallel, after A):** Frontend DeviceTable component (Task 4) + DFR panel Add light removal (Task 5).
- **Wave C:** Integration — wire DeviceTable into DeviceManager, replace relay table (Task 6).

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (Seed device_registry from YAML) | — | 2, 3 | — |
| 2 (Backend: DeviceCreate model + DeviceRepository) | 1 | 3 | — |
| 3 (Backend: POST/PUT/DELETE /api/devices/registry routes) | 2 | 4, 6 | — |
| 4 (Frontend: DeviceTable component + apiClient) | 3 | 6 | 5 |
| 5 (Frontend: DFR panel Add light removal) | — | 6 | 4 |
| 6 (Integration: wire DeviceTable into DeviceManager) | 3, 4, 5 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 1. Seed device_registry table from old YAML config (fixes lights being off)
  What to do:
  - Run the existing seed migration `009_seed_device_registry_from_yaml.py` OR manually insert all devices from the old YAML (git commit `87baad7:Infrastructure/automation-service/automation_config.yaml`) into the `device_registry` table.
  - The seed migration may have a bug: it parses `per_room_index` as `int(device_key.split("_", 1)[1])` which fails for keys like `light_f_1` (split gives `["light", "f_1"]`, `int("f_1")` raises ValueError). If the migration fails, manually insert the devices using SQL.
  - Devices to insert (from old YAML at commit 87baad7):

    **Flower Room (main cluster):**
    | device_name | display_name | device_type | channel (relay) | dimming_board_id | dimming_channel | per_room_index |
    |---|---|---|---|---|---|---|
    | light_f_1 | Chilled Front | light | 10 | 2 | 0 | 1 |
    | light_f_2 | Apache | light | 11 | 1 | 1 | 2 |
    | light_f_3 | Chilled Back | light | 12 | 2 | 1 | 3 |
    | Heater Flower | (none) | heating | 0 | — | — | — |
    | exhaust_fan | (none) | fan | 1 | — | — | — |
    | Midea Cube 50 pints | (none) | dehumidifier | 2 | — | — | — |

    **Veg Room (main cluster):**
    | device_name | display_name | device_type | channel (relay) | dimming_board_id | dimming_channel | per_room_index |
    |---|---|---|---|---|---|---|
    | light_v_1 | Eyefinity Top | light | 3 | 0 | 0 | 1 |
    | light_v_2 | Ridgetop Bottom Right | light | 4 | 0 | 1 | 2 |
    | light_v_3 | Ridgetop Bottom Left | light | 5 | 1 | 0 | 3 |
    | Heater Veg | (none) | heating | 6 | — | — | — |
    | exhaust_fan | (none) | fan | 9 | — | — | — |
    | Ivation 35 pints | (none) | dehumidifier | 7 | — | — | — |
    | Exhaust 4 inches | (none) | fan | 8 | — | — | — |

  - For lights: insert with `device_type='light'`, `dimming_enabled=true`, `dimming_type='dfr0971'`, `safety_level=0` (or 40 for veg lights 2-3 per old YAML), `pid_enabled=false`, `interlock_with=[]`, `pid_setpoints={}`.
  - For non-lights: insert with `device_type` = canonical type (heating, fan, dehumidifier), `channel` = relay channel, `pid_enabled=false`, `interlock_with=[]`, `pid_setpoints={}`, `dimming_enabled=false`, `dimming_type=NULL`, `dimming_board_id=NULL`, `dimming_channel=NULL`, `per_room_index=NULL`.
  - After inserting: restart automation-service so `config.get_devices()` reads the freshly populated DB and the control loop picks up the devices.
  - Verify: `curl http://localhost:8001/api/devices/Flower%20Room/main` returns the devices. Check `journalctl -u automation-service` for scheduler initialization showing devices loaded.
  Must NOT do:
  - MUST NOT modify the YAML file — it stays as-is for reference.
  - MUST NOT skip any device from the old YAML.
  - MUST NOT insert devices with wrong channel assignments (relay channels are physical — duplicates would conflict).
  Parallelization: Wave 0 | Blocked by: — | Blocks: 2, 3 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/alembic/versions/009_seed_device_registry_from_yaml.py` (existing seed migration — may have per_room_index parsing bug)
  - `Infrastructure/automation-service/alembic/versions/008_device_registry.py` (schema migration — already applied, alembic_version at 008)
  - Git commit `87baad7:Infrastructure/automation-service/automation_config.yaml` (old YAML with all device definitions)
  - `Infrastructure/automation-service/app/repositories/devices.py:13-18` (_ROOM_PREFIXES — Flower=f, Veg=v)
  - `Infrastructure/automation-service/app/repositories/devices.py:28-30` (_generate_light_device_name — pattern reference)
  - `Infrastructure/automation-service/app/config.py:289-307` (get_devices — reads from DB, returns to control loop)
  Acceptance criteria (agent-executable):
  - `sudo -u postgres psql -d cea_sensors -c "SELECT count(*) FROM device_registry"` → returns > 0 (at least 13 devices).
  - `sudo -u postgres psql -d cea_sensors -c "SELECT device_name, device_type, location, channel FROM device_registry ORDER BY location, channel"` → returns all devices from the table above.
  - After service restart: `journalctl -u automation-service --since "1 min ago" | grep -i "scheduler"` → shows devices loaded.
  QA scenarios:
  - Happy: after seeding + restart, the relay matrix shows devices on channels, DFR panel shows lights, and scheduled lights turn ON at the right time. Evidence: `.omo/evidence/task-1-centralized-device-table.txt`
  - Failure: if device_registry is still empty after seeding, control loop has no devices → lights stay off → `journalctl -u automation-service | grep "device" | tail -5` shows empty. Evidence: `.omo/evidence/task-1-failure-centralized-device-table.txt`
  Commit: N (data seeding, not code) — or Y if a SQL script is created: `fix(db): seed device_registry from old YAML config`

- [x] 2. Backend: DeviceCreate model + DeviceRepository CRUD methods + typed row dispatcher + config fix
  What to do:
  - Create `DeviceCreate` Pydantic model in `app/models/device_registry.py` for NON-LIGHT devices only. Fields: device_type (str), room (str), display_name (str), channel (int, 0-15, relay channel), pid_enabled (bool, default False), interlock_with (list[str], default []), pid_setpoints (dict, default {}). Lights reuse existing `LightDeviceCreate` (device_registry.py:57-68) — do NOT create a DeviceCreate for lights.
  - Create `DeviceUpdate` Pydantic model — partial update fields for non-light devices: channel?, display_name?, pid_enabled?, interlock_with?, pid_setpoints?. Room is NOT updatable for non-lights (prevents device_name change, avoids cascade complexity). Lights use existing `LightDeviceUpdate` (device_registry.py:71-85).
  - Create `_UI_TO_DB_DEVICE_TYPES` dict in `app/models/device_registry.py`: {"heater": "heating", "dehumidifier": "dehumidifier", "extraction fan": "exhaust", "fan": "cooling", "humidifier": "humidifier", "co2 tank": "co2", "light": "light"}. Do NOT use `_canonicalize_device_types` from config.py.
  - Add `device_name` pattern regex `r"^[a-z]+_[fvlo]_\d+$"` to the `Device` model (device_registry.py:20) — matches the format `{type}_{prefix}_{index}`.
  - Add `DeviceRepository.create_device()` method — inserts a non-light device into `device_registry` table. Auto-generate `device_name` as `{canonical_type}_{room_prefix}_{n}` (e.g. `heating_f_1`) using `_room_prefix()` (repositories/devices.py:20-25) and `_generate_device_name()` (new helper). Use `SELECT COUNT(*) ... FOR UPDATE` or a UNIQUE constraint on (device_type, location, per_room_index) to prevent race conditions. Canonicalize device_type via `_UI_TO_DB_DEVICE_TYPES` before insertion.
  - Add `DeviceRepository.update_device()` — updates non-light device fields (channel, display_name, pid_enabled, interlock_with, pid_setpoints). Does NOT update room (preventing device_name change). Handle relay channel reassignment: when channel changes, NULL the old channel on any device that had it (global conflict check).
  - Add `DeviceRepository.delete_device()` — deletes a non-light device from `device_registry`.
  - Add relay channel conflict checking: GLOBALLY reject duplicate channel assignments (WHERE channel = $1, across ALL locations). Reference: `bind_relay()` (repositories/devices.py:491-515) checks globally.
  - Add `_row_to_typed_device()` dispatcher (repositories/devices.py) — returns `LightDevice` for lights (device_type == "light") and `Device` for non-lights, based on the `device_type` column.
  - Modify `update_light()` (repositories/devices.py:368-464) to call `_row_to_typed_device()` instead of `_row_to_light_device()` at lines 454 and 461. This fixes the bug where `LightDevice(device_type="heating")` fails `Literal["light"]` validation.
  - Fix `config.update_device_config()` (config.py:537-590): currently calls `update_light()` for ALL devices (line 583). Fix: check device_type — if "light", call `update_light()`; if non-light, call new `update_device()`.
  - Add `get_device_count_by_type_location()` helper for auto-generating device_name index. Must be race-safe: use `SELECT ... FOR UPDATE` within the create transaction.
  Must NOT do:
  - MUST NOT create a DeviceCreate model for lights — reuse `LightDeviceCreate`.
  - MUST NOT write to YAML config.
  - MUST NOT allow user-provided `device_name` — always auto-generated.
  - MUST NOT allow room updates for non-light devices via `update_device()`.
  - MUST NOT use `_canonicalize_device_types` from config.py — use the new `_UI_TO_DB_DEVICE_TYPES` dict.
  - MUST NOT set relay conflict scope to per-room — conflicts are GLOBAL.
  Must address (Momus findings):
  - C2: new `_UI_TO_DB_DEVICE_TYPES` dict with exact mappings ✓
  - C3: device_name pattern `{canonical_type}_{room_prefix}_{n}` with regex ✓
  - C4+M5: `update_light()` modified to use `_row_to_typed_device()`, `config.update_device_config()` fixed to route non-lights ✓
  - C5: two separate models, LightDeviceCreate reused ✓
  - M2: `Device.device_name` gets pattern validator ✓
  - M4: relay conflict scope is GLOBAL ✓
  - M9: room validation via `_room_prefix()` which raises ValueError for unknown rooms ✓
  - M10: `get_device_count_by_type_location()` race-safe with `FOR UPDATE` ✓
  - M11: room NOT updatable for non-lights — stated explicitly ✓
  Parallelization: Wave A | Blocked by: 1 | Blocks: 3 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/app/models/device_registry.py:14-28` (Device model — add device_name pattern)
  - `Infrastructure/automation-service/app/models/device_registry.py:31-54` (LightDevice model — do NOT modify)
  - `Infrastructure/automation-service/app/models/device_registry.py:57-68` (LightDeviceCreate — reuse for lights)
  - `Infrastructure/automation-service/app/models/device_registry.py:71-85` (LightDeviceUpdate — reuse for lights)
  - `Infrastructure/automation-service/app/repositories/devices.py:13-18` (_ROOM_PREFIXES — room validation)
  - `Infrastructure/automation-service/app/repositories/devices.py:20-25` (_room_prefix — raises ValueError for unknown)
  - `Infrastructure/automation-service/app/repositories/devices.py:28-30` (_generate_light_device_name — reference pattern)
  - `Infrastructure/automation-service/app/repositories/devices.py:33-45` (_row_to_device — non-light converter, reference)
  - `Infrastructure/automation-service/app/repositories/devices.py:48-64` (_row_to_light_device — light converter, has Literal["light"] bug)
  - `Infrastructure/automation-service/app/repositories/devices.py:328-366` (create_light — reference pattern)
  - `Infrastructure/automation-service/app/repositories/devices.py:368-464` (update_light — MODIFY to use _row_to_typed_device at lines 454, 461)
  - `Infrastructure/automation-service/app/repositories/devices.py:491-515` (bind_relay — GLOBAL conflict check reference)
  - `Infrastructure/automation-service/app/repositories/devices.py:539-585` (cascade_device_name_change — existing for lights)
  - `Infrastructure/automation-service/app/config.py:537-590` (update_device_config — FIX to route non-lights to update_device())
  - `Infrastructure/automation-service/app/config.py:60-62` (_DEVICE_TYPE_ALIASES — only heater→heating, do NOT use)
  - `Infrastructure/automation-service/app/config.py:65-124` (_canonicalize_device_types — do NOT use, only aliases heater)
  - `Infrastructure/automation-service/app/config.py:209` (self._device_cache — where invalidate_device_cache goes)
  Acceptance criteria (agent-executable):
  - `.venv/bin/python -m pytest tests/test_device_repository.py -v` → all tests pass.
  - `grep -n "class DeviceCreate" Infrastructure/automation-service/app/models/device_registry.py` → match found.
  - `grep -n "def create_device" Infrastructure/automation-service/app/repositories/devices.py` → match found.
  - `grep -n "def _row_to_typed_device" Infrastructure/automation-service/app/repositories/devices.py` → match found.
  QA scenarios:
  - Happy: create a non-light device (heater) via repo method, verify it appears in get_all_as_hierarchy(), verify relay channel conflict is rejected. Evidence: `.omo/evidence/task-1-centralized-device-table.txt`
  - Failure: attempt duplicate relay channel → rejected. Attempt to read a non-light device via _row_to_light_device → should use _row_to_typed_device instead, no ValidationError. Evidence: `.omo/evidence/task-1-failure-centralized-device-table.txt`
  Commit: Y | feat(devices): DeviceCreate model + DeviceRepository CRUD + typed row dispatcher

- [x] 3. Backend: POST/PUT/DELETE /api/devices/registry unified device endpoint
  What to do:
  - Create `POST /api/devices/registry` route in `app/routes/devices.py` (or new `app/routes/devices_crud.py`). Accepts a request body with `device_type` discriminator: if device_type == "light", validate body as `LightDeviceCreate` and call existing `create_light()` (lights.py:818-854 pattern); if non-light, validate body as `DeviceCreate` (new model) and call new `create_device()`. Canonicalize device_type via `_UI_TO_DB_DEVICE_TYPES` dict before passing to repo. Invalidates `config.invalidate_device_cache()` after creation. Returns the created device.
  - Create `PUT /api/devices/registry/{device_id}` route. If device is a light (check DB device_type), validate body as `LightDeviceUpdate` and call existing `update_light()`. If non-light, validate body as `DeviceUpdate` and call new `update_device()`. For lights: if room or per_room_index changes, call existing `cascade_device_name_change()`. Invalidates cache. Returns updated device.
  - Create `DELETE /api/devices/registry/{device_id}` route. Deletes device from DB. For lights: cascade nulls relay_channel + clears DFR assignment + nullifies schedule references (existing pattern). Invalidates cache. Returns success.
  - Create `GET /api/devices/registry` route — returns ALL devices (light and non-light) as a flat list from `device_registry`. This is a NEW path — do NOT touch existing `GET /api/devices` (devices.py:78-101, returns relay states, consumed by useSensorPolling.ts:79).
  - Add DFR channel conflict checking: reject duplicate (board_id, dimming_channel) for lights. Return HTTP **409** (matching existing lights.py:833-834, NOT 400).
  - Add `invalidate_device_cache()` method to `ConfigLoader` (config.py, which owns `self._device_cache` at line 209). The method: `self._device_cache._device_hierarchy_cache = None; self._device_cache._cache_timestamp = None`. Routes call `config.invalidate_device_cache()` after every create/update/delete.
  - Validate room input against `_room_prefix()` (repositories/devices.py:21-25) — raises ValueError for unknown rooms. Catch and return 400.
  Must NOT do:
  - MUST NOT touch existing `GET /api/devices` (devices.py:78-101) or `GET /api/devices/{location}/{cluster}` (devices.py:104).
  - MUST NOT touch existing `apiClient.getAllDevices()` (api.ts:146-147).
  - MUST NOT write to YAML.
  - MUST NOT modify existing `POST /api/lights` endpoint — it stays for DFR panel operations.
  - MUST NOT remove existing `GET /api/devices/channels` — relay matrix depends on it.
  - MUST NOT use 400 for DFR conflicts — use 409.
  Must address (Momus findings):
  - C1: new `/registry` path, existing `GET /api/devices` untouched ✓
  - M1: `invalidate_device_cache()` on ConfigLoader with exact mechanism ✓
  - M3: DFR conflict returns 409 ✓
  - M7: old endpoints documented as "GET functional, POST broken/deprecated" ✓
  - M8: useSensorPolling.ts:79 consumer not affected — different path ✓
  Parallelization: Wave A | Blocked by: 2 | Blocks: 4, 6 | Can parallelize with: —
  References:
  - `Infrastructure/automation-service/app/routes/devices.py:78-101` (GET /api/devices — existing, DO NOT TOUCH)
  - `Infrastructure/automation-service/app/routes/devices.py:475-529` (GET /api/devices/channels — existing, keep)
  - `Infrastructure/automation-service/app/routes/devices.py:532-632` (POST /api/devices/channels/{channel} — broken, deprecated)
  - `Infrastructure/automation-service/app/routes/lights.py:818-854` (POST /api/lights — reference for creation pattern)
  - `Infrastructure/automation-service/app/routes/lights.py:824-839` (DFR conflict check — returns 409)
  - `Infrastructure/automation-service/app/routes/lights.py:841-845` (per_room_index auto-gen pattern)
  - `Infrastructure/automation-service/app/config.py:209` (self._device_cache — where invalidate goes)
  - `Infrastructure/automation-service/app/config.py:289-307` (get_devices — DB-backed, cache)
  - `Infrastructure/automation-service/app/config.py:496-535` (write_full_config — STRIPS devices section)
  - `Infrastructure/automation-service/app/config.py:537-590` (update_device_config — fix to route non-lights)
  - `Infrastructure/automation-service/app/engine_config_cache.py:15-61` (EngineConfigCache — no invalidation method, add one via ConfigLoader)
  - `Infrastructure/frontend/src/hooks/useSensorPolling.ts:79` (existing GET /api/devices consumer — MUST NOT break)
  Acceptance criteria (agent-executable):
  - `.venv/bin/python -m pytest tests/test_device_crud_endpoint.py -v` → all tests pass.
  - `grep -n "POST.*api/devices" Infrastructure/automation-service/app/routes/devices_crud.py` (or devices.py) → match found.
  QA scenarios:
  - Happy: curl POST /api/devices with a heater body → 200, device appears in GET /api/devices. curl POST /api/devices with a light body (board_id, dimming_channel) → 200, device appears in GET /api/devices AND GET /api/devices/channels. Evidence: `.omo/evidence/task-2-centralized-device-table.txt`
  - Failure: duplicate relay channel → 400. Duplicate DFR (board_id, dimming_channel) → 400. Invalid device_type → 400. Evidence: `.omo/evidence/task-2-failure-centralized-device-table.txt`
  Commit: Y | feat(devices): unified POST/PUT/DELETE /api/devices endpoint

- [x] 4. Frontend: DeviceTable component + apiClient methods + DeviceRegistryEntry type
  What to do:
  - Create new `DeviceRegistryEntry` type in `Infrastructure/frontend/src/types/device.ts`. Fields: `{ device_id: number, device_type: string, device_name: string, display_name: string | null, location: string, cluster: string, channel: number | null (relay), board_id?: number | null, dimming_channel?: number | null, per_room_index?: number | null, pid_enabled?: boolean, interlock_with?: string[], pid_setpoints?: Record<string, number> }`. Do NOT modify the existing `Device` type (device.ts:3-11) — it is relay-state-shaped.
  - Create `Infrastructure/frontend/src/components/devices/DeviceTable.tsx`.
  - Fetches devices via new `apiClient.getDeviceRegistry()` (calls `GET /api/devices/registry` — NOT `GET /api/devices` which returns relay states).
  - Renders a table with columns: Device Name | Type | Room | Relay Ch | DFR Board | DFR Channel | Actions.
  - DFR Board and DFR Channel columns are conditionally shown — only for rows where device_type === "light". For non-light rows, these columns show "-".
  - "Add device" button at the bottom of the table. Clicking adds a new empty row with inline form fields:
    - Room: dropdown (Flower Room, Veg Room, Lab) from `ZONES` config (zones.ts — excludes Outside, which is fine since the backend `_ROOM_PREFIXES` supports it but the frontend doesn't expose it).
    - Device Type: dropdown (heater, dehumidifier, extraction fan, fan, humidifier, co2 tank, light) from `DEVICE_TYPES` (relay.ts:1-9).
    - Display Name: text input (user-enterable — this is NOT device_name, it's display_name).
    - Relay Channel: dropdown (R1-R16, or unassigned) — maps to MCP channel 0-15.
    - If Device Type = "light": also show DFR Board (dropdown 0/1/2) + DFR Channel (dropdown 0/1).
  - On Save: calls `apiClient.createDevice()` with the form data. The backend auto-generates `device_name` and canonicalizes `device_type`. The frontend only sends `display_name`, `device_type` (UI type), `room`, `relay_channel` (as `channel`), and if light: `board_id`, `dimming_channel`.
  - Inline editing: click any existing row to edit it. Same fields. On save: calls `apiClient.updateDevice(device_id, body)`.
  - Delete: trash icon per row. Confirmation popup. Calls `apiClient.deleteDevice(device_id)`.
  - Add to `apiClient` in `services/api.ts`:
    - `createDevice(body: { display_name, device_type, room, channel? }): Promise<DeviceRegistryEntry>` — POST /api/devices/registry
    - `createLight(body: { display_name, room, board_id, dimming_channel, channel? }): Promise<DeviceRegistryEntry>` — POST /api/devices/registry (with device_type="light")
    - `updateDevice(device_id: number, body: Partial<...>): Promise<DeviceRegistryEntry>` — PUT /api/devices/registry/{device_id}
    - `deleteDevice(device_id: number): Promise<{ success: boolean }>` — DELETE /api/devices/registry/{device_id}
    - `getDeviceRegistry(): Promise<DeviceRegistryEntry[]>` — GET /api/devices/registry
  - Do NOT modify existing `getAllDevices()` (api.ts:146-147) — it calls `GET /api/devices` (relay states) and is consumed by `useSensorPolling.ts:79`.
  Must NOT do:
  - MUST NOT create a `dfrViewModel.ts` or similar new module.
  - MUST NOT modify `RelayChannelMatrix.tsx`.
  - MUST NOT modify the existing `Device` type (device.ts:3-11).
  - MUST NOT modify existing `getChannels()` or `getRelayBoardState()` or `getAllDevices()` API methods.
  Must address (Momus findings):
  - C1: new `/registry` path, existing `getAllDevices()` untouched ✓
  - M2: new `DeviceRegistryEntry` type, existing `Device` type untouched ✓
  - M8: `useSensorPolling.ts:79` not affected — new method `getDeviceRegistry()` hits different path ✓
  - M13: DeviceTable does not handle DFR-specific operations — those stay in DfrBoardsPanel ✓
  - M16: `getDeviceRegistry()` uses new endpoint, not `GET /api/devices/channels` ✓
  Parallelization: Wave B | Blocked by: 3 | Blocks: 6 | Can parallelize with: 5
  References:
  - `Infrastructure/frontend/src/types/device.ts:3-11` (existing Device type — relay-state-shaped, DO NOT MODIFY)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:599-832` (existing relay table + relay matrix layout — the table section is what gets replaced)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:85-259` (existing edit form logic — reference for inline editing pattern)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` (reference for DFR board/channel dropdown UI)
  - `Infrastructure/frontend/src/services/api.ts:146-147` (existing getAllDevices — DO NOT MODIFY)
  - `Infrastructure/frontend/src/services/api.ts:196-229` (createLight, updateLight, deleteLight — reference for API method pattern)
  - `Infrastructure/frontend/src/services/api.ts:231-254` (getLightsByRoom — reference, uses GET /api/devices/channels)
  - `Infrastructure/frontend/src/types/relay.ts:1-9` (DEVICE_TYPES constant)
  - `Infrastructure/frontend/src/config/zones.ts:34-36` (ZONES — room options, excludes Outside)
  - `Infrastructure/frontend/src/types/relay.ts:33-35` (RelayChannelsResponse — reference)
  - `Infrastructure/frontend/src/hooks/useSensorPolling.ts:79` (existing getAllDevices consumer — MUST NOT break)
  Acceptance criteria (agent-executable):
  - `cd Infrastructure/frontend && npx vitest run src/components/devices/__tests__/DeviceTable.test.tsx` → all tests pass.
  - `grep -n "createDevice\|updateDevice\|deleteDevice\|getAllDevices" Infrastructure/frontend/src/services/api.ts` → 4 matches.
  QA scenarios:
  - Happy: render DeviceTable with mock data, verify table shows rows with conditional DFR columns for lights. Click "Add device", fill form, verify createDevice called. Click row, edit, save, verify updateDevice called. Click delete, confirm, verify deleteDevice called. Evidence: `.omo/evidence/task-3-centralized-device-table.txt`
  - Failure: submit form with empty display_name → validation error. Submit light without DFR board → validation error. Evidence: `.omo/evidence/task-3-failure-centralized-device-table.txt`
  Commit: Y | feat(frontend): DeviceTable component with inline CRUD

- [x] 5. Frontend: Remove DFR panel "Add light" button and form state
  What to do:
  - In `DfrBoardsPanel.tsx`, remove:
    - The `+ Add light` button (line 801-809).
    - The `openAddForm()` function (line 256-266).
    - The `closeAddForm()` function (line 268-280).
    - The `updateAddDraft()` function (line 282-297).
    - The `validateAddIndex()` function (line 299-311).
    - The `submitAddLight()` function (line 313-347).
    - The `addDraftBySlot` state (line 68).
    - The `indexErrorBySlot` state (line 70).
    - The `AddLightDraft` type (line 41-45).
    - The add form JSX (lines 752-806).
  - Keep intact:
    - DFR channel assignment dropdown (line 584-596).
    - Light rename inline input (line 649-671).
    - Light edit form (line 600-646).
    - Light test button (line 682-690).
    - Light remove button + confirmation (line 722-738).
  - Update any tests in `DfrBoardsPanel.test.tsx` that reference the "Add light" button to remove those test cases or update them to verify the button is absent.
  Must NOT do:
  - MUST NOT modify the DFR channel assignment, test, or remove functionality.
  - MUST NOT modify `apiClient.createLight()` — it's still used by the new DeviceTable for light creation (or the new `createDevice()` covers it — either way the method stays).
  Must address (Metis findings):
  - M13: DFR panel keeps assignment, rename, edit, test, remove ✓
  Parallelization: Wave B | Blocked by: — | Blocks: 6 | Can parallelize with: 4
  References:
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:41-45` (AddLightDraft type to remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:68` (addDraftBySlot state to remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:70` (indexErrorBySlot state to remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:256-347` (add form functions to remove)
  - `Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx:752-809` (add form JSX + button to remove)
  - `Infrastructure/frontend/src/components/devices/__tests__/DfrBoardsPanel.test.tsx` (tests to update — the "pre-fills per_room_index" test at line 51-67 references add-btn and must be removed or updated)
  Acceptance criteria (agent-executable):
  - `cd Infrastructure/frontend && npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` → all remaining tests pass.
  - `grep -n "add-btn\|Add light\|addDraft\|AddLightDraft\|submitAddLight\|openAddForm" Infrastructure/frontend/src/components/devices/DfrBoardsPanel.tsx` → no matches.
  QA scenarios:
  - Happy: render DfrBoardsPanel, verify no "Add light" button exists on empty slots. Verify assignment dropdown, edit, test, remove still work. Evidence: `.omo/evidence/task-4-centralized-device-table.txt`
  - Failure: grep for "Add light" in DfrBoardsPanel.tsx → should return no matches. Evidence: `.omo/evidence/task-4-failure-centralized-device-table.txt`
  Commit: Y | refactor(dfr): remove Add light button from DFR panel

- [x] 6. Integration: wire DeviceTable into DeviceManager, replace relay channel table
  What to do:
  - In `DeviceManager.tsx`, replace the "Channel Assignment Table" section (lines 633-816) with `<DeviceTable />`.
  - Keep the `RelayChannelMatrix` section (lines 818-832) as-is.
  - Keep the `DfrBoardsPanel` (line 601) as-is (now without Add light).
  - Keep the relay board status badge (lines 620-623) — it reads from `relayState` which is independent.
  - Remove the now-unused relay table state and logic from DeviceManager:
    - `editing` / `editForm` / `saving` / `isClearingEdit` state (only if no longer needed by the matrix).
    - `startEdit()` / `cancelEdit()` / `saveEdit()` / `clearChannelRow()` / `hasPendingChanges` — if the matrix doesn't use them.
    - `persistedChannelMap` / `displayChannels` / `displayChannelMap` — if the matrix doesn't use them.
  - KEEP the relay matrix's dependencies: `relayChannels` / `statusByChannel` / `relayState` / `refreshRelayState()` / `nowMs` — the matrix still needs these. The matrix builds view models from `channels` (from `getChannels()` → `GET /api/devices/channels`).
  - So `getChannels()` and `relayState` polling STAY. Only the table HTML and table-specific edit logic is removed.
  - The relay matrix's `onSelectChannel` callback currently calls `openEditFromRelayBox()` which scrolls to the table row. With the table gone, this should either be removed or repurposed (e.g., scroll to the DeviceTable row for the same device).
  Must NOT do:
  - MUST NOT remove `getChannels()` polling — the relay matrix depends on it.
  - MUST NOT remove `getRelayBoardState()` polling — the relay matrix depends on it.
  - MUST NOT modify `RelayChannelMatrix.tsx`.
  Must address (Metis findings):
  - M7: old endpoints remain functional (GET /api/devices/channels still polled for matrix) ✓
  - M8: coupling between table and matrix is removed — matrix uses its own state, DeviceTable uses its own ✓
  Parallelization: Wave C | Blocked by: 3, 4, 5 | Blocks: F1-F4 | Can parallelize with: —
  References:
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:599-632` (devices tab header — keep)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:633-816` (channel assignment table — REPLACE with DeviceTable)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:818-832` (relay matrix — KEEP as-is)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:85-259` (table-specific state + edit logic — remove table-only parts, keep matrix parts)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:261-287` (relayState polling — KEEP)
  - `Infrastructure/frontend/src/components/DeviceManager.tsx:378-384` (openEditFromRelayBox — update or remove)
  Acceptance criteria (agent-executable):
  - `cd Infrastructure/frontend && npx tsc --noEmit` → zero errors.
  - `cd Infrastructure/frontend && npx vitest run` → all tests pass.
  - Visual: load the Devices & Relays page, verify DeviceTable is visible, relay matrix is visible, DFR panel is visible. No relay channel table.
  QA scenarios:
  - Happy: load DeviceConfig page, DeviceTable renders with devices, relay matrix shows live state, DFR panel shows DFR boards without "Add light" button. Evidence: `.omo/evidence/task-5-centralized-device-table.txt`
  - Failure: remove DeviceTable import → tsc error. Evidence: `.omo/evidence/task-5-failure-centralized-device-table.txt`
  Commit: Y | feat(frontend): replace relay table with DeviceTable in DeviceManager

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — APPROVED with M10 gap noted (FOR UPDATE missing, but UNIQUE constraint on (location, cluster, device_name) prevents duplicate data). All other findings C1-C5, M1-M9, M11 verified.
- [x] F2. Code quality review — PASSED: pytest 62 passed, vitest 79 passed, ruff clean, no new pyright errors on changed files, no bare excepts, no console.log in new code.
- [x] F3. Real manual QA — PASSED: (1) DeviceTable visible, (2) POST heater creates device, (3) POST light with DFR works, (4) duplicate relay → 409, (5) duplicate DFR → 409, (6) PUT updates display_name, (7) DELETE removes device, (8) GET /api/devices still works, (9) logs clean.
- [x] F4. Scope fidelity — PASSED: relay matrix untouched, no YAML writes, no auto-migration, device_name auto-generated, GET /api/devices/channels preserved, control loop untouched, _canonicalize_device_types not used, DFR conflict returns 409.

## Commit strategy
- Wave 0: Task 1 (seed) — data seeding, no code commit unless SQL script created.
- Wave A: Tasks 2-3 (backend) — sequential (3 depends on 2). Commit independently when tests pass.
- Wave B: Tasks 4-5 (frontend) — parallel. Commit independently.
- Wave C: Task 6 (integration) — after Wave B. Single commit.
- Deploy after all merge (single deploy with deploy.sh). Lights come back on after Wave 0 seed + service restart.

## Success criteria
1. `GET /api/devices/registry` returns all devices (light and non-light) from device_registry DB.
2. `POST /api/devices/registry` creates any device type in DB — heater, fan, dehumidifier, light.
3. `POST /api/devices/registry` rejects duplicate relay channels (GLOBAL conflict) and duplicate DFR (board, channel) for lights (returns 409).
4. `GET /api/devices` (existing, relay states) still works — `useSensorPolling.ts:79` unaffected.
5. `config.update_device_config()` no longer crashes for non-light devices — routes to `update_device()`.
6. `update_light()` returns typed models (LightDevice for lights, Device for non-lights) — no Pydantic `Literal["light"]` crash.
7. DeviceTable component shows all devices with inline edit + add + delete.
8. DFR panel has no "Add light" button — only assignment, edit, test, remove.
9. Relay matrix shows live ON/OFF state independently.
10. All tests pass: backend pytest + frontend vitest.
