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
| CircularTimePicker | `components/CircularTimePicker.tsx` | Radial time selection |
| LightIntensity | `components/LightIntensity.tsx` | ZoneConfig dimmable light targets (CUR/TGT) |
| DeviceTable | `components/devices/DeviceTable.tsx` | Canonical device CRUD; relay steal confirmation; DFR conflict display |
| DeviceManager | `components/DeviceManager.tsx` | Tabbed container; subscribes to shared control snapshot store |
| RelayChannelMatrix | `components/devices/RelayChannelMatrix.tsx` | 16-channel relay grid; main matrix allows unassigned raw timers; room matrices grey foreign/unassigned tiles |
| RelayChannelBox | `components/devices/RelayChannelBox.tsx` | Single relay channel box; LED shows observed MCP state; button shows command/mode/sync/stale/alarm |
| DfrBoardsPanel | `components/devices/DfrBoardsPanel.tsx` | DFR status/Test/Rename; assignment editing lives only in DeviceTable |
| useControlSnapshot | `hooks/useControlSnapshot.ts` | Module-owned `useSyncExternalStore` with one 1s poller, one in-flight request, last-good retention, and `refreshNow()` after mutations |

## COMPONENT DETAILS

- **DeviceTable**: Inline editing for display name, optional relay, and light DFR pair. Relay steal requires operator confirmation (`confirmed_relay_steal=true` after a 409 conflict). DFR conflicts are rejected and shown as owner details. Delete uses `X-Confirm-Destructive` header. Success calls `refreshNow()` on the shared store.
- **DeviceManager**: Tabbed container that subscribes to the shared control snapshot store; all child views read the same snapshot.
- **RelayChannelMatrix**: 16-channel grid. Main matrix permits timed raw ON for unassigned channels. Room matrices render all sixteen tiles but disable foreign and unassigned channels. All relay labels come from the backend composite snapshot (`physical_relay`, `pin_label`); no frontend `channel + 1` math.
- **relayViewModel.ts**: Builds view models directly from `ControlSnapshotResponse.relays`. LED uses `observed_state`; the control button separately renders `AUTO`, `MANUAL OFF`, `TIMED_ON` countdown, `SYNCING`, `STALE`, or mismatch alarms.
- **RelayChannelBox**: While the relay board is `STALE`, only OFF actions are enabled.
- **DfrBoardsPanel**: Always renders DFR boards 0–2 with two channels each. Shows `commanded_intensity` (acknowledged cached command, not physical voltage) and `command_acknowledged`. Test and Rename only; create/assign/move/delete remain in DeviceTable.
- **API service**: `services/api/devices.ts` defines the frontend-mirror command union (`AutoCommand | ManualOffCommand | TimedOnCommand`) and calls `POST /api/devices/{location}/{cluster}/{device}/command` for assigned-device control. `updateDevice()` exposes 409 conflict details for relay and DFR assignments.

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

Last updated: 2026-07-30 (relay-registry-control-snapshot-recovery implementation complete; deployment pending owner approval)
