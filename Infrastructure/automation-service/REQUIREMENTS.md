# Automation Service Requirements

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

- **MCP/relay verification**
  - **Startup**: Optional MCP23017 I2C probe after init; configurable `require_mcp` (default false): if true, startup fails when probe fails; else fallback to simulation and log warning.
  - **Health**: GET /health exposes `hardware.mcp.connected` and `hardware.mcp.simulation` so operators know real vs simulation.
  - **Commissioning**: POST /api/hardware/relays/test (body: `channel` 0–15 or `all`: true, optional `duration_ms`) toggles channel(s), read-back verifies; response includes per-channel pass/fail and `mcp_connected`. GET /api/hardware/relays/state returns all 16 channel states.