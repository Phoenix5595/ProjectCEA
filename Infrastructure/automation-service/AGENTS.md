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

### Climate Modes
- **DAY**: Lights ON, day setpoints
- **NIGHT**: Lights OFF, night setpoints
- **PRE_DAY**: Ramp from NIGHT → PRE_DAY setpoints (lights still OFF)
- **PRE_NIGHT**: Ramp from DAY → PRE_NIGHT setpoints (lights still ON)

### Ramp Logic
- `ramp_in_duration`: 0-240 minutes
- PRE_NIGHT: Fetches DAY setpoints, ramps to PRE_NIGHT
- PRE_DAY: Fetches NIGHT setpoints, ramps to PRE_DAY

### Hardware
- **MCP23017** (0x20): 16-channel relay expander
- **DFR0971** (0x58-0x5A): 0-10V DAC for light dimming

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
