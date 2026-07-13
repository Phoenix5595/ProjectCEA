# CEA FRONTEND

## OVERVIEW

React 18 + TypeScript + Vite + Tailwind. WebSocket real-time sensor data. Built `dist/` served by automation-service.

## STRUCTURE

```
frontend/
├── src/
│   ├── components/
│   │   ├── ClimatePeriodTimeline.tsx
│   │   ├── ClimatePeriodsTable.tsx
│   │   ├── CircularTimePicker.tsx
│   │   ├── LightIntensity.tsx
│   │   ├── DeviceManager.tsx
│   │   └── devices/
│   │       ├── DeviceTable.tsx
│   │       ├── RelayChannelMatrix.tsx
│   │       ├── RelayChannelBox.tsx
│   │       └── DfrBoardsPanel.tsx
│   ├── pages/
│   │   └── DeviceConfig.tsx
│   ├── services/            # API + WebSocket clients
│   ├── types/               # TypeScript interfaces
│   ├── utils/               # Sensor helpers
│   └── contexts/            # React context providers
├── public/
├── dist/                    # Production build
├── vite.config.ts           # Dev proxy → Caddy :8080
└── package.json
```

## DEEP DIVE DOCS

| Topic | Document |
|-------|----------|
| Setup | `README.md` |
| Requirements | `REQUIREMENTS.md` |
| Grafana | `grafana/README.md` |
| Alerting | `grafana/alerting/README.md` |
| Setpoints in Grafana | `grafana/SETPOINTS_IN_GRAFANA.md` |

## LIGHT VS CLIMATE AND ROOM MODES

- **Light** and **climate** are decoupled:
  - **Light**: `CircularTimePicker` sets `day_start_time`, `night_start_time`, `light_ramp_up_minutes`, `light_ramp_down_minutes`. Intensity follows sun/moon schedules.
  - **Climate**: `ClimatePeriodsTable` + `POST /api/climate-periods/{location}/{cluster}` — variable periods with `ramp_minutes` per period.
- **Room modes**: Veg (18h), Flower (12h + Stretch/Bulk/Ripen), Drying / Sleep. Manual light control shown only in Drying and Sleep.

## ZONECONFIG SAVE
**SAVE** runs three steps:

1. **Mode parameters** — `PUT /api/room-modes/room/{location}/{cluster}/parameters`.
2. **Room schedule (lights)** — `POST /api/room-schedule/{location}/{cluster}` with photoperiod times and `ramp_up_duration` / `ramp_down_duration` from `light_ramp_*`.
3. **Climate periods** — `POST /api/climate-periods/{location}/{cluster}` with period rows.
## KEY COMPONENTS

| Component | File | Purpose |
|-----------|------|---------|
| ClimatePeriodTimeline | `components/ClimatePeriodTimeline.tsx` | 24h sun/moon + ramps. Draggable handles (w-8), right-click edit, ramps on sun band |
| ClimatePeriodsTable | `components/ClimatePeriodsTable.tsx` | Climate period CRUD |
| CircularTimePicker | `components/CircularTimePicker.tsx` | Radial time selection (657 lines) |
| LightIntensity | `components/LightIntensity.tsx` | ZoneConfig dimmable light targets (CUR/TGT) |
| DeviceTable | `components/devices/DeviceTable.tsx` | Device CRUD, relay steal red outline |
| DeviceManager | `components/DeviceManager.tsx` | Tabbed container, refreshKey-driven real-time reload |
| RelayChannelMatrix | `components/devices/RelayChannelMatrix.tsx` | 16-channel relay grid |
| RelayChannelBox | `components/devices/RelayChannelBox.tsx` | Single relay channel box, badges text-[20px] |
| DfrBoardsPanel | `components/devices/DfrBoardsPanel.tsx` | DFR0971 board/channel assignment |

## COMPONENT DETAILS

- **DeviceTable**: Inline editing. Relay steal shows `ring-2 ring-status-danger` on displaced devices via `displaced_device_id`. Delete uses `X-Confirm-Destructive` header.
- **DeviceManager**: Tabbed container (Devices/Settings). `refreshKey` increments on any device change, triggers `loadChannels(false)` for real-time relay matrix update.
- **RelayChannelMatrix**: 16-channel grid. Uses `relayViewModel.ts` for display logic.
- **relayViewModel.ts**: `getChannelDisplayName()` returns `display_name` for ALL devices. Falls back to `light_name` → `device_name`. `assignedDeviceName` uses canonical `device_name` for API calls.
- **RelayChannelBox**: Badge text `text-[20px]`, padding `px-2 py-1`.
- **ClimatePeriodTimeline**: Moon edge handles are `w-8`, `opacity-100`, `cursor-ew-resize`. Right-click moon band opens context menu for time editing. Ramp gradients (up/down) on SUN band edges (left=up, right=down).
- **API service**: `api.ts` `updateDevice()` return type includes `displaced_device_id?: number | null`.

## DEVELOPMENT

```bash
npm install
npm run dev    # port 3001
npm run build
sudo systemctl restart automation-service
```

## ENVIRONMENT

Single source of truth: `src/config/env.ts`. After Phase 3.4b all clients default to Caddy `:8080`; `VITE_*_URL` overrides are emergency escape hatches only.

```bash
# .env (gitignored)
VITE_API_BASE_URL=            # Override Caddy base
VITE_BACKEND_API_URL=         # Escape hatch: backend client
VITE_AUTOMATION_API_URL=      # Escape hatch: automation client
VITE_WEATHER_API_URL=         # Escape hatch: weather client
VITE_WEBSOCKET_URL=           # Escape hatch: WS URL
VITE_CEA_API_KEY=             # Build-time API key
VITE_GRAFANA_BASE_URL=        # REQUIRED for Grafana iframes
```

Monitoring embeds preserve Grafana time-range controls and `refresh=1s`. Optimize slow dashboards through SQL/panel work.

## ANTI-PATTERNS

| Never | Reason |
|-------|--------|
| Direct database access | Always use APIs |
| Hardcode URLs | Use environment variables |
| Skip input validation | Ranges enforced server-side |
| Commit `.env` files | Gitignored |
| Use port other than 3001 in dev | Conflicts with services |
| console.log in production | Use proper logging |

---

## GRAFANA DASHBOARDS

**Locations**: Provisioned from `/var/lib/grafana/dashboards/`. Sync: `Infrastructure/scripts/sync_to_iskra.sh`. Host: `projectcea_grafana` on `iskraprojectcea:3001`.

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

Last updated: 2026-07-12
