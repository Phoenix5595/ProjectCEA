# AUTOMATION SERVICE

## OVERVIEW

Core control service: PID loops, schedules, mode transitions, device control, alarms. Serves frontend SPA on port 8001.

## STRUCTURE

```
automation-service/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── container.py         # Dependency injection
│   ├── database.py          # TimescaleDB ops
│   ├── redis_client.py      # Streams + state
│   ├── control/             # PID, scheduler, device control
│   ├── routes/              # API endpoints
│   ├── hardware/            # I2C drivers (DFR0971, MCP23017)
│   └── automation/          # Rules engine, interlocks
├── config_cli.py            # CLI for setpoints/PID/schedules
└── automation_config.yaml   # Hardware + zone config
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Full architecture | `README.md` |
| Requirements | `REQUIREMENTS.md` (mode timing, ramp logic) |
| PID tuning | `PID_TUNING_GUIDE.md` |
| Control subsystem | `app/control/AGENTS.md` |

## KEY CONCEPTS

### Light vs climate periods
- **Photoperiod**: Sun = lights on (ramps from `mode_parameters.light_ramp_up_minutes` / `light_ramp_down_minutes`). Moon = 0% outside that window. Boundaries come from `mode_parameters.day_start_time` / `night_start_time` per room, per mode. Overnight-capable (day_start can be > night_start).
- **Light intensity**: Comes from `light_target_intensity` table `(device_id, mode_id) -> target_intensity` (REAL, 0-100, default 10.0). When row missing, scheduler returns `MINIMUM_LIGHT_INTENSITY = 10.0`. Deprecated `mode_parameters.main_light_intensity` / `supplemental_light_intensity` columns still exist but are no longer read.
- **Light programs**: `light_programs` table provides supplemental (adds light during dark) and override (replaces intensity during sun) programs. Time-slot or cycle mode. Priority DESC, ties broken by created_at ASC.
- **Climate**: Setpoints come from `climate_periods` (named periods, `start_time`, `end_time`, `ramp_minutes`, targets). The control loop resolves the active period via `get_active_period()` / `ClimatePeriodResolver`. Climate does not switch lights; the light schedule does.
- **Lights on/off** come only from the photoperiod, not from climate period names.

### Ramp logic
- **Light ramp**: Per-device, computed from `mode_parameters.light_ramp_up_minutes` / `light_ramp_down_minutes`. Scheduler computes intensity vs time since sun start/end. Reset/resume is time-based (recalculates from elapsed time on restart).
- **Climate period ramp**: Each row's `ramp_minutes` — interpolate from previous period's setpoints to current period's over that window.

### Hardware
- **MCP23017** (relays) -> I2C bus 0, address 0x27. 16 ON/OFF channels (0-15).
- **DFR0971** (dimming) -> I2C bus 1, addresses 0x88 / 0x89 / 0x90. 6 dimming channels total (3 boards x 2 channels).
- Channel conflicts prevented by startup Pydantic validation.
- **DFR assignment management**: `GET /api/lights/dfr/assignments` and `PUT /api/lights/dfr/assign`. Assignments are globally unique for `(board_id, channel)` and only mutate YAML plus `config.reload()`.

### Config validation (startup)

[`app/models/config_schema.py`](app/models/config_schema.py) validates `automation_config.yaml` at load. Invalid config causes startup failure with a clear error.

**Rules:** I2C bus numbers 0-7; `control.update_interval` 1-5s; relay `channel` 0-15 and unique per room; `device_type` from allowed set; `dimming_board_id` must reference `hardware.dfr0971_boards`.

**How to fix errors:** Read the log message. Edit `automation_config.yaml` to resolve. Restart: `sudo systemctl restart automation-service`.

## SCHEDULER CACHES

The `Scheduler` maintains 4 atomic cache methods. Control loop won't tick until `_ready` flag is set (all caches populated):

1. `update_mode_parameters({(loc, cluster): {mode_id, day_start, night_start, ramp_up, ramp_down}})` — from `mode_parameters` table
2. `update_light_intensities({(device_id, mode_id): target_intensity})` — from `light_target_intensity` table
3. `update_light_programs([program dicts])` — from `light_programs` table
4. `update_device_lookup({(loc, cluster, device_name): {device_id, device_type, ...}})` — from `device_registry`

## REPOSITORY PATTERN

All data operations go through dedicated repositories:

| Repository | File | Purpose |
|------------|------|---------|
| `ControlAction` | `repositories/control_action.py` | Control action logging |
| `Device` | `repositories/device.py` | Device states and hardware config |
| `PID` | `repositories/pid.py` | PID parameters and tuning data |
| `RoomMode` | `repositories/room_mode.py` | Room operational modes |
| `Schedule` | `repositories/schedule/` | Non-light schedule rows |
| `LightTargetIntensity` | `repositories/light_target_intensity.py` | Per-light, per-mode intensity anchors |
| `LightPrograms` | `repositories/light_programs.py` | Supplemental and override light programs |
| `Sensor` | `repositories/sensor.py` | Sensor data validation and storage |
| `Setpoint` | `repositories/setpoint.py` | Environmental setpoints |
| `Config` | `repositories/config.py` | System configuration |
| `ClimatePeriod` | `repositories/climate_periods.py` | Climate period configuration |
| `Calendar` | `repositories/calendar.py` | Calendar events and grow plans |

`DatabaseManager` is a pure facade for connection management. All queries handled by repositories.

## API GROUPS

### Active Routes (18)

| Router | Prefix | Purpose |
|--------|--------|---------|
| `schedules` | `/api/schedules`, `/api/room-schedule/...` | Time-based automation + room schedule sync |
| `lights` | `/api/lights/...` | Light control, status, DFR assignments, targets |
| `climate_periods` | `/api/climate-periods/...` | Climate period configuration |
| `devices` | `/api/devices/...`, `/api/control/history` | Device state, manual control, mappings |
| `devices_crud` | `/api/devices/registry/...` | Device registry CRUD |
| `hardware` | `/api/hardware/relays/...` | Hardware relay test and state |
| `status` | `/health`, `/ready`, `/api/status` | Health and status checks |
| `notes` | `/api/notes/...` | Zone/mode notes |
| `alarms` | `/api/alarms/...` | Alarm management |
| `pid` | `/api/pid/...` | PID parameters, modes, autotune |
| `mode` | `/api/mode/...` | Mode management (auto/manual/override/failsafe) |
| `failsafe` | `/api/failsafe/...` | Failsafe status and clear |
| `websocket` | `/ws` | Real-time WebSocket |
| `room_modes` | `/api/room-modes/...` | Room mode management and parameters |
| `calendar` | `/api/calendar/...` | Grow calendar, events, sync |
| `redis_state` | `/api/redis-state/...` | Redis state queries |
| `debug` | `/api/debug/...` | Debug endpoints (mode-state, ramps, light-schedule-health, mode-history) |
| `system_config` | `/api/config/...` | System configuration and restart |

### Wave 3 Splits

- `lights` router was split from monolithic `routes/lights.py` into `routes/lights/` package:
  - `dfr_assignments.py` — DFR0971 board/channel assignment endpoints
  - `light_status.py` — Light status and query endpoints
  - `light_control.py` — Direct hardware control (intensity, voltage)
  - `light_target.py` — DB-backed target intensity updates
  - `light_crud.py` — Light CRUD (create, update, delete)
  - `light_test.py` — DFR test sweep endpoint

### Removed Routes (Dead Code)

| Route | Removal |
|-------|---------|
| `rules.router` | Deleted in Wave 1 — stub endpoints, zero live callers |
| `schedules/climate.router` | Deleted in Wave 1 — legacy no-op endpoints, `climate_periods` is canonical |
| Legacy PID routes in `pid.py` | Deleted in Wave 1 — 8 endpoints hardcoding "Flower Room"/"main", duplicated v2 location/cluster routes |

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Skip TimescaleDB writes | Setpoints must persist |
| Bypass validation | Setpoint ranges enforced (10-35C, 30-90% RH, 400-2000 ppm) |
| Modify zones at runtime | Requires restart |
| Use `sleep()` in control loop | Blocks deterministic 2s tick |

## COMMANDS

```bash
# CLI setpoint management
./config_cli.py setpoint get "Flower Room" main --mode DAY
./config_cli.py setpoint set "Flower Room" main --mode DAY --temp 24.0 --vpd 1.2 --dry-run

# Restart after config change
sudo systemctl restart automation-service
```

---

## SERVICE RESTART PERMISSIONS

The `cea` system user needs passwordless sudo to restart the automation service. This is required for the `POST /api/config/restart` endpoint.

- **Sudoers file**: `install/sudoers-cea-restart`
- **Installed to**: `/etc/sudoers.d/sudoers-cea-restart`
- **Command allowed**: `sudo /bin/systemctl restart automation-service.service`
- **Install script**: `Infrastructure/scripts/install-sudoers.sh` (called by `deploy.sh`)

---

## DATA FLOW: Sensors -> Control -> Storage

### 1. CAN Processor writes sensor values to:
- **Redis** `sensor:{name}` (real-time, 10s TTL)
- **PostgreSQL** `measurement` table (historical)

### 2. Automation Service reads from Redis for control decisions:
- `GET sensor:{name}` -> current value
- `GET effective_setpoint:{room}:{cluster}:{type}` -> target
- Compares and decides: heat/cool/nothing

### 3. Automation Service writes decisions to:
- **Redis** `automation:{room}:{cluster}:{device}` (state)
- **PostgreSQL** `automation_state` (history)
- **PostgreSQL** `control_history` (state changes)

### Why Redis for Control Loop?
- Control loop runs every ~1 second
- Redis GET: <1ms latency
- PostgreSQL query: 10-100ms latency
- Redis is the **source of truth** for current values

---

## REDIS ARCHITECTURE

### Key Schema (cea:* prefix)

```
cea:sensor:{location}:{cluster}:{sensor_type}  -> Current sensor value
cea:setpoint:{location}:{cluster}:{device}    -> Target values
cea:schedule:{location}:{cluster}             -> Active schedules
cea:ramp:{location}:{cluster}:{device}        -> Active ramp state
cea:mode:{location}:{cluster}                 -> Current mode (CRITICAL - no TTL)
cea:alarm:{location}:{cluster}:{alarm_type}   -> Active alarms
cea:pid:{device_type}                          -> PID parameters
cea:heartbeat:{service_name}                  -> Service liveness
cea:config:{config_type}:{id}                 -> Configuration snapshots
```

**Legacy (non-`cea:`) keys still written by the control loop** include `effective_setpoint:{location}:{cluster}:...` for climate and per-dimmer light telemetry. Do not use a single cluster-level `effective_setpoint:...:effective_light_intensity` for multiple lights (last-writer overwrite).

### TTL Strategy

| Category      | Data Type              | TTL      | Rationale                         |
| ------------ | --------------------- | -------- | -------------------------------- |
| **Critical** | mode, failsafe        | None     | Must survive restart              |
| **Runtime**  | setpoints, ramps     | 60s      | Refreshes frequently              |
| **Transient**| sensor values        | 10s      | Always fresh                     |
| **Cached**   | schedules, PID       | 300s     | Cache-aside, refresh on write    |

---

## EVENT BUS ARCHITECTURE

Config changes publish to both in-memory queue AND Redis Streams (`cea:events:config`).

| Module | Purpose |
|--------|---------|
| `app/events/__init__.py` | ConfigEventBus (dual-publish) |
| `app/events/redis_streams.py` | RedisStreamPublisher |
| `app/events/consumer.py` | RedisEventConsumer |

---

## VPD CASCADE CONTROL

**VPD is king; PID never uses humidity.** Humidifier and dehumidifier are controlled only from VPD setpoint and current VPD. PID is used only for heating, cooling, and CO2.

### Safety Logic for Heating Failure
If heating is active AND temp is below setpoint by 2C+, system enters safe mode and exhaust is inhibited to prevent making heating failure worse.

---

## RELAY STEAL

`devices_crud.py` steals relay channels instead of returning 409. Uses `clear_relay_binding_only(displaced_id)` BEFORE `update_device()`. Response includes `displaced_device_id`.

---

## TESTING

No automated test suite. Verify via `ruff check .` and manual API testing.

---

*Last updated: 2026-07-12*
