# CEA FRONTEND

## OVERVIEW

React 18 + TypeScript + Vite + Tailwind. WebSocket for real-time sensor data. Built `dist/` served by automation-service.

## STRUCTURE

```
frontend/
├── src/
│   ├── components/          # 21 React components (main UI)
│   │   ├── SetpointTimeline.tsx  # 869 lines - 24h visualization
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

- **Light (sun/moon)** drives intensity; **climate (PRE_DAY, DAY, PRE_NIGHT, NIGHT)** drives setpoints and is slave to light (DAY = sun length, NIGHT = moon duration).
- **Room modes**: **Veg** (18h photoperiod, start/end movable), **Flower** (12h photoperiod + submodes Stretch/Bulk/Ripen with different setpoints), **Drying** (24h moon, manual mode active), **Sleep** (24h moon, different setpoints, manual mode active). Manual light control is **shown only in Drying and Sleep**; it is hidden in Veg and Flower.

## ZONECONFIG SAVE (LIGHT PHOTOPERIOD)

ZoneConfig is the main place users set day/night times (CircularTimePicker) and climate parameters. **SAVE** does two things:

1. **Mode parameters** — `PUT /api/room-modes/room/{location}/{cluster}/parameters` (climate setpoints, day/night times, ramp minutes).
2. **Room schedule (lights)** — `POST /api/room-schedule/{location}/{cluster}` with the same day/night times. This updates the `schedules` table and creates/updates per-device DAY/NIGHT schedules so the control loop (`get_light_intensity_details`) actually turns lights on/off.

Without (2), changing photoperiod in ZoneConfig would only update mode_parameters; lights would still follow old or missing entries in `schedules`.

## KEY COMPONENTS

| Component | Purpose | Lines |
|-----------|---------|-------|
| `SetpointTimeline` | 24h timeline with modes/ramps | 869 |
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
