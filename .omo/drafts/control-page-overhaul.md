---
slug: control-page-overhaul
status: awaiting-approval
intent: clear
pending-action: write .omo/plans/control-page-overhaul.md
approach: 3-wave plan — Wave 1: fix DB (create missing tables + seed), Wave 2: fix frontend components (relay matrix display names, badge sizing, relay steal, real-time update, timeline bugs), Wave 3: deploy + verify
---

# Draft: control-page-overhaul

## Components (topology ledger)
| id | outcome | status | evidence |
|----|---------|--------|----------|
| DB-fix | Create light_target_intensity + light_programs tables directly via SQL, seed from mode_parameters | active | DB missing both tables; alembic_version=010 but 03f/04f never ran |
| Relay-matrix-names | RelayChannelBox uses display_name for ALL devices, not just lights | active | relayViewModel.ts:114-124 getChannelDisplayName returns device_name for non-lights |
| Badge-sizing | OFF/AUTO/ON badges 2.5x bigger | active | RelayChannelBox.tsx:164 text-[8px] needs scaling up |
| Relay-steal | Allow stealing relay # from another device, null displaced device, red outline | active | devices_crud.py:289-305 raises 409 on conflict; DeviceTable submitEdit needs error handling |
| Realtime-matrix | Relay matrix updates in real-time after device table change | active | ZoneConfig loadChannels only runs on mount; DeviceTable onRefresh doesn't reload channels |
| Light-10pct-bug | Lights show 10% instead of mid-ramp targets | active | light_target_intensity table missing → scheduler falls back to MINIMUM_LIGHT_INTENSITY=10.0 |
| Light-target-fail | "Light target update failed for: light_v_1" error | active | set_target_intensity (lights.py:766) writes to light_target_intensity which doesn't exist |
| Timeline-moon-edit | Can't change moon end time on timeline | active | ClimatePeriodTimeline.tsx handle edge handles swapped: night-start uses handleEdgeMouseDown('end'), night-end uses handleEdgeMouseDown('start') |
| Timeline-right-click | Right-click should edit start/end time | active | Right-click only opens ramp popover (line 311-317), no time editing |
| Timeline-photoperiod-lock | Photoperiod not locked at 6h moon max for veg | active | lockedPhotoperiodHours passed but veg=18h sun, moon=6h; lock logic in handleEdgeMouseDown and handleMouseMove seems correct but may not be wired |
| Timeline-ramp-overlap | Ramp times overlap with moon start/end | active | Ramp gradients rendered relative to sun band edges (lines 339-358), not correctly positioned relative to moon |

## Open assumptions (announced defaults)
- User explicitly rejected alembic migrations: "delete all migration tests and just edit the DB directly"
- Direct SQL table creation is acceptable
- All light target intensities default to mode_parameters.main_light_intensity value

## Findings (cited)
- `sudo -u postgres psql -d cea_sensors -c "\dt" | grep light_target` → TABLES DO NOT EXIST
- `sudo -u postgres psql -d cea_sensors -c "SELECT version_num FROM alembic_version;"` → 010_canonicalize_device_names (but 03f/04f never ran)
- `curl -s ... /api/lights/Veg%20Room/main/zone-status` → intensity=10, target_intensity=10.0, scheduler_effective_intensity=10.0
- `journalctl -u automation-service` → "Failed to load light targets for Veg Room/main: relation 'light_target_intensity' does not exist" (every 5s)
- `scheduler.py:454`: `target_intensity = self._light_intensities.get((device_id, active_mode_id))` → returns None → MINIMUM_LIGHT_INTENSITY (10.0)
- `lights.py:805`: `ok = await database.light_target_intensity_repo.set_intensity(...)` → fails because table doesn't exist → 500
- `relayViewModel.ts:114-124`: `getChannelDisplayName()` returns `channel.device_name` for non-lights (canonical name like `heater_f_1`), NOT `display_name`
- `RelayChannelBox.tsx:84`: `const deviceLabel = channel.deviceName || 'Unassigned'` — uses `deviceName` from view model
- `RelayChannelBox.tsx:164`: badge button `text-[8px]` — needs 2.5x → `text-[20px]` or similar
- `devices_crud.py:289-305`: non-light update raises 409 when channel already occupied
- `devices_crud.py:225-241`: light update raises 409 when relay_channel already occupied
- `ZoneConfig.tsx:124-126`: `loadChannels()` only runs on mount via useEffect
- `ClimatePeriodTimeline.tsx:326-337`: night-start handle calls `handleEdgeMouseDown('end')` and night-end calls `handleEdgeMouseDown('start')` — swapped
- `ClimatePeriodTimeline.tsx:311-317`: right-click (onContextMenu) only opens ramp popover, no time editing

## Decisions (with rationale)
- Skip alembic, create tables directly via SQL (user's explicit decision)
- Delete migration test files (user's explicit decision)
- Badge 2.5x: scale from text-[8px] to text-[20px] (2.5x = 20px)
- Relay steal: change 409 to allow, null out displaced device's channel, return info about displacement
