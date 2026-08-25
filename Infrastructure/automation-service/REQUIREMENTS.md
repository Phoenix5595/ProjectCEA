# Automation Service Requirements

Normative behavior for the automation service. Hardware addresses, ports, and topology are owned by `ARCHITECTURE.md` and `Infrastructure/REQUIREMENTS.md`.

## Validation Surface

Local verification runs the automation pure tests against fakes for database, Redis, and I2C. Runtime validation uses `/health`, `/ready`, `journalctl`, and operator dashboards. The canonical gate is listed in `ARCHITECTURE.md`.

## Control Loop

- The configured control tick is 1 s (`automation_config.yaml` `control.update_interval`). Startup validation accepts 1–5 s.
- Each tick captures the current `RuntimeDeviceSnapshot` exactly once.
- The empty registry is a valid state: startup installs a ready empty snapshot and emits no relay-ON or nonzero DFR command.

## Device Registry

`device_registry` is the sole source of truth for device identity, relay bindings, and DFR bindings. No YAML device definitions or commissioning subsystem participate. `automation_config.yaml` must not define devices, relay channels, or DFR assignments.

- Relay channel conflicts return HTTP 409. A confirmed steal commands and observes the old relay OFF before committing, and the response includes `displaced_device_id`.
- DFR `(dimming_board_id, dimming_channel)` pairs are globally unique; conflicts return HTTP 409 without a steal path.

## Climate

- Climate setpoints come from `climate_periods` rows keyed by `(location, cluster, mode_id, submode_id)`. Each row has `period_name`, `start_time`, `end_time`, `ramp_minutes`, and target columns.
- `ClimatePeriodResolver` and `SetpointManager` bridge period-to-period ramps via `ramp_in_duration`.
- Constant modes with `room_modes.is_constant = true` may use a single period. A period with `start_time == end_time` means 24-hour coverage; prefer `00:00` → `00:00`.

## Lights

- Photoperiod boundaries come from `mode_parameters.day_start_time` and `night_start_time` per room per mode. Overnight windows are supported.
- Per-light intensity anchors live in `light_target_intensity` keyed by `(device_id, mode_id)`. A missing row falls back to `MINIMUM_LIGHT_INTENSITY = 10.0`.
- `light_programs` provide `supplemental` (adds light in dark) or `override` (replaces intensity in sun) programs. Priority is descending; ties break by `created_at` ascending.
- Moon-authority modes (`drying`, `sleep`) force scheduled lights to 0%, DFR intensities to 0%, and light relays OFF on entry and every tick. Manual light controls remain available.
- Authority order: safety interlocks > manual override > schedule automation.
- Relay/dimmer sequencing: intensity > 0 uses relay ON then dimmer set; intensity = 0 uses dimmer 0 then relay OFF.

## VPD Control

VPD is the master controller; humidity tracks VPD-derived targets. PID is used for heating, cooling, and CO2 only. The global heating-failure↔exhaust interlock is not currently configured.

## Scheduler and Cache

The `Scheduler` installs snapshot-derived mode, light intensity, light program, and device lookup caches as one `install_snapshot()` operation. The `_ready` flag blocks ticks until the complete snapshot is installed.

After any write that changes schedules, invalidate the cache keys that `schedule_repo.get_schedules()` can hit: `schedules:all`, `schedules:loc:{location}`, and `schedules:loc:{location}:cluster:{cluster}`.

## Mode Transitions

- `ModeTransitionService` must not call `sync_room_schedule_from_mode_parameters` on submode-only transitions. Light schedules stay tied to the parent mode.
- Mode parameters use `light_ramp_up_minutes` / `light_ramp_down_minutes`, which map to `schedules.ramp_up_duration` / `ramp_down_duration` for light rows.

## Time and State

- Control logic uses the Quebec local timezone (`America/Toronto`) aware `now`.
- Dict and list values written to Redis must be JSON-encoded.
- Effective light intensity telemetry is written every tick to keep Redis and `effective_setpoints` aligned with runtime state.
