# dfr-panel-cleanup - Draft

## Status: plan-written (Metis findings folded in)

## Intent routing: CLEAR

## Components (topology lock)
1. **Backend fix**: `LightDeviceUpdate` model + `update_registry_device()` — add `dimming_board_id`/`dimming_channel` fields so DeviceTable can persist DFR assignments
2. **DeviceTable DFR edit**: Fix the edit form to actually persist DFR board/channel changes through `updateDevice`
3. **DfrBoardsPanel refactor**: Remove assignment dropdown (read-only display), remove duplicate DFR board label, add room badge/header, keep Test + Rename, wire shared refresh
4. **Shared refresh wiring**: DeviceManager passes refresh callback to both DeviceTable and DfrBoardsPanel so rename in either panel refreshes both

## User decisions (resolved)
- Q1: Fix DeviceTable, DFR panel read-only for assignment ✅
- Q2: Board header once, CH0/CH1 per slot (remove repeated DFR0) ✅
- Q3: Shared parent refresh callback ✅
- Q4: Room as badge/header on channel slot ✅

## Key findings from exploration
- DfrBoardsPanel rename: `updateDeviceConfig()` → `POST /api/devices/{loc}/{clust}/{dev}/config` → updates `display_name` in `device_registry`
- DeviceTable rename: `updateDevice()` → `PUT /api/devices/registry/{id}` → updates `display_name` in `device_registry`
- BOTH update the SAME column in the SAME table ✅
- DFR assignment via DfrBoardsPanel: `assignDfrChannel()` → `PUT /api/lights/dfr/assign` → updates `dimming_board_id`/`dimming_channel` in `device_registry` ✅
- DFR assignment via DeviceTable: `updateDevice()` → `PUT /api/devices/registry/{id}` → `LightDeviceUpdate` model does NOT include `dimming_board_id`/`dimming_channel` → fields silently dropped ❌ BUG
- DeviceTable `createDevice()` DOES create lights with DFR fields via `LightDeviceCreate` (has `board_id`/`dimming_channel`) ✅ — only the update path is broken

## Approach
1. Backend: Add `dimming_board_id` and `dimming_channel` to `LightDeviceUpdate` model and `update_registry_device()` handler
2. Frontend DeviceTable: Already sends the fields correctly — once backend accepts them, the edit will work
3. Frontend DfrBoardsPanel: Remove assignment `<select>`, make DFR board/channel display read-only. Replace repeated "DFR0 · CH0" with board header "DFR0" + per-slot "CH0"/"CH1". Add room badge prominently on each assigned slot. Wire shared refresh from parent.
4. Frontend DeviceManager: Pass refresh callbacks to both children. When either saves a rename, call both refreshes.

## Pending action
Write `.omo/plans/dfr-panel-cleanup.md` with appended todos.