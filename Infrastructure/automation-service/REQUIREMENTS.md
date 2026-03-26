# Automation Service Requirements

- Service runs under systemd (`automation-service.service`); restart after config/frontend builds: `sudo systemctl restart automation-service.service`.
- **Climate setpoints**: Resolved from the **`climate_periods`** table (per location/cluster/mode/submode). Each row has `period_name`, `start_time`, `end_time`, `ramp_minutes`, and setpoints (heat/cool/VPD/CO2). The control loop uses `ClimatePeriodRepository.get_active_period()` — **not** a fixed PRE_DAY / DAY / PRE_NIGHT / NIGHT ladder.
- **Light (master)**: Sun/moon photoperiod from light schedules (`room_schedule` + per-device SUN/MOON rows). **Climate (slave)**: whichever **climate_period** is active at local time; period boundaries are independent of fixed “PRE_*” names (those concepts are retired for control).
- **Legacy / metadata**: `schedules` rows with `device_name = 'climate'` may still expose `pre_day_duration` / `pre_night_duration` via `/api/climate-schedule` for timing metadata; **effective setpoints and ramps** come from **`climate_periods`**, not from mode-based `setpoints` rows or PRE_* mode names.
- Time parsing accepts `HH:MM` or `HH:MM:SS` strings.
- Database tables in use include: `schedules`, `climate_periods`, `setpoints` (legacy), `pid_parameters`, `config_versions`, `effective_setpoints`. Primary climate path: **`climate_periods`**.
- Keep UI/DB aligned for **climate periods** (`/api/climate-periods/...`) and light schedules; do not assume PRE_* modes in new features.
- Light schedules are always daily (lights require `day_of_week = NULL`; per-day light schedules are invalid).
- **Mode parameters: ramp pairs** — **`light_ramp_up_minutes` / `light_ramp_down_minutes`** map to light **`schedules.ramp_up_duration` / `ramp_down_duration`** (sun/moon intensity). Climate transition ramps are **`climate_periods.ramp_minutes`** per period, not `ramp_up_minutes` / `ramp_down_minutes` on `mode_parameters`. `sync-from-mode-parameters` and ZoneConfig room-schedule POST must use **`light_ramp_*`** for room/light schedule ramps only.
- Light ramp-up recalculates mid-ramp on target changes and completes within the original `ramp_up_duration` (increase slope if needed); ramp-down always continues to 0% even if the target changes mid-ramp.

- **MCP/relay verification**
  - **Startup**: Optional MCP23017 I2C probe after init; configurable `require_mcp` (default false): if true, startup fails when probe fails; else fallback to simulation and log warning.
  - **Health**: GET /health exposes `hardware.mcp.connected` and `hardware.mcp.simulation` so operators know real vs simulation.
  - **Commissioning**: POST /api/hardware/relays/test (body: `channel` 0–15 or `all`: true, optional `duration_ms`) toggles channel(s), read-back verifies; response includes per-channel pass/fail and `mcp_connected`. GET /api/hardware/relays/state returns all 16 channel states.