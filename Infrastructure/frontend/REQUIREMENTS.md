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
- Grow-mode labels use `GET /api/room-modes/active/{location}/{cluster}` for Flower Room/main and Veg Room/main. Do not use Redis control modes from `/api/mode` as grow modes. Lab has no grow mode; its dashboard card shows sensor freshness/readings and available trend labels/deltas, plus named devices at wide/mobile widths or ON/OFF/unknown counts in the 768–1099px summary—never grow mode, decision, schedule, or setpoint placeholders.

## Dashboard Viewports and Navigation

- Desktop navigation is a permanent 30px icon rail on every route. Each icon link has an accessible label and title; there is no expanded desktop state or persisted collapse preference. Below 768px the hamburger opens a labelled 208px drawer with visible link labels and the package-version footer; route changes and the close control dismiss it.
- Dashboard QA viewports are 1920×1080, 1280×1440, 960×1080, and Pixel 9 Pro XL at 448×997 CSS px, DPR 3. The 960 and phone projects apply only to `/`; monitoring page viewport gates remain unchanged.
- At all three desktop sizes the dashboard fits one viewport without document or in-panel scrolling and without overlapping panels. Long lists and notes remain reachable through paging or transient detail dialogs; dialogs/popovers may scroll as overlays.
- Pixel 9 Pro XL uses one-column document flow in DOM order. The document may scroll vertically; horizontal page scroll and internal dashboard-panel scrolling are not allowed.
- The dashboard calendar always renders six complete weeks and keeps 44px minimum day-button targets. Phone day cells show marker dots/counts; each date's accessible name includes task, note, and phase counts.
- Compact dashboard Event Log shows eight category groups per page and five newest-first events per flat/expanded page, with visible status and bounded controls. Detail payloads, long inspector notes, event lists, and forms remain available in accessible dialogs. Full-width room logs retain their existing inline-detail behavior and do not use dashboard pagination.

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
