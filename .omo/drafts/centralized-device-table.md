# Draft: centralized-device-table

## Status
`status: approved` — user approved all three recommended defaults
Decisions:
1. REPLACE existing relay table with new device table
2. New unified POST /api/devices endpoint (writes to device_registry for all types)
3. Remove DFR panel "Add light" button

Pending action: write `.omo/plans/centralized-device-table.md` (scaffolded, awaiting Metis)

## Goal
Replace the fragmented device-creation UX (DFR panel "Add light" + relay channel assignment) with a single centralized device table on the DeviceConfig page. Each row = one device. "Add device" button adds a new row. Fields: room, device type, device name, relay channel (R#), and if light: DFR board + DFR channel.

## What I found (evidence with paths)

### Current architecture — TWO separate device management surfaces

**Surface 1: Relay channel table** (`DeviceManager.tsx`)
- Channel-centric: 16 fixed rows (R1-R16), each assignable to a device
- Inline editing: click a row → edit form with device_name, device_type, location, cluster, light_name
- API calls: `GET /api/devices/channels`, `POST /api/devices/channels/{channel}`, `DELETE /api/devices/channels/{channel}`
- Also shows live relay state (ON/OFF, elapsed time) via `getRelayBoardState()` polling

**Surface 2: DFR boards panel** (`DfrBoardsPanel.tsx`)
- DFR-centric: shows DFR0971 boards with 2 channel slots each
- "Add light" button opens form with room, display_name, per_room_index
- API calls: `GET /api/lights/dfr/assignments`, `POST /api/lights`, `PUT /api/lights/{id}`, `DELETE /api/lights/{id}`

### CRITICAL: Non-light device creation writes to YAML, not DB
- `POST /api/devices/channels/{channel}` (`devices.py:533-632`) writes to `automation_config.yaml` via `config.write_full_config()` + `config.reload()`
- It does NOT write to the `device_registry` DB table
- But `config.get_devices()` was just flipped to read from `device_registry` DB (commit `8884bae`)
- The seed migration `009_seed_device_registry_from_yaml.py` never ran → `device_registry` table is EMPTY
- **This means**: non-light devices created via the existing YAML endpoint are invisible to the control loop (which reads from DB)

### Backend models
- `Device` (device_registry.py:14) — non-light devices. Fields: device_type, channel (relay), pid_enabled, interlock_with, pid_setpoints, display_name, device_name, location, cluster
- `LightDevice` (device_registry.py:31) — light devices. Fields: device_id, board_id, dimming_channel, relay_channel, safety_level, per_room_index, display_name, device_name, location, cluster
- `LightDeviceCreate` (device_registry.py:57) — for creating lights via `POST /api/lights`
- No `DeviceCreate` model exists — there's no endpoint to create non-light devices in the DB

### DeviceRegistryRepository
- `app/repositories/devices.py` has `_row_to_device()` and `_row_to_light_device()` converters
- Has `rename_and_regenerate_device_name()` for lights
- Has `cascade_device_name_change()` for lights
- No `create_device()` or `create_non_light_device()` method exists

### Web research findings (best practices)
- Inline editing is fastest for tables with simple fields (fewer than 8 columns) — our table has 5-7 columns
- "Add row" button at the bottom of the table is the standard CRUD pattern
- Conditional columns (show DFR fields only when device_type = "light") reduces visual noise
- Sticky footer with "Add" button keeps the action discoverable
- Keyboard navigation (Tab between fields, Enter to save, Escape to cancel) is expected

## Components (topology lock)
- **C1 — Backend: unified device creation API** — new `POST /api/devices` endpoint that writes to `device_registry` table for ALL device types (lights and non-lights). Mirrors the existing `POST /api/lights` pattern. Also: `PUT /api/devices/{device_id}` for updates, `DELETE /api/devices/{device_id}` for removal.
- **C2 — Frontend: centralized device table component** — new `DeviceTable.tsx` component on the DeviceConfig page. Device-centric rows (not channel-centric). "Add device" button. Inline editing. Conditional DFR fields for lights. Uses the new `POST/PUT/DELETE /api/devices` endpoints.
- **C3 — Frontend: integrate into DeviceConfig page** — place the new table on the DeviceConfig page (DeviceManager.tsx), replacing or coexisting with the existing relay channel table.
- **C4 — Backend: fix non-light device persistence** — ensure non-light device creation writes to `device_registry` DB, not YAML. The existing `POST /api/devices/channels/{channel}` endpoint should either be migrated or deprecated.

## Open questions (owner-decisions, asked with WHY)
1. **Replace or coexist with existing relay table?** The existing relay table shows live ON/OFF/elapsed state. The new device table is for CRUD. If replace: live state moves to relay matrix only. If coexist: two tables on one page.
2. **Backend API approach for non-light devices?** Need a new `POST /api/devices` endpoint that writes to device_registry (like `POST /api/lights` does for lights)?
3. **DFR panel "Add light" removal?** Should DFR panel keep its "Add light" button, or should all device creation go through the new table?

## Approval gate
Awaiting user approval to write `.omo/plans/centralized-device-table.md`.
