# Automation Service Requirements

- Service runs under systemd (`automation-service.service`); restart after config/frontend builds: `sudo systemctl restart automation-service.service`.
- Climate modes supported: DAY, NIGHT, PRE_DAY, PRE_NIGHT. `ramp_in_duration` validated 0–240 minutes; PRE_DAY/PRE_NIGHT take precedence during their periods.
- **Climate Period Timing**: Two categories of schedules - Light Schedule (controls lights ON/OFF) and Climate Schedule (controls setpoints)
  - **Light Schedule**: DAY = lights ON (day_start_time to day_end_time), NIGHT = lights OFF (rest of time)
  - **Climate Schedule**: Follows light schedule timing but with transition periods
    - **DAY**: Pure climate DAY period (day_start_time to day_end_time - pre_night_duration), lights ON + climate DAY
    - **PRE_NIGHT**: Climate transition period (day_end_time - pre_night_duration to day_end_time), occurs DURING day light period (lights still ON), replaces end of DAY period
    - **NIGHT**: Pure climate NIGHT period (day_end_time to day_start_time - pre_day_duration), lights OFF + climate NIGHT
    - **PRE_DAY**: Climate transition period (day_start_time - pre_day_duration to day_start_time), occurs DURING night light period (lights still OFF), replaces end of NIGHT period
  - **Period Priority**: PRE_DAY > DAY > PRE_NIGHT > NIGHT
  - **Ramp Logic**: PRE_NIGHT ramps from DAY setpoints → PRE_NIGHT setpoints (fetches DAY setpoints from database), PRE_DAY ramps from NIGHT setpoints → PRE_DAY setpoints
- **Light (master)**: sun and moon. **Climate (slave)**: PRE_DAY (if duration > 0), DAY (same length as sun), PRE_NIGHT (if duration > 0), NIGHT (same duration as moon). Setpoints only for climate.
- Time parsing accepts `HH:MM` or `HH:MM:SS` strings.
- Database tables in use: `schedules`, `setpoints`, `pid_parameters`, `config_versions`, `effective_setpoints`. No unused tables identified for removal during latest audit.
- Keep UI/DB schema aligned for setpoints (modes + `ramp_in_duration`) and schedules (pre_day_duration, pre_night_duration).
- Light schedules are always daily (lights require `day_of_week = NULL`; per-day light schedules are invalid).
- Light ramp-up recalculates mid-ramp on target changes and completes within the original `ramp_up_duration` (increase slope if needed); ramp-down always continues to 0% even if the target changes mid-ramp.

- **MCP/relay verification**
  - **Startup**: Optional MCP23017 I2C probe after init; configurable `require_mcp` (default false): if true, startup fails when probe fails; else fallback to simulation and log warning.
  - **Health**: GET /health exposes `hardware.mcp.connected` and `hardware.mcp.simulation` so operators know real vs simulation.
  - **Commissioning**: POST /api/hardware/relays/test (body: `channel` 0–15 or `all`: true, optional `duration_ms`) toggles channel(s), read-back verifies; response includes per-channel pass/fail and `mcp_connected`. GET /api/hardware/relays/state returns all 16 channel states.

