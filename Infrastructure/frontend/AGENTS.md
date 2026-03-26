# CEA FRONTEND

## OVERVIEW

React 18 + TypeScript + Vite + Tailwind. WebSocket for real-time sensor data. Built `dist/` served by automation-service.

## STRUCTURE

```
frontend/
├── src/
│   ├── components/          # 21 React components (main UI)
│   │   ├── ClimatePeriodsTable.tsx   # Climate period CRUD
│   │   ├── ClimatePeriodTimeline.tsx # 24h sun/moon + climate setpoint curves
│   │   ├── CircularTimePicker.tsx # 657 lines - clock UI
│   │   ├── LightIntensity.tsx    # ZoneConfig dimmable light targets (CUR/TGT)
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

- **Light (sun/moon photoperiod)** and **climate** are decoupled:
  - **Light**: `CircularTimePicker` sets `day_start_time`, `night_start_time`, and **`light_ramp_up_minutes` / `light_ramp_down_minutes`** (fade ramps). Intensity follows sun/moon schedules.
  - **Climate**: `ClimatePeriodsTable` + `POST /api/climate-periods/{location}/{cluster}` — variable **climate periods** with `ramp_minutes` per period (not the same as light ramps).
- **Room modes**: **Veg** (18h photoperiod option), **Flower** (12h + submodes Stretch/Bulk/Ripen), **Drying** / **Sleep** (manual light where applicable). Manual light control is **shown only in Drying and Sleep**; hidden in Veg and Flower.

## ZONECONFIG SAVE (LIGHT + CLIMATE)

**SAVE** runs three steps:

1. **Mode parameters** — `PUT /api/room-modes/room/{location}/{cluster}/parameters`.
2. **Room schedule (lights)** — `POST /api/room-schedule/{location}/{cluster}` with photoperiod times and **`ramp_up_duration` / `ramp_down_duration` from `light_ramp_*` only** (not legacy `ramp_up_minutes` / `ramp_down_minutes`). Updates `room_schedule` and per-device SUN/MOON rows.
3. **Climate periods** — `POST /api/climate-periods/{location}/{cluster}` with the period rows.

Without (2), lights would not track photoperiod in `schedules`. Without (3), climate periods would not persist.

## KEY COMPONENTS

| Component | Purpose | Lines |
|-----------|---------|-------|
| `ClimatePeriodsTable` | Climate period CRUD (times, ramps, setpoints) | — |
| `ClimatePeriodTimeline` | 24h sun/moon + heat/cool/VPD curves | — |
| `CircularTimePicker` | Radial time selection | 657 |
| `LightIntensity` | Light intensity panel in ZoneConfig | — |
| `ScheduleManager` | Schedule list + CRUD | 514 |

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
