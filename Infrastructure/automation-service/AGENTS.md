# AUTOMATION SERVICE

## OVERVIEW

Core control service: PID loops, schedules, mode transitions, device control, alarms. Serves frontend SPA on port 8001.

## STRUCTURE

```
automation-service/
├── app/
│   ├── main.py              # FastAPI app entry
│   ├── container.py         # Dependency injection (424 lines)
│   ├── database.py          # TimescaleDB ops (2,878 lines - hotspot)
│   ├── redis_client.py      # Streams + state (1,411 lines - hotspot)
│   ├── control/             # PID, scheduler, device control
│   ├── routes/              # API endpoints
│   ├── hardware/            # I2C drivers (DFR0971, MCP23017)
│   └── automation/          # Rules engine, interlocks
├── config_cli.py            # CLI for setpoints/PID/schedules
├── automation_config.yaml   # Hardware + zone config
└── tests/
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Full architecture | `README.md` (comprehensive) |
| Requirements | `REQUIREMENTS.md` (mode timing, ramp logic) |
| PID tuning | `PID_TUNING_GUIDE.md` |
| Control subsystem | `app/control/AGENTS.md` |

## KEY CONCEPTS

### Light (sun/moon) vs climate (slave to light)
- **Light (master)**: Two periods only — **sun** (lights on) and **moon** (lights off). Sun schedule defines photoperiod; outside that window = moon = 0%. Drying/sleep room modes = 24h moon. Drives intensity and relay only.
- **Climate (slave)**: PRE_DAY (if duration > 0), DAY (same length as sun, slave to sun), PRE_NIGHT (if duration > 0), NIGHT (same duration as moon, slave to moon). Drives setpoints only; PRE_DAY/PRE_NIGHT do not change lights.
- Light intensity is never undefined: either from sun schedule (with ramps) or 0% (moon).

### Climate Modes
- **DAY**: Lights ON, day setpoints
- **NIGHT**: Lights OFF, night setpoints
- **PRE_DAY**: Ramp from NIGHT → PRE_DAY setpoints (lights still OFF)
- **PRE_NIGHT**: Ramp from DAY → PRE_NIGHT setpoints (lights still ON)

### Ramp Logic
- `ramp_in_duration`: 0-240 minutes
- PRE_NIGHT: Fetches DAY setpoints, ramps to PRE_NIGHT
- PRE_DAY: Fetches NIGHT setpoints, ramps to PRE_DAY

### Light ramp (time-based, per-device)
- Light ramp state is keyed by `(location, cluster, device_name)`. Each device has its own ramp.
- **Reset/resume is time-based**: intensity is computed from time since schedule start (`time_since_start / ramp_up_duration`). On service restart, the ramp resumes by recalculating where the light should be from elapsed time (no stored intensity).
- Intensity is never undefined: either from sun schedule (with ramps) or 0% (moon). No schedule match or moon/NIGHT schedule → scheduler returns 0.0.

- **MCP23017** (relays) → I2C bus 0, address 0x27. 16 ON/OFF channels (0–15).
- **DFR0971** (dimming) → I2C bus 1, addresses 0x88 / 0x89 / 0x90. 6 dimming channels total (3 boards × 2 channels).
- Each DFR0971 board has 2 channels (0-1).
- Channel conflicts are prevented by startup validation using Pydantic models; ensure no duplicate channel assignments.
- Do not use the same I2C bus for both unless intentionally single-bus; never swap roles (MCP for dimming, DFR for relays). Config: `automation_config.yaml` `hardware.mcp_i2c_bus` and `hardware.dfr0971_i2c_bus`.

- Troubleshooting steps:
  1) Check I2C bus with `i2cdetect -y <bus>`
  2) Check board availability using API endpoints
  3) Always check DFR0971 status first via `/api/hardware/dfr0971/status`
  4) Check relay status with `journalctl` logs

### Config validation (startup)

[`app/models/config_schema.py`](app/models/config_schema.py) validates `automation_config.yaml` at load. Invalid config causes startup failure with a clear error.

**Rules:** I2C bus numbers 0–7; `control.update_interval` 1–5s; relay `channel` 0–15 and unique per room; `device_type` from allowed set; `dimming_board_id` must reference `hardware.dfr0971_boards`.

**How to fix errors:** Read the log message (e.g. `duplicate relay channels` or `hardware.mcp_i2c_bus must be between 0 and 7`). Edit `automation_config.yaml`: resolve duplicates by changing one device’s `channel`; fix bus/interval values; ensure dimming devices reference an existing `board_id` in `dfr0971_boards`. Restart: `sudo systemctl restart automation-service`.

## API GROUPS

| Prefix | Purpose |
|--------|---------|
| `/api/devices` | Device state, manual control |
| `/api/setpoints` | Target values per mode |
| `/api/schedules` | Time-based automation |
| `/api/pid` | PID parameters |
| `/api/failsafe` | Safety status |
| `/ws` | Real-time WebSocket |

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Skip TimescaleDB writes | Setpoints must persist |
| Bypass validation | Setpoint ranges enforced (10-35°C, 30-90% RH, 400-2000 ppm) |
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

## DATA FLOW: Sensors → Control → Storage

### 1. CAN Processor writes sensor values to:
- **Redis** `sensor:{name}` (real-time, 10s TTL)
- **PostgreSQL** `measurement` table (historical)

### 2. Automation Service reads from Redis for control decisions:
- `GET sensor:{name}` → current value
- `GET effective_setpoint:{room}:{cluster}:{type}` → target
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

## REDIS ARCHITECTURE (Feb 2026)

### Key Schema (cea:* prefix)

All Redis keys follow the standardized schema:

```
cea:sensor:{location}:{cluster}:{sensor_type}  → Current sensor value
cea:setpoint:{location}:{cluster}:{device}    → Target values
cea:schedule:{location}:{cluster}             → Active schedules
cea:ramp:{location}:{cluster}:{device}        → Active ramp state
cea:mode:{location}:{cluster}                 → Current mode (CRITICAL - no TTL)
cea:alarm:{location}:{cluster}:{alarm_type}   → Active alarms
cea:pid:{device_type}                          → PID parameters
cea:heartbeat:{service_name}                  → Service liveness
cea:config:{config_type}:{id}                 → Configuration snapshots
```

### TTL Strategy

| Category      | Data Type              | TTL      | Rationale                         |
| ------------ | --------------------- | -------- | -------------------------------- |
| **Critical** | mode, failsafe        | None     | Must survive restart              |
| **Runtime**  | setpoints, ramps     | 60s      | Refreshes frequently              |
| **Transient**| sensor values        | 10s      | Always fresh                     |
| **Cached**   | schedules, PID       | 300s     | Cache-aside, refresh on write    |

### Key Modules

| Module | Purpose |
|--------|---------|
| `app/redis/schema.py` | Key pattern constants and builders |
| `app/redis/ttl.py` | TTL category constants |
| `app/redis/validation.py` | Schema validation mixin |
| `app/redis/migrate.py` | Key migration with rollback |

### StateManager

`app/state/__init__.py` provides in-memory caching with TTL and Redis fallback:

```python
from app.state import StateManager, get_state_manager

# Enable schema validation (optional)
state = StateManager(default_ttl=60.0, validate_keys=True)

# Cache-aside pattern
value = await state.get_mode("Flower Room", "main")
await state.set_mode("Flower Room", "main", "DAY", ttl=300)
```

---

## EVENT BUS ARCHITECTURE (Feb 2026)

### Dual-Publish Pattern

Config changes publish to both in-memory queue AND Redis Streams:

- **In-memory**: `asyncio.Queue` for same-process handlers
- **Redis Streams**: `cea:events:config` for cross-service propagation

### Key Modules

| Module | Purpose |
|--------|---------|
| `app/events/__init__.py` | ConfigEventBus (in-memory + Redis dual-publish) |
| `app/events/redis_streams.py` | RedisStreamPublisher |
| `app/events/consumer.py` | RedisEventConsumer (reads from stream) |

### Usage

```python
from app.events import ConfigEventBus, ConfigChangeEvent, ConfigEventType, get_event_bus

# Publish event
event = ConfigChangeEvent(
    event_type=ConfigEventType.SCHEDULE_CHANGED,
    location="Flower Room",
    cluster="main",
    config_type="schedules",
    data={"action": "updated", "schedule_id": 123}
)
await get_event_bus().publish(event)
```

---

## FUTURE: MULTI-CLUSTER SUPPORT

### Cluster Naming Convention



### Sensor Key Naming



### Control Isolation

Each cluster should be independently controllable:
- Separate PID instances per cluster
- Separate setpoints per cluster (if needed)
- Failover: If one cluster's sensors fail, use backup chain

### Database Schema Considerations

C:\Windows\System32\main.cpl

---

## LEAF TEMPERATURE INPUT METHODS

### Current: Manual Delta Entry
- User measures with handheld IR laser
- Enters delta (typically -1°C to -3°C vs air temp)
- Stored in database per room

### Future: IR Camera Heatmap
- Thermal camera captures leaf canopy
- ML model extracts average leaf temperature
- Real-time updates to VPD controller

### Fallback Chain


---

*Last updated: 2026-01-13 - Multi-cluster and leaf temp documentation*


---

## FUTURE: MULTI-CLUSTER SUPPORT

### Cluster Naming Convention

Location examples: Flower Room, Veg Room, Lab, Water Management
Cluster examples: main, secondary, backup

Full examples:
- Flower Room / main (current front sensors)
- Flower Room / secondary (future back sensors)
- Veg Room / main (only cluster needed)
- Lab / main (TBD configuration)

### Sensor Key Naming

Format: {sensor_type}_{location_suffix}_{cluster_suffix}

Examples:
- dry_bulb_f_main: Flower Room, main cluster, dry bulb
- dry_bulb_f_sec: Flower Room, secondary cluster
- wet_bulb_v_main: Veg Room, main cluster, wet bulb
- co2_f_main: Flower Room, main cluster, CO2
- water_level_wm_main: Water Management, main cluster

### Control Isolation

Each cluster should be independently controllable:
- Separate PID instances per cluster
- Separate setpoints per cluster if needed
- Failover: If one cluster sensors fail, use backup chain

### Database Schema Considerations

Current: location + cluster columns exist
Future: Ensure all queries filter by BOTH location AND cluster
Never assume one cluster per room

Good query pattern:
  SELECT * FROM measurement WHERE location = X AND cluster = Y

Bad query pattern (will break with multi-cluster):
  SELECT * FROM measurement WHERE location = X

---

## LEAF TEMPERATURE INPUT METHODS

### Current: Manual Delta Entry
- User measures with handheld IR laser
- Enters delta (typically -1C to -3C vs air temp)
- Stored in database per room

### Future: IR Camera Heatmap
- Thermal camera captures leaf canopy
- ML model extracts average leaf temperature
- Real-time updates to VPD controller

### Fallback Chain
1. IR Camera (best accuracy)
2. Manual Delta (user-measured)
3. Calculated Offset (-2C default)

---

## VPD CASCADE CONTROL

**VPD is king; PID never uses humidity.** Humidifier and dehumidifier are controlled only from VPD setpoint and current VPD (no humidity setpoint or RH sensor in the control decision). PID is used only for heating, cooling, and CO2. Cascade is documented in ARCHITECTURE.md (VPD Cascade Control), ARCHITECTURE_SCHEMATIC.md (Control Loop), and this file.

VPD is the MASTER controller for humidity-related automation.
RH is monitored as SAFETY backup for heating failure scenarios.

### Why VPD as Master
- Plants respond to VPD, not raw humidity
- Automatically adapts to temperature changes
- Reduces equipment cycling
- Industry best practice for CEA

### Safety Logic for Heating Failure
Problem: If heater fails, temp drops, RH spikes, VPD drops
Bad response: Exhaust fan tries to lower RH, makes temp drop worse

Safety rule implemented:
- If heating is active AND temp is below setpoint by 2C+
- System enters safe mode
- Exhaust is inhibited to prevent making heating failure worse

---

*Last updated: 2026-01-13 - Multi-cluster and VPD documentation*


---

## SELF-TUNING PID IMPLEMENTATION

### Algorithm: Relay Feedback Auto-Tuning

The system will use relay feedback method to identify optimal PID parameters:

1. Apply relay (on-off) control around setpoint
2. Measure resulting oscillation period (Tu) and amplitude (a)
3. Calculate ultimate gain: Ku = 4d / (pi * a) where d = relay amplitude
4. Apply Ziegler-Nichols or SIMC tuning rules
5. Continuously refine based on observed performance

### Data Logging for Neural-PID

Every control action logs:
- Timestamp
- Setpoint
- Process variable (sensor reading)
- Error (setpoint - PV)
- PID output (0-100%)
- Actual device state
- Current Kp, Ki, Kd values
- Settling time, overshoot, steady-state error

This data feeds future Neural-PID training.

### Deadband and Anti-Windup

- Deadband: 0.5C for temperature, 2% for humidity
- Anti-windup: Clamp integral term when output saturated
- Derivative filter: Low-pass to reduce noise sensitivity

---

## LEAF TEMPERATURE DELTA SYSTEM

### Database Schema Addition



### Calculation Logic



### Frontend UI

Add to room settings page:
- "Leaf Temperature Offset (Day)" input with default -1.5C
- "Leaf Temperature Offset (Night)" input with default -0.5C
- "Last measured" timestamp display
- Help text explaining measurement procedure

---

*Implementation details for self-tuning PID and leaf temperature*

## OPTIMIZATION WORK COMPLETED (Jan 2026)

### Phase 1: Critical Reliability
- **Bug Fix**: Fixed  signature mismatch in device_controller.py
  - Was passing 
  - Now passes 
- **CAN Processor**: Implemented async batching for DB writes
  - 100ms max delay, 50 message threshold
  - 10,000 message queue capacity
  - Redis writes remain instant (non-negotiable for control loop)
- **Service Hardening**:
  - Created dedicated `cea` system user
  - Added systemd watchdog (30s timeout)
  - Memory limits: automation-service 256MB, can-processor 128MB
  - Security: NoNewPrivileges, ProtectSystem=strict, PrivateTmp

### Phase 2: Database Optimization
- **Hypertables**: measurement, effective_setpoints, automation_state
- **Compression**: Enabled on effective_setpoints (segmentby: location, cluster)
- **Retention**: 2-year retention for AI training data

### Phase 3: VPD Cascade Controller
- New `vpd_controller.py` module created
- Features:
  - VPD calculation from air temp, humidity, leaf temp delta
  - PID control for VPD targeting
  - Growth stage presets (propagation, vegetative, flowering)
  - Cascade control: VPD → humidity setpoint adjustment

### Phase 4: Modular Architecture
- Clean module interfaces in `app/control/__init__.py`
- Public API exports: ControlEngine, PIDControllerManager, VPDController, SetpointManager, DeviceController

## VPD CONTROLLER USAGE

```python
from app.control import VPDController

vpd = VPDController(leaf_temp_delta=-2.0)
state = vpd.calculate_vpd(air_temp_c=25.0, humidity_pct=60.0)
print(f"VPD: {state.vpd_kpa:.2f} kPa")

# Get target humidity for desired VPD
target_rh = vpd.calculate_target_humidity(target_vpd=1.0, air_temp_c=25.0)
```

---

## METRICS (Feb 2026)

### Redis Metrics

`app/metrics/redis_metrics.py` tracks cache hit/miss and latency:

```python
from app.metrics import RedisMetrics

metrics = RedisMetrics()
metrics.track_hit()
metrics.track_miss()
metrics.track_operation_latency("set_get", 5.2)

stats = metrics.get_stats()
# {'hits': 1, 'misses': 1, 'hit_rate_percent': 50.0, 'latency': {...}}
```

### Event Metrics

`app/metrics/event_metrics.py` tracks event publish/consume:

```python
from app.metrics import EventMetrics

events = EventMetrics()
events.track_published("SCHEDULE_CHANGED")
events.track_consumed("SCHEDULE_CHANGED")
events.track_processing_latency("SCHEDULE_CHANGED", 12.5)

stats = events.get_stats()
# {'published': {'SCHEDULE_CHANGED': 1}, 'consumed': {...}, 'latency_ms': {...}}
```
