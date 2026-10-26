# Automation Service Requirements

Normative behavior for the automation service. Hardware addresses, ports, and topology are owned by `ARCHITECTURE.md` and `Infrastructure/REQUIREMENTS.md`.

## Validation Surface

Local verification runs the automation pure tests against fakes for database, Redis, and I2C. Runtime validation uses `/health`, `/ready`, `journalctl`, and operator dashboards. The canonical gate is listed in `ARCHITECTURE.md`.

## Control Loop

- Control cadence reads the `automation.update_interval` service setting each tick (1–5 s); DB > `/var/lib/projectcea/settings-cache/automation.json` > YAML fallback, with live updates.
- Each tick captures the current `RuntimeDeviceSnapshot` exactly once.
- `/api/settings` exposes shared settings/schema and `/api/settings/{service}` persists live updates. `/api/config` and restart metadata are hardware-only.
- The empty registry is a valid state: startup installs a ready empty snapshot and emits no relay-ON or nonzero DFR command.

## Device Registry

`device_registry` is the sole source of truth for device identity, relay bindings, and DFR bindings. No YAML device definitions or commissioning subsystem participate. `automation_config.yaml` must not define devices, relay channels, or DFR assignments.

- Relay channel conflicts return HTTP 409. A confirmed steal commands and observes the old relay OFF before committing, and the response includes `displaced_device_id`.
- **R10 relay moves.** `PUT /api/devices/registry/{device_id}/relay` with `{channel}` moves a binding onto an empty channel or swaps owners with an occupied one. Both channels must confirm OFF before any binding write; a failed safe-off returns HTTP 503 `Unable to make relay channel N safe` with no binding changed. The shared `actuation_lock` serializes the control tick, manual device commands, raw channel writes, relay tests and registry mutations against each other and against a move.
- DFR `(dimming_board_id, dimming_channel)` pairs are globally unique; conflicts return HTTP 409 without a steal path.

## Climate

- Climate setpoints come from `climate_periods` rows keyed by `(location, cluster, mode_id, submode_id)`. Each row has `period_name`, `start_time`, `end_time`, `ramp_minutes`, and target columns.
- `ClimatePeriodResolver` and `SetpointManager` bridge period-to-period ramps via `ramp_in_duration`.
- Climate ramps survive restarts the way light ramps do: nothing is persisted. On a room's first evaluation after process start, if the current period's scheduled window `[start_time, start_time + ramp_minutes)` contains now, `SetpointManager` re-derives the ramp from the clock: anchor at the period boundary, source = targets of the cyclic predecessor period of the exact captured profile (rows ordered by start clock, wrapping), same skip thresholds as transitions, no lifecycle event. A ramp re-planned by a mid-window target or profile edit resumes on the scheduled line after a restart.
- Constant modes with `room_modes.is_constant = true` may use a single period. A period with `start_time == end_time` means 24-hour coverage; prefer `00:00` → `00:00`.
- The saved projection trajectory emits an `effective` segment series for every climate metric that is currently active: a schedule period with a setpoint for it, including the ramp between periods (owner rule). A metric with no setpoint (for example CO₂ when unset) is reported unavailable, never invented. Monitoring graphs only these effective segments; the `scheduled` trajectory is for the Control timeline. Verified on production 2026-10-25: Flower emitted effective heating, cooling and VPD (heating ramping 25.0 → 24.98 °C) and CO₂ unavailable.

## Lights

- Photoperiod boundaries come from `mode_parameters.day_start_time` and `night_start_time` per room per mode. Overnight windows are supported.
- Photoperiod (SUN/MOON) and climate periods are independent schedules (owner rule R5). Nothing derives one from the other: climate period boundaries never move a photoperiod boundary, and photoperiod times never select a climate period. Graph sun/moon overlays come only from recorded or projected photoperiod.
- Per-light intensity anchors live in `light_target_intensity` keyed by `(device_id, mode_id)`. A missing row falls back to `MINIMUM_LIGHT_INTENSITY = 10.0`.
- `light_programs` provide `supplemental` (adds light in dark) or `override` (replaces intensity in sun) programs. Priority is descending; ties break by `created_at` ascending.
- Moon-authority modes (`drying`, `sleep`) force scheduled lights to 0%, DFR intensities to 0%, and light relays OFF on entry and every tick. Manual light controls remain available.
- Authority order: safety interlocks > manual override > schedule automation.
- Relay/dimmer sequencing: intensity > 0 uses relay ON then dimmer set; intensity = 0 uses dimmer 0 then relay OFF.
- Historical room phase recording is non-blocking and changes-only: one first SUN/MOON observation and real phase changes use `monitoring_room_photoperiod.source = 'photoperiod_transition'`. Time elapsed, profile metadata changes, and same-phase restarts do not create phase heartbeats.
- Sparse startup, shutdown, control-failure, and lost-record/recovery boundaries are stored separately in `monitoring_photoperiod_coverage`. Failed transactions retain their ordered bounded batch; repeatable-read restart seeding and conditional inserts prevent duplicate phase rows after ambiguous commits.
- An unclean stop (last coverage fact still `available`) is closed with `unavailable/unclean_restart` stamped at the previous run's latest persisted proof of recording: its last phase row, last coverage row, or its last `effective_setpoints` `Main` row before this process started. No re-baseline phase row is written at resume.
- Stops are bounded so the `stopped` closure is written: uvicorn runs with `--timeout-graceful-shutdown 5`, which cancels still-open requests (notably the `/api/events/stream` SSE) after 5 s and then runs lifespan shutdown, well inside systemd's stop timeout. The flag must appear in both `automation-service.service` and `systemd-overrides/automation-service.service.d/override.conf`; the drop-in resets `ExecStart=`, so its line is the effective one.

## VPD Control

VPD is the master controller; humidity tracks VPD-derived targets. PID is used for heating, cooling, and CO2 only. The global heating-failure↔exhaust interlock is not currently configured.

## Scheduler and Cache

The `Scheduler` installs snapshot-derived mode, light intensity, light program, and device lookup caches as one `install_snapshot()` operation. The `_ready` flag blocks ticks until the complete snapshot is installed.

After any write that changes schedules, invalidate the cache keys that `schedule_repo.get_schedules()` can hit: `schedules:all`, `schedules:loc:{location}`, and `schedules:loc:{location}:cluster:{cluster}`.

## Mode Transitions

- `ModeTransitionService` must not call `sync_room_schedule_from_mode_parameters` on submode-only transitions. Light schedules stay tied to the parent mode.
- Mode parameters use `light_ramp_up_minutes` / `light_ramp_down_minutes`, which map to `schedules.ramp_up_duration` / `ramp_down_duration` for light rows.

## Grow Calendar

Flower grow plans (`POST /api/calendar/grow-plans/flower`) take `anchor` (`bed_veg_start`, `flower_start` or `ripen_end`), `anchor_date`, `bed_veg_start`, `bed_veg_days`, `stretch_days`, `bulk_days`, `ripen_days`, `drying_days`, optional `pot_start` and `clone_start`, and `auto_mode_transition`. For the `bed_veg_start` anchor, `anchor_date` must equal `bed_veg_start`.

- Fixed phase orders: 1 clone (Veg Room, optional), 2 pot veg (Veg Room, optional), 3 bed veg (Flower Room), 4 flip to flower, 5 stretch, 6 bulk, 7 ripen, 8 harvest on the last ripen day, 9 drying. Rows without a target mode store `auto_mode_transition = false`.
- Defaults: bed veg 14 days, stretch 21, bulk 21, ripen 21, drying 7. Stretch, bulk and ripen can each be extended by +1 week with `POST /api/calendar/grow-plans/{id}/extend` (`{event_id, days}`, 1–60): that phase's end grows by `days` and every later phase with a higher `phase_order` shifts by the same amount.
- **R11 Manual modes, automatic submodes.** Mode changes (Veg/Flower/Drying/Sleep) happen only through the room-mode API. `CalendarModeScheduler` changes only the submode of the active mode: a scheduled target that names a different mode returns without an event or an application, and `GET /api/calendar/pending-mode-change/{location}/{cluster}` surfaces the change instead. The "Automatic submode changes" toggle (`GET/PUT /api/calendar/flower-mode-transitions`) gates submode automation only.
- **Bed veg auto-extension.** While Flower Room is not in Flower, every scheduler tick and startup catch-up calls `align_flower_start(plan, today, forward_only=True)` on the current run. A plan whose flip has passed and whose stretch has not ended moves its flip to today: bed veg ends yesterday and every later phase shifts by the same delta; a flip already today is a no-op. Manual Flower activation from Veg calls it with `forward_only=False`, moving the flip to the activation day under the same stretch-not-ended condition. Both shifts write no operational event; a failed shift logs a warning and never fails the committed mode change.
- **Expected harvest.** `GET /api/calendar/expected-harvest/{location}/{cluster}` returns the last `flower_ripen` end date of the plan occupying the room (null once that day is past) with its `grow_plan_id` and ripen `event_id`. It is derived on read and never stored.

The pending change is derived and never stored. The expected mode for today is `veg-post` after the plan's last date, else `drying` from the drying start, else `flower` from the flip, else `veg-pre`. Non-empty combinations, by active mode:

| Expected mode today \ active mode | `veg` | `flower` | `drying` |
|---|---|---|---|
| before the flip (`veg-pre`) | — | — | → veg |
| flower window | → flower | — | → flower |
| drying window | → flower | → drying | — |
| after the plan's last date (`veg-post`) | — | → drying | → veg |

Other active modes (including `sleep`) and a missing plan yield no button; a still-active mode keeps its button after its window passed.

## Time and State

- Control logic uses the Quebec local timezone (`America/Toronto`) aware `now`.
- Dict and list values written to Redis must be JSON-encoded.
- Effective light intensity telemetry is written every tick to keep Redis and `effective_setpoints` aligned with runtime state.
