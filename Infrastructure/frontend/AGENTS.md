# CEA FRONTEND

## OVERVIEW

React 18 + TypeScript + Vite + Tailwind. WebSocket for real-time sensor data. Built `dist/` served by automation-service.

## STRUCTURE

```
frontend/
├── src/
│   ├── components/          # 21 React components (main UI)
│   │   ├── ClimatePeriodsTable.tsx  # Climate period CRUD
│   │   ├── ClimatePeriodTimeline.tsx # 24h visualization
│   │   ├── CircularTimePicker.tsx # 657 lines - clock UI
│   │   ├── LightManager.tsx      # Light cluster control
│   │   └── ScheduleManager.tsx   # Schedule CRUD
│   ├── pages/               # Route pages
│   ├── services/            # API + WebSocket clients
│   ├── types/               # TypeScript interfaces
│   ├── utils/               # Sensor helpers
│   └── contexts/            # React context providers
├── public/
├── dist/                    # Production build output
├── vite.config.ts           # Dev proxy: /api → :8000, /ws → :8001
└── package.json
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Setup | `README.md` |
| Requirements | `REQUIREMENTS.md` (timeline rendering) |
| Grafana | `grafana/README.md` |
| Alerting | `grafana/alerting/README.md` |
| Setpoints in Grafana | `grafana/SETPOINTS_IN_GRAFANA.md` |

## LIGHT VS CLIMATE AND ROOM MODES

- **Light (sun/moon photoperiod)** and **climate** are fully decoupled:
  - **Light**: Drives intensity via sun/moon schedule with `light_ramp_up_minutes`/`light_ramp_down_minutes`; controls when lights turn on/off and at what intensity.
  - **Climate**: Setpoints come from **`climate_periods`** table (named periods with `start_time`, `end_time`, `ramp_minutes`, and setpoints for temp/humidity/CO2/VPD); completely independent of light schedule.
  - Operators may align climate period boundaries with day/night for convenience, but the system enforces no coupling.
- **Room modes**: **Veg** (18h photoperiod, start/end movable), **Flower** (12h photoperiod + submodes Stretch/Bulk/Ripen with different setpoints), **Drying** (24h moon, manual mode active), **Sleep** (24h moon, different setpoints, manual mode active). Manual light control is **shown only in Drying and Sleep**; it is hidden in Veg and Flower.

## ZONECONFIG SAVE (LIGHT + CLIMATE)

ZoneConfig is the main place users set day/night times (CircularTimePicker) and climate parameters. **SAVE** does three things:

1. **Mode parameters** — `PUT /api/room-modes/room/{location}/{cluster}/parameters` (climate setpoints, day/night times, ramp minutes).
2. **Room schedule (lights)** — `POST /api/room-schedule/{location}/{cluster}` with the same day/night times. This updates the `schedules` table and creates/updates per-device DAY/NIGHT schedules so the control loop (`get_light_intensity_details`) actually turns lights on/off.
3. **Climate periods** — `POST /api/climate-periods/bulk` with the configured climate periods (named periods with start/end times, ramp_minutes, and setpoints). This saves the complete climate schedule independently of the light schedule.

Without (2), changing photoperiod in ZoneConfig would only update mode_parameters; lights would still follow old or missing entries in `schedules`. Without (3), climate periods would not be persisted.

## KEY COMPONENTS

| Component | Purpose | Lines |
|-----------|---------|-------|
| `ClimatePeriodsTable` | Climate period CRUD (add/edit/delete periods) | — |
| `ClimatePeriodTimeline` | 24h timeline visualization of climate periods | — |
| `CircularTimePicker` | Radial time selection | 657 |
| `LightManager` | Light intensity control (not used in ZoneConfig) | 565 |
| `ScheduleManager` | Schedule list + CRUD | 514 |
| `SetpointEditor` | Setpoint form | — |

## DEVELOPMENT

```bash
# Install dependencies
npm install

# Development (port 3001)
npm run dev

# Production build
npm run build

# After build, restart automation-service to serve new dist/
sudo systemctl restart automation-service
```

## ENVIRONMENT

```bash
# .env (gitignored)
VITE_BACKEND_API_URL=http://mothernode:8000
VITE_AUTOMATION_API_URL=http://mothernode:8001
VITE_WEBSOCKET_URL=ws://mothernode:8000/ws
```

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Direct database access | Always use APIs |
| Hardcode URLs | Use environment variables |
| Skip input validation | Ranges enforced server-side |
| Commit `.env` files | Gitignored |
| Use port other than 3001 in dev | Conflicts with services |
| console.log in production | 61 instances found - use proper logging |

---

## GRAFANA DASHBOARDS

### Locations
- Provisioned from: `/var/lib/grafana/dashboards/`
- Sync script: `Infrastructure/frontend/grafana/sync_dashboards.sh`
- Auto-sync: Every 5 minutes via systemd timer

### Data Sources

| Source | Use For | Response Time |
|--------|---------|---------------|
| Redis | Current values, device states | <1ms |
| PostgreSQL | Historical time-series | 50-100ms |

### Query Patterns

| Panel Type | Data Source | Query Pattern |
|------------|-------------|---------------|
| Stat (current temp) | Redis | `GET sensor:dry_bulb_f` |
| Table (current values) | Redis | `MGET sensor:*` |
| Time-series graph | PostgreSQL | `get_sensor_data_optimized()` |
| Day/Night overlay | PostgreSQL | `get_light_periods()` |
| Min/Max stats | PostgreSQL | `get_sensor_stats()` |

### ANTI-PATTERNS

| Never | Instead |
|-------|---------|
| Use PostgreSQL for current-value panels | Use Redis |
| Query raw `measurement` table for >1h | Use aggregates |
| Skip `maxDataPoints` setting | Set to 1500 |
