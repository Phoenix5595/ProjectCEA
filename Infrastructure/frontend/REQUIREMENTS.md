# Frontend Requirements

Contracts for the React frontend. Architecture, service ports, and hardware boundaries are owned by `ARCHITECTURE.md` and `Infrastructure/REQUIREMENTS.md`.

## Stack and Entry Points

- React 18 + TypeScript + Vite + Tailwind.
- `npm run dev` serves on port 3001 with `server.host: "0.0.0.0"`. `server.allowedHosts` must include `.ts.net` for Tailscale MagicDNS.
- `npm run build` produces `dist/`, served by `automation-service` after deploy.
- Single source of truth for API URLs: `src/config/env.ts`. Defaults resolve to Caddy `:8080`; `VITE_BACKEND_API_URL`, `VITE_AUTOMATION_API_URL`, `VITE_WEATHER_API_URL`, and `VITE_WEBSOCKET_URL` are emergency escape hatches only.

## Grafana Embedding

The SPA embeds the production Grafana instance at `http://iskraprojectcea:3001`. `VITE_GRAFANA_BASE_URL` in `src/config/env.ts` defaults to that URL.

Embed requirements on the Grafana side (configured in `Infrastructure/iskra_stack/docker-compose.yml`):

- `GF_SECURITY_ALLOW_EMBEDDING=true`
- `GF_AUTH_ANONYMOUS_ENABLED=true` with `GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer`
- `GF_DASHBOARDS_MIN_REFRESH_INTERVAL=1s`
- `GF_DATE_FORMATS_DEFAULT_TIMEZONE=America/Toronto`

Dashboards and datasources are provisioned from the repo; the frontend relies on stable datasource UIDs (`bf6vebq5ipybke` for PostgreSQL, `bf9yw6nuqt81sa` for Redis). Do not change datasource UIDs without updating the embedding code.

Sensor display names in Grafana follow frontend mappings; backend sensor keys remain unchanged.

## Cluster Topology

`src/config/clusterTopology.ts` mirrors `Infrastructure/shared/cluster_topology.py` and is the single registry of room → device cluster + sensor sub-clusters.

- Poll `/api/devices/{room}/{cluster}` over `ZONES` (all `cluster: "main"`).
- Poll `/api/sensors/{room}/{cluster}` over `getSensorPollZones()`.
- Use `getDashboardPollZones()` only for the bulk-Redis-key fan-out, which mixes both planes.

## Dashboard Status

- Sensor freshness is scoped to `(location, cluster)`. Flower Front and Back render separate age, source, and quality badges next to the readings they qualify; a live cluster never upgrades another cluster’s status.
- Grow-mode labels use `GET /api/room-modes/active/{location}/{cluster}` for Flower Room/main and Veg Room/main. Do not use Redis control modes from `/api/mode` as grow modes. Lab has no grow mode; its dashboard card shows sensor freshness/readings and available trend labels/deltas, plus named devices in full cards or ON/OFF/unknown counts in the existing 768–1099px summary—never grow mode, decision, schedule, or setpoint placeholders.

## Dashboard Viewports and Navigation

- Web navigation is a permanent 30px icon rail on every route. Each icon link has an accessible label and title; there is no expanded desktop state, persisted collapse preference, hamburger, or mobile drawer.
- Dashboard QA in this pass uses exactly 1920×1080 and 1280×1440. The 960px redesign is deferred; no mobile-web layout guarantee or phone QA project is supported. A separate mobile app does not exist yet.
- At both supported dashboard QA sizes the page fits one viewport without document or in-panel scrolling or overlapping panels. Long lists and notes remain reachable through paging or transient detail dialogs; dialogs/popovers may scroll as overlays.
- The dashboard calendar always renders six complete weeks, keeps 44px minimum day-button targets, and exposes task, note, and phase counts in each date's accessible name.
- The compact dashboard Event Log always shows all eight canonical category groups; categories are not paginated. Flat and expanded event lists show five newest-first events per page with visible status and event-list controls.
- Detail payloads, long inspector notes, and forms remain available in accessible dialogs. Full-width room logs retain their existing inline-detail behavior and do not use dashboard pagination.

## Zone Configuration

A ZoneConfig SAVE performs three operations in order:

1. `PUT /api/room-modes/room/{location}/{cluster}/parameters`.
2. `POST /api/room-schedule/{location}/{cluster}` with photoperiod times and `ramp_up_duration` / `ramp_down_duration` derived from `light_ramp_up_minutes` / `light_ramp_down_minutes`.
3. `POST /api/climate-periods/{location}/{cluster}` with period rows.

Climate periods are keyed by `(location, cluster, mode_id, submode_id)`. Fetch them with the active `mode_id` and `submode_id` so the table shows only the active flower submode.

## Device Management

- Device registry CRUD is the only assignment mutation path.
- Flower Room devices always target `main`; `normalizeDeviceControlCluster` enforces this.
- DFR assignments are globally unique; conflicts are rejected.
- Relay steal requires operator confirmation after a 409 response.
- Relay labels come from the backend control snapshot (`physical_relay`, `pin_label`); no frontend `channel + 1` math.

## Lights

- Light intensity targets come from the DB SUN/DAY row.
- The editable sun target must match `day_target_intensity` / `schedule_sun_target_intensity` from zone-status, not only the scheduler nominal.
- Manual light controls are shown only in constant modes (`drying`, `sleep`).
- The light slider renders 0% at the right and 100% at the left.

## Monitoring

Native monitoring pages at `/flower/monitoring` and `/vegetation/monitoring` replace Grafana iframes. Visual and accessibility contracts live in `DESIGN.md`. Browser tests must not contact production endpoints; fixture origin and route guard assertions enforce this.

- Historical photoperiod points remain separate from `projectionHistory`. Forecast refreshes cannot recolor the past; tail history replaces only its own half-open overlap and preserves phase/provenance/metadata boundaries.
- Historical background coverage ends at the earlier of Now and the history response end. Forecast bands apply only inside their publication window at or after Now. Missing evidence, recording gaps, and expired publications remain UNKNOWN and unpainted.
- SUN bands use fixed `rgba(251, 191, 36, 0.12)` and MOON bands fixed `rgba(129, 140, 248, 0.12)` under the series, independent of theme. Their exact-time rectangles are clipped to the plot bbox; chart and panel geometry is unchanged.
- `tests/monitoring/photoperiod.spec.ts` exercises absolute historical fixtures, toolbar-applied ranges, zoom, the Now seam, and actual canvases under all six themes at 1920x1080 and 1280x1440 on the guarded loopback preview.

## Validation

Local verification gates are:

```bash
npx tsc --noEmit
npm run build
npx vitest run src/components/devices/__tests__/targetValidation.test.ts src/components/devices/__tests__/relaySnapshot.test.ts
```

Runtime validation uses UI behavior and health endpoints.

## Anti-Patterns

- Hardcode API URLs outside `src/config/env.ts`.
- Mix device and sensor sub-clusters in polling.
- Send `ramp_up_minutes` / `ramp_down_minutes` for the room-schedule POST.
- Commit `.env` files.
