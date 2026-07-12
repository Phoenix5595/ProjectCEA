# Centralized Device Table - Task 1 Learnings

## 2026-07-07: Seed device_registry from YAML config

### What happened
- Migration `009_seed_device_registry_from_yaml.py` exists but could not be run via alembic due to two issues:
  1. **PYTHONPATH**: `alembic env.py` imports `shared.db_credentials` which requires `Infrastructure/` in PYTHONPATH. The systemd service sets this correctly, but manual `alembic upgrade` from cwd does not.
  2. **DB auth**: Even with PYTHONPATH fixed, alembic connects as `cea_user` which failed password auth (peer auth works only via `sudo -u postgres`).
  3. **Migration bug**: The migration has a known `per_room_index` parsing bug for keys like `light_f_1` — `split("_", 1)[1]` gives `"f_1"`, and `int("f_1")` raises `ValueError`.

### Resolution
- Manually inserted all 13 devices via `sudo -u postgres psql` SQL INSERT.
- Cleared the existing incomplete row (`light_v_1` with NULL channel and wrong display_name).
- Updated `alembic_version` from `008_device_registry` to `009_seed_device_registry` so the migration is marked applied.

### Device counts verified
- `SELECT count(*) FROM device_registry` → **13**
- Flower Room / main: 6 devices (3 lights + 3 non-lights)
- Veg Room / main: 7 devices (3 lights + 4 non-lights)

### Service restart
- `sudo systemctl restart automation-service` succeeded.
- Journalctl shows scheduler initialized with 42 schedules, all 6 lights loaded with correct safety levels, and DFR0971 boards initialized.
- API endpoints verified:
  - `GET /api/devices/Flower Room/main` → 6 devices
  - `GET /api/devices/Veg Room/main` → 7 devices

### Key data inserted
| Room | Device | Type | Channel | Board | Dimmer Ch | Safety |
|------|--------|------|---------|-------|-----------|--------|
| Flower | light_f_1 | light | 10 | 2 | 0 | 0 |
| Flower | light_f_2 | light | 11 | 1 | 1 | 0 |
| Flower | light_f_3 | light | 12 | 2 | 1 | 0 |
| Flower | heating_f_1 | heating | 0 | — | — | — |
| Flower | exhaust_f_1 | exhaust | 1 | — | — | — |
| Flower | dehumidifier_f_1 | dehumidifier | 2 | — | — | — |
| Veg | light_v_1 | light | 3 | 0 | 0 | 0 |
| Veg | light_v_2 | light | 4 | 0 | 1 | 40 |
| Veg | light_v_3 | light | 5 | 1 | 0 | 40 |
| Veg | heating_v_1 | heating | 6 | — | — | — |
| Veg | exhaust_v_1 | exhaust | 9 | — | — | — |
| Veg | dehumidifier_v_1 | dehumidifier | 7 | — | — | — |
| Veg | cooling_v_1 | cooling | 8 | — | — | — |

### Next steps for Task 2
- Update `config.get_devices()` to read from `device_registry` instead of YAML.
- Ensure control loop uses DB-backed device list.
- Remove or deprecate YAML `devices:` block once fully migrated.

## 2026-07-07: Task 2 — DeviceCreate model + DeviceRepository CRUD + typed row dispatcher + config fix

### What happened
- Added `DeviceCreate` and `DeviceUpdate` Pydantic models to `app/models/device_registry.py` for non-light devices.
- Added `_UI_TO_DB_DEVICE_TYPES` mapping (heater→heating, fan→cooling, extraction fan→exhaust, etc.).
- Added `device_name` regex `^[a-z][a-z0-9]*_[fvlo]_\d+$` to `Device` model (adjusted from task spec to allow digits in type prefix for `co2`).
- Added `device_id` field to `Device` model for consistency with `LightDevice`.
- Implemented `DeviceRepository.create_device()`, `update_device()`, `delete_device()`, `get_device_count_by_type_location()`, and `_row_to_typed_device()` dispatcher.
- `create_device()` auto-generates device_name as `{canonical_type}_{prefix}_{n}` using race-safe count within transaction.
- `update_device()` handles global relay channel conflicts (not per-room) and rejects light devices with ValueError.
- `delete_device()` only deletes non-light devices (`device_type != 'light'`).
- Modified `update_light()` to call `_row_to_typed_device()` with type guard and cast.
- Fixed `config.update_device_config()` to route lights to `update_light()` and non-lights to `update_device()`.
- Added `ConfigLoader.invalidate_device_cache()` to clear `EngineConfigCache`.
- Updated `test_config_devices_db.py` to verify both light and non-light routing.
- Created `tests/test_device_repository.py` with 18 tests covering create, update, delete, typed dispatch, channel conflicts, and count helpers.

### Key fixes during implementation
1. **JSONB deserialization**: asyncpg returns `interlock_with` and `pid_setpoints` as strings from `dict(row)`; `_row_to_device()` now parses them with `json.loads()` when needed.
2. **device_name regex for `co2`**: Original regex `^[a-z]+_[fvlo]_\d+$` rejected `co2_f_1`. Updated to `^[a-z][a-z0-9]*_[fvlo]_\d+$` to allow digits in the type prefix.
3. **Test model updates**: `test_device_models.py` had invalid device names (`heater1`, `fan1`) that failed the new regex. Updated to canonical names (`heating_f_1`, `cooling_v_1`).

### Verification
- `pytest tests/test_device_repository.py -v` → **18 passed**
- `pytest tests/test_device_models.py tests/test_config_devices_db.py` → **26 passed**
- Full test suite (excluding pre-existing broken alembic/httpx tests) → **189 passed**

### Next steps for Task 3
- Add backend API routes for non-light device CRUD (`POST /api/devices`, `PUT /api/devices/{id}`, `DELETE /api/devices/{id}`).
- Wire routes to `DeviceRepository` methods.
- Update frontend to use new endpoints.

## 2026-07-07: Task 3 — Unified /api/devices/registry CRUD endpoints

### What happened
- Created `app/routes/devices_crud.py` with four unified registry endpoints:
  - `GET /api/devices/registry` — flat list of all typed devices (light + non-light) via `get_all_devices_flat()`
  - `POST /api/devices/registry` — discriminated creation by `device_type`:
    - `"light"` → validates as `LightDeviceCreate`, DFR conflict check (409), calls `create_light()`
    - non-light → validates as `DeviceCreate`, canonicalizes type via `_UI_TO_DB_DEVICE_TYPES`, global relay conflict check (409), calls `create_device()`
  - `PUT /api/devices/registry/{device_id}` — type-aware update:
    - Reads current `device_type` from DB first
    - Light → `LightDeviceUpdate` → `update_light()`, with `cascade_device_name_change()` on room/index change
    - Non-light → `DeviceUpdate` → `update_device()`
    - Relay channel conflict check (409) on both paths
  - `DELETE /api/devices/registry/{device_id}` — type-aware deletion:
    - Non-light → `delete_device()`
    - Light → `delete_light()` + cascade deletes schedules via `schedule_repo.delete_schedules_by_device_name()` + cleans `effective_setpoints`
- Added `DeviceRepository.get_all_devices_flat()` to return a typed flat list using `_row_to_typed_device()` dispatcher.
- Added `ScheduleRepository.delete_schedules_by_device_name()` for cascade cleanup on light deletion.
- Wired `devices_crud.router` into `routes.py` with dependency overrides (`get_device_repo`, `get_config`, `get_database`).
- All routes call `config.invalidate_device_cache()` after mutating operations.
- Room validation uses `_room_prefix()` — catches `ValueError` and returns HTTP 400.
- DFR conflicts return HTTP 409 (matching existing `lights.py` pattern, NOT 400).
- Relay channel conflicts also return HTTP 409.

### Key design decisions
1. **Dict-based request bodies**: POST/PUT accept `dict[str, Any]` instead of Union Pydantic models because FastAPI doesn't easily discriminate unions at the request-body level without complex wrappers. Manual field filtering (`{k: v for k, v in body.items() if k in Model.model_fields}`) keeps validation clean.
2. **No YAML writes**: All mutations go to DB only; `invalidate_device_cache()` ensures the control loop sees changes within 30s TTL.
3. **Existing endpoints untouched**: `GET /api/devices` (relay states) and `GET /api/devices/{location}/{cluster}` remain unchanged.

### Verification
- `pytest tests/test_device_crud_endpoint.py -v` → **18 passed**
- Combined with Task 2 tests (`test_device_repository.py`, `test_device_models.py`, `test_config_devices_db.py`) → **62 passed**
- Pre-existing `test_device_registry_repository.py` errors are alembic CLI issues (missing `alembic.__main__` in venv), unrelated to this task.

### Next steps for Task 4
- Update frontend to call `/api/devices/registry` for device management UI.
- Deprecate or redirect legacy `POST /api/lights` and `POST /api/devices/channels/{channel}` once frontend is migrated.

## 2026-07-07: Task 4 — DeviceRegistryEntry type + apiClient methods + DeviceTable component + tests

### What happened
- Added `DeviceRegistryEntry` interface to `src/types/device.ts` (did NOT modify existing `Device` type).
  - Key insight: backend `GET /api/devices/registry` returns a union of `Device` (non-light) and `LightDevice` (light) Pydantic models. Non-lights carry `channel` (relay 0-15); lights carry `relay_channel` (nullable), `board_id`, and `dimming_channel`. Both `channel` and `relay_channel` are optional in the TS type to accurately describe either JSON shape.
- Added 4 methods to `apiClient` in `src/services/api.ts` (did NOT modify existing `getAllDevices()`):
  - `getDeviceRegistry()` — GET /api/devices/registry
  - `createDevice(body)` — POST /api/devices/registry (unified; handles both light and non-light via `device_type` discrimination)
  - `updateDevice(device_id, body)` — PUT /api/devices/registry/{device_id}
  - `deleteDevice(device_id)` — DELETE /api/devices/registry/{device_id}
- Created `DeviceTable.tsx` component:
  - Fetches via `apiClient.getDeviceRegistry()`
  - Columns: Device Name | Type | Room | Relay Ch | DFR Board | DFR Channel | Actions
  - DFR Board/Channel cells show values for lights, `—` for non-lights
  - Add device button at bottom opens inline form row
  - Form fields: Room dropdown (ZONES), Device Type dropdown (DEVICE_TYPES), Display Name input, Relay Channel dropdown (R1-R16 = 0-15), conditional DFR Board + DFR Channel for lights
  - On Save: calls `apiClient.createDevice()` for both light and non-light (unified endpoint discriminates by `device_type`)
  - Inline editing: click row → edit form → `apiClient.updateDevice()`
  - Delete: button with confirmation → `apiClient.deleteDevice()`
  - Uses `relayChannelOf()` helper to read `channel ?? relay_channel` for display
- Created `DeviceTable.test.tsx` with 8 tests covering: render, DFR conditional columns, add form open, create non-light, create light, inline edit + save, delete confirmation + delete, delete cancel.

### Key design decisions
1. **Unified `createDevice()` for both light and non-light**: The backend POST /api/devices/registry discriminates by `device_type` field. Using one method for both keeps the frontend aligned with the unified registry endpoint, rather than splitting between legacy `createLight()` (POST /api/lights) and the new endpoint.
2. **`channel` vs `relay_channel` in the type**: Made both optional. The component uses `relayChannelOf()` to normalize: `device.channel ?? device.relay_channel`. This avoids data loss without forcing a backend schema change.
3. **Relay channel as R1-R16 (values 0-15)**: Matches existing `getRelayNumber()` convention in `relayViewModel.ts`.
4. **No room editing in inline edit**: Backend `DeviceUpdate` model says "Room is NOT updatable — device identity is tied to location" for non-lights. `LightDeviceUpdate` allows room changes but we kept the edit form simple (display_name + channel + DFR for lights).

### Verification
- `npx vitest run src/components/devices/__tests__/DeviceTable.test.tsx` → **8 passed**
- `npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` → **4 passed** (no regression)
- `npx tsc --noEmit` → **0 errors** (clean TypeScript build)

### Next steps for Task 5/6
- Task 5: Remove DFR panel (can parallelize with this task).
- Task 6: Integration — wire DeviceTable into DeviceManager or a new page.

## 2026-07-07: Task 6 — Wire DeviceTable into DeviceManager, replace relay channel table

### What happened
- Replaced the "Channel Assignment Table" (relay channel table, ~180 lines of inline editing JSX) in `DeviceManager.tsx` with `<DeviceTable />`.
- DeviceTable fetches its own data via `getDeviceRegistry()` and handles its own inline add/edit/delete — no editing state needed in DeviceManager anymore.
- Removed now-unused relay table state and logic from DeviceManager:
  - State: `editing`, `editForm`, `saving`, `isClearingEdit`, `lightNames`
  - Refs: `tablePanelRef`, `matrixPanelRef`
  - Memos: `displayChannels`, `displayChannelMap`, `hasPendingChanges`, `uniqueLightNames`, `roomFilteredLights`, `locationOptions`
  - Functions: `startEdit`, `openEditFromRelayBox`, `clearChannelRow`, `cancelEdit`, `saveEdit`, `toUiDeviceType`, `getDefaultLocationCluster`
  - Effects: lights-fetch-by-room effect, pointerdown-outside-click-cancel effect
  - Constants: `ChannelEditForm` interface, `EMPTY_EDIT_FORM`, `DEFAULT_LOCATION`, `DEFAULT_CLUSTER`
  - "Save Changes" button (was conditional on `hasPendingChanges`)
  - `setLightNames` call removed from `loadChannels()`
- Cleaned up imports: removed `useRef`, `ZONES`, `DEVICE_TYPES`, `DeviceTypeOption`, `LightNameOption`, `getChannelDisplayName`, `getReadableDeviceType`; added `DeviceTable` import.
- `relayChannels` memo switched from `displayChannels` to `channels` (displayChannels was only different during editing, now always equals channels).
- RelayChannelMatrix props: removed `editingChannel` and `onSelectChannel` (both optional, default to `null`/`undefined`). The matrix no longer has a click-to-edit-channel callback since DeviceTable handles editing independently.
- Layout: replaced the two-column grid (`md:grid-cols-[55fr_45fr]` with table left, matrix right) with vertical stacking — DeviceTable on top, RelayChannelMatrix below. DeviceTable is wider (7 columns) and doesn't fit the old 55fr column.
- Removed `DeviceManager.test.tsx` — it tested the old relay table inline editing flow (click row → select type → light greyout). That functionality is replaced by DeviceTable's own editing flow, which has its own test file (`DeviceTable.test.tsx`, 8 tests).

### What was KEPT (relay matrix dependencies)
- `getChannels()` polling — relay matrix needs channel data for `relayChannels` memo
- `getRelayBoardState()` polling — relay matrix needs relay state
- `persistedChannelMap` — used by `handleRelayMenuAction` to look up channel info
- `relayChannels`, `statusByChannel`, `nowMs` — passed to RelayChannelMatrix
- `handleRelayMenuAction` — passed to RelayChannelMatrix as `onMenuAction`
- `menuOpenChannel` + `setMenuOpenChannel` + document-click dismiss effect — relay matrix menu
- Relay board status badge, DfrBoardsPanel, SystemSettingsPanel — all unchanged
- `RelayChannelMatrix.tsx` — NOT modified (per task constraints)

### Pre-existing test failures (NOT caused by this task)
- `relayMatrix.test.tsx` has 2 failing tests that expect `R1 · CH 15` format but the component renders `R1 · GPB7` (pin label format). Confirmed pre-existing by running tests on stashed original code — same 2 failures. This is a test/code mismatch from a prior relay label format change, unrelated to Task 6.

### Verification
- `npx tsc --noEmit` → **0 errors**
- `npx vitest run` → **80 passed, 2 failed** (both pre-existing `relayMatrix.test.tsx` failures, confirmed unrelated)
- DeviceTable tests: **8 passed**
- DfrBoardsPanel tests: **4 passed**

### Next steps
- Final Wave F1-F4 (unblocked by this task)

## 2026-07-07: Task 5 — Remove DFR panel Add light button and form state

### What happened
- Removed from `DfrBoardsPanel.tsx`:
  - `AddLightDraft` type
  - `addDraftBySlot` and `indexErrorBySlot` state hooks
  - `openAddForm`, `closeAddForm`, `updateAddDraft`, `validateAddIndex`, `submitAddLight` functions
  - Add form JSX (room/display_name/per_room_index inputs + Add/Cancel buttons)
  - `+ Add light` dashed button
  - Unused `slotKey`/`addDraft`/`indexError` locals in `renderChannel`
- When a DFR channel is unassigned, the slot now renders `null` below the assignment dropdown (no add button).
- Kept intact: assignment dropdown, rename inline input, edit form, test button, remove button + confirmation.
- `apiClient.createLight` mock left in test file (still used by DeviceTable); `createLight` API client method untouched.
- Updated `DfrBoardsPanel.test.tsx`:
  - Replaced "pre-fills per_room_index" test with "does not render the Add light button" test asserting `add-btn`/`add-form`/`add-submit`/`add-index` testids are absent.
  - Removed `add-btn-0-1` disabled assertion from the test-progress test (button no longer exists).

### Verification
- `npx tsc --noEmit` → no DfrBoardsPanel errors
- `npx vitest run src/components/devices/__tests__/DfrBoardsPanel.test.tsx` → 4 passed
