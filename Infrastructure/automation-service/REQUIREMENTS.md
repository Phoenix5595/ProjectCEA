# Automation Service Requirements

- **Control loop structure**: `ControlEngine` orchestrates each tick; `DeviceProcessor` runs per-cluster device and PID steps. Refactors may split **phase helpers** (e.g. device hierarchy cache, light effective-setpoint logging throttles, performance stats) into dedicated modules **without** changing actuator ordering, interlocks, or tick cadence. Prefer explicit interfaces over growing `ControlEngine` methods.
- Service runs under systemd (`automation-service.service`); restart after config/frontend builds: `sudo systemctl restart automation-service.service`.
- **Climate setpoints**: Resolved from the **`climate_periods`** table (per location/cluster/mode/submode). Each row has `period_name`, `start_time`, `end_time`, `ramp_minutes`, and setpoints. The control loop uses `ClimatePeriodRepository.get_active_period()` — **not** a fixed PRE_DAY / DAY / PRE_NIGHT / NIGHT ladder. `ClimatePeriodResolver` / `SetpointManager` apply period-to-period ramps (`ramp_in_duration` bridge); this timeline is **independent** of photoperiod length.
- **Photoperiod (lights)**: Sun/moon window and **light** fade durations come from `mode_parameters` (`day_start_time`, `night_start_time`, `light_ramp_up_minutes`, `light_ramp_down_minutes`) and are written to `schedules` (`room_schedule` + per-device SUN/MOON). **`POST /api/room-schedule`** must use **`light_ramp_*`** for `ramp_up_duration` / `ramp_down_duration`, not legacy `ramp_up_minutes` / `ramp_down_minutes`.
- **Legacy / metadata**: `schedules` / `climate_schedule` may still expose old fields; **effective** setpoints and climate ramps come from **`climate_periods`**.
- Time parsing accepts `HH:MM` or `HH:MM:SS` strings.
- Database tables in use include: `schedules`, `climate_periods`, `mode_parameters`, `setpoints` (legacy), `pid_parameters`, `config_versions`, `effective_setpoints`. Primary climate path: **`climate_periods`**.
- Keep UI/DB aligned for **climate periods** (`/api/climate-periods/{location}/{cluster}`) and light schedules.
- Light schedules are always daily (lights require `day_of_week = NULL`; per-day light schedules are invalid).
- **Mode parameters: ramp pairs** — **`light_ramp_up_minutes` / `light_ramp_down_minutes`** map to light **`schedules.ramp_up_duration` / `ramp_down_duration`** (sun/moon intensity). Climate transition ramps are **`climate_periods.ramp_minutes`** per period, not `ramp_up_minutes` / `ramp_down_minutes` on `mode_parameters`. `sync-from-mode-parameters` and ZoneConfig room-schedule POST must use **`light_ramp_*`** for room/light schedule ramps only.
- Light ramp-up recalculates mid-ramp on target changes and completes within the original `ramp_up_duration` (increase slope if needed); ramp-down always continues to 0% even if the target changes mid-ramp.
- **Scheduler load**: Whenever schedules are loaded into `Scheduler` (service startup, `update_schedules` from API, or config events), use `merge_schedules_with_config(db_rows, config)` so `room_schedule` rows are expanded into per-light **SUN** rows for each DFR0971 light that lacks them. Otherwise photoperiod can be correct while intensity stays 0%.
- **Scheduler time semantics**: `get_schedule_intensity` ramp math uses `datetime.combine` in the **same** naive/aware mode as `current_time` (naive local vs timezone-aware) so subtracting ramp boundaries never mixes offset-naive and offset-aware datetimes. **`is_in_photoperiod(location, cluster, time)`** defines sun vs moon for climate: per-light SUN/DAY rows take precedence over `room_schedule`; **`ClimatePeriodResolver.calculate_is_sun`** supports both the legacy `(light_schedule, current_time)` call and `(current_time, location, cluster)` delegating to the scheduler.
- **StateManager / Redis**: Dict and list values written to Redis must use **JSON** (`json.dumps`), not `str()` — repr-style strings break `/api/schedules` and any code that expects a list of dicts from cache. Invalid legacy keys are dropped on read so Postgres repopulates cache.
- **Batch hardware execution + Redis/UI**: When using `HardwareBatchExecutor` for DFR0971 updates, persist the *intended* final light intensity to Redis after the batch completes (write **0%** on failure). Otherwise `/api/lights/*/zone-status` will read stale Redis values and make the UI appear “stuck on” even when the hardware has turned off.

- **Heating safety**: The automation loop does **not** run heating-failure monitoring (no emergency / warning / critical / low-temperature alerts from `DeviceProcessor`). PID and actuators behave as before; only the separate safety logger was removed.

- **MCP/relay verification**
  - **Startup**: Optional MCP23017 I2C probe after init; configurable `require_mcp` (default false): if true, startup fails when probe fails; else fallback to simulation and log warning.
  - **Health**: GET /health exposes `hardware.mcp.connected` and `hardware.mcp.simulation` so operators know real vs simulation.
  - **Commissioning**: POST /api/hardware/relays/test (body: `channel` 0–15 or `all`: true, optional `duration_ms`) toggles channel(s), read-back verifies; response includes per-channel pass/fail and `mcp_connected`. GET /api/hardware/relays/state returns all 16 channel states.

- **Light authority and override contract**
  - Authority order is strict: **Safety interlocks > Manual override > Schedule automation**.
  - Manual light overrides are TTL-based and expire automatically according to operator-selected duration, then return to scheduled automation.
  - Schedule target updates during ramp windows must recalculate from current intensity and still finish at the original ramp window end.

- **Light failure behavior**
  - On relay/dimmer write failure during SUN, preserve the last known hardware light state (hold-last) and emit an alert/log; do not force immediate OFF unless a safety interlock requires it.
  - Relay/dimmer sequencing remains mandatory: intensity > 0 uses relay ON then dimmer set; intensity = 0 uses dimmer 0 then relay OFF.

- **Light telemetry**
  - Effective light intensity telemetry is written every control loop for each managed dimmable light to keep Grafana aligned with runtime state.
  - Light telemetry writes must flow through a unified writer path to avoid DB/Redis drift.

- **Deploy and rollback (production)**
  - **`ProjectCEA/deploy.sh`**: copies `Infrastructure/` to `/opt/projectcea/releases/<release_id>`, switches `/opt/projectcea/current`, restarts services, runs HTTP health checks on backend (8000), automation (8001), onewire-worker (8004). If any check fails, **symlink reverts** to the previous release (when it still exists on disk), services restart again, script exits non-zero. **NDJSON log** (one JSON object per line, machine-readable for triage): `/var/lib/projectcea/deploy.log` (events include `deploy_start`, `health_ok` / `health_fail`, `rollback_auto_*`, `deploy_success` / `deploy_fail`).
  - **`ProjectCEA/rollback-deploy.sh`**: points `current` at **`rollback_to_path`** from `/var/lib/projectcea/deploy_state.json` (written on last **successful** deploy) or at an explicit release id/path, then restarts the same services and re-checks health. Logs `rollback_manual_*` lines to the same NDJSON file.
  - **Manifest**: On success, `deploy.sh` writes `/opt/projectcea/current/deploy_manifest.json` with `release_id`, `git_sha`, `deployed_at`, `health_ok`.
  - **Helpers**: `Infrastructure/scripts/deploy_json_line.py` (adds `ts` UTC), `deploy_emit_event.py` (builds event payload from env).