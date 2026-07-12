# production-safety — Draft

## Status: awaiting-plan-rewrite
## Pending action: rewrite .omo/plans/production-safety.md with new architecture

## FINAL ARCHITECTURE (after deep exploration + user decisions)

### Problem statement
The schedule system had duplicated data: per-device SUN/MOON rows in `schedules` table were just copies of `mode_parameters` photoperiod. The duplication caused bugs — lights could exist without schedule rows, went dark silently, and the runtime synthesis code was a band-aid that masked the root cause.

### New architecture — single source of truth for each concept

#### Photoperiod (sun/moon window):
- **Source of truth:** `mode_parameters` table (day_start_time, night_start_time)
- **Read by:** Scheduler.is_in_photoperiod() — cached in Scheduler during schedule updates
- **No per-device rows needed** — all lights in a room share the same photoperiod

#### Per-light intensity:
- **Source of truth:** NEW table `light_target_intensity(device_id, mode_id, target_intensity)`
- **Mode-specific:** each light can have different intensity per room mode (veg, flower, drying, sleep)
- **Default:** if no row exists for (device_id, mode_id), default to 100%

#### Ramp durations:
- **Source of truth:** `mode_parameters` (light_ramp_up_minutes, light_ramp_down_minutes)
- **Room-level, not per-device**

#### Climate setpoints (non-light devices):
- **Source of truth:** `climate_periods` table — UNCHANGED
- Already works correctly via ClimatePeriodResolver

### What gets REMOVED:
1. `expand_light_schedules_for_control()` — runtime synthesis deleted entirely
2. Per-device SUN/MOON rows creation in `save_room_schedule()` — no longer needed
3. `room_schedule` device_name rows in `schedules` table — photoperiod comes from mode_parameters
4. Scheduler's dependency on per-device SUN/MOON rows for times and intensity

### What gets CREATED:
1. New `light_target_intensity` table (alembic migration)
2. Migration of existing intensities from schedules → light_target_intensity
3. Repository for light_target_intensity CRUD
4. Scheduler changes: `is_in_photoperiod()` reads cached mode_parameters, `get_schedule_intensity()` reads from light_target_intensity + mode_parameters
5. `save_room_schedule()` stops creating per-device rows, only updates mode_parameters
6. `create_light` / `create_registry_device` create light_target_intensity row for active mode
7. `update_light` intensity endpoint updates light_target_intensity
8. background_tasks/container loads mode_parameters + light_target_intensity into Scheduler
9. AlarmManager: CRITICAL when room has no mode_parameters for active mode, WARNING when light has no intensity row

### Scheduler changes (detailed):
- Add `update_mode_parameters(params: dict)` — caches {location: {cluster: {day_start, night_start, ramp_up, ramp_down}}}
- Add `update_light_intensities(intensities: dict)` — caches {(location, cluster, device_name): {mode_id: target_intensity}}
- `is_in_photoperiod()`: reads from cached mode_parameters instead of self.schedules
- `get_schedule_intensity()`: uses mode_parameters for window + ramps, light_target_intensity for target, is_sun flag for on/off
- Ramp state (_light_ramp_state) stays the same — keyed by (location, cluster, device_name)

### Data flow after redesign:
```
1. Startup: background_tasks loads mode_parameters + light_target_intensity → Scheduler caches them
2. ControlEngine: ClimatePeriodResolver resolves active period (already works for climate setpoints)
3. ControlEngine: calculates is_sun from Scheduler.is_in_photoperiod() (now reads mode_parameters)
4. DeviceProcessor._build_light_decision():
   - If is_sun: Scheduler.get_light_intensity_details() returns (effective %, nominal %, ramp progress)
     - Photoperiod window from cached mode_parameters
     - target_intensity from cached light_target_intensity for (device, active_mode)
     - Ramp logic same as before (durations from mode_parameters)
   - If not is_sun: 0%
5. No schedule_changed events needed for light photoperiod changes (mode_parameters changes handle it)
```

### Menu of todos (will be in plan):
1. Alembic: create light_target_intensity table + migrate existing intensities
2. Repository: light_target_intensity CRUD
3. Scheduler: rewrite is_in_photoperiod + get_schedule_intensity to read mode_parameters + light_target_intensity
4. background_tasks/container: load mode_parameters + light_target_intensity into Scheduler on startup + on changes
5. create_light / create_registry_device: create light_target_intensity row for active mode
6. save_room_schedule: stop creating per-device rows, only update mode_parameters
7. update_light intensity: update light_target_intensity instead of schedules
8. Remove expand_light_schedules_for_control from schedule_merge.py + update merge_schedules_with_config
9. AlarmManager: CRITICAL for missing mode_parameters, WARNING for missing light intensity
10. X-Confirm-Destructive header guard
11. Ban F3 permanently (AGENTS.md)
12. Documentation (AGENTS.md + ARCHITECTURE.md + SCHEMATIC)
13. Deploy + verify
