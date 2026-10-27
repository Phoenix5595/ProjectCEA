# Frontend Requirements

Contracts for the React frontend. Architecture, service ports, and hardware boundaries are owned by `ARCHITECTURE.md` and `Infrastructure/REQUIREMENTS.md`.

## Stack and Entry Points

- React 18 + TypeScript + Vite + Tailwind.
- `npm run dev` serves on port 3001 with `server.host: "0.0.0.0"`. `server.allowedHosts` must include `.ts.net` for Tailscale MagicDNS.
- `npm run build` produces `dist/`, served by `automation-service` after deploy.
- Single source of truth for API URLs: `src/config/env.ts`. Defaults resolve to Caddy `:8080`; `VITE_BACKEND_API_URL`, `VITE_AUTOMATION_API_URL`, `VITE_WEATHER_API_URL`, and `VITE_WEBSOCKET_URL` are emergency escape hatches only.

## Monitoring API Boundary

Room monitoring reads (history, overview, control) go to the in-house read-only `monitoring-service` (`:8005`, behind Caddy `:8080`). `MONITORING_API_URL` in `src/config/env.ts` defaults to the Caddy base; there is no external dashboard product and no direct browser-to-database access.

Sensor display names in the monitoring manifests follow frontend mappings; backend sensor keys remain unchanged.

## Cluster Topology

`src/config/clusterTopology.ts` mirrors `Infrastructure/shared/cluster_topology.py` and is the single registry of room → device cluster + sensor sub-clusters.

- Poll `/api/devices/{room}/{cluster}` over `ZONES` (all `cluster: "main"`).
- Poll `/api/sensors/{room}/{cluster}/live` over `getSensorPollZones()`.
- Use `getDashboardPollZones()` only for the bulk-Redis-key fan-out, which mixes both planes.

## Dashboard Status

- Sensor freshness is scoped to `(location, cluster)`. Each cluster's Front/Back (or Climate) tag sits left of its readings; a stale or missing cluster gets the red alert theme plus a hover/screen-reader line with its last good reading (Toronto clock), age and source instead of a badge. A live cluster never upgrades another cluster’s status.
- Grow-mode labels use `GET /api/room-modes/active/{location}/{cluster}` for Flower Room/main and Veg Room/main. Do not use Redis control modes from `/api/mode` as grow modes. Lab has no grow mode; its dashboard card shows sensor freshness/readings and available trend labels/deltas, plus named devices in full cards or ON/OFF/unknown counts in the existing 768–1099px summary—never grow mode, decision, schedule, or setpoint placeholders.

## Dashboard Viewports and Navigation

- Web navigation is a permanent 30px icon rail on every route. Each icon link has an accessible label and title; there is no expanded desktop state, persisted collapse preference, hamburger, or mobile drawer.
- Sector top bar: the room name links to the sector root (`aria-current="page"` on that page) and the tabs are Monitoring, Control, Automation, Soil (Flower only), Historical — there is no Overview tab. `/laboratory` and `/devices` show the room-name link only.
- Dashboard QA in this pass uses exactly 1920×1080 and 1280×1440. The 960px redesign is deferred; no mobile-web layout guarantee or phone QA project is supported. A separate mobile app does not exist yet.
- **R7 No-scroll.** At both supported dashboard QA sizes the overview pages (`/`, `/flower`, `/vegetation`, `/laboratory`) fit one viewport without document or panel scrolling or overlapping panels. From 768 px wide up they keep a 1280×720 minimum layout and never document-scroll at ≥1280×720; only the event-log body, the dashboard calendar inspector and a room overview's inline day detail scroll internally. Long lists and notes remain reachable through paging or transient detail dialogs; dialogs/popovers may scroll as overlays.
- The dashboard calendar always renders six complete weeks, keeps 44px minimum day-button targets at viewport heights ≥900px, and exposes task, note, and phase counts in each date's accessible name.
- **R8 Event log.** No event log has filter controls, a room selector, a view switch or an event total. The compact dashboard Event Log shows one card per canonical category — eight at viewport heights ≥ 1300 CSS px and six below 1300 CSS px, updating on resize. The six-card view merges Ramp into Control and Mutation into System without dropping events, and each card shows one detail line from its latest event. Categories are not paginated; a card opens that category's paged list, where five newest-first events show per page with visible status and event-list controls. The dashboard log carries every room; a room overview's log is automatically scoped to its room by data (`useEventLog({ location, cluster })`), flat and newest-first. Server-side `/api/events/history` filters remain for API clients.
- Detail payloads, long inspector notes, and forms remain available in accessible dialogs. Full-width room logs retain their existing inline-detail behavior and do not use dashboard pagination.

## Zone Configuration

A ZoneConfig SAVE performs three operations in order:

1. `PUT /api/room-modes/room/{location}/{cluster}/parameters`.
2. `POST /api/room-schedule/{location}/{cluster}` with photoperiod times and `ramp_up_duration` / `ramp_down_duration` derived from `light_ramp_up_minutes` / `light_ramp_down_minutes`.
3. `POST /api/climate-periods/{location}/{cluster}` with period rows.

Climate periods are keyed by `(location, cluster, mode_id, submode_id)`. Fetch them with the active `mode_id` and `submode_id` so the table shows only the active flower submode.

## Device Management

- Device registry CRUD is the only assignment mutation path.
- **R10 Relay matrix.** Dragging an assignment onto an empty relay moves it; onto an occupied relay swaps the two. Both channels must confirm OFF before any binding changes, otherwise nothing is rebound and the API returns HTTP 503. The control tick, manual device commands, raw channel writes, relay tests and registry mutations hold the shared actuation lock, so none of them interleaves with a move. The move route is `PUT /api/devices/registry/{device_id}/relay` with body `{ channel }`; it returns the moved device/channel and the swapped device/channel (null when the target was empty).
- Flower Room devices always target `main`; `normalizeDeviceControlCluster` enforces this.
- DFR assignments are globally unique; conflicts are rejected.
- Relay steal requires operator confirmation after a 409 response.
- Relay labels come from the backend control snapshot (`physical_relay`, `pin_label`); no frontend `channel + 1` math.
- DFR0971 board names are edited only in `DfrBoardsPanel`. System Settings shows board ID (read-only) and board address but no name input; a board-address save resends the stored name because `DfrBoardUpdate.name` is still required by the API.
- System Settings saves nested settings objects (for example `safety_limits`) as one `PUT /api/settings/automation`; the mutation event lists each changed leaf as a dotted key (`safety_limits.max_temperature`) with its before/after value.

## Soil Sensors

- **R9 Soil RS-485.**
  - Sensor Settings has one sub-tab page per bus: **CAN bus** (commissioning, identity confirmation, room/position form) and **RS485** (placement only, no live values), selected by `/devices?tab=sensors&settings_tab=can|rs485`; a missing or unknown value opens CAN, and switching keeps the other query parameters. The Flower Soil page toolbar has a permanent **RS485 settings** button that opens the RS485 page.
  - The Modbus address is the probe's device ID. A new probe is connected alone at factory address 1; the soil service writes into it the lowest address in 2–254 that never appeared in `sensor_registry` or `sensor_address_history` and registers it there. Address 1 is never a device ID. RS-485 rows are never pending.
  - Never-placed, unknown and disconnected probes are Unassigned.
  - A probe absent for 10 minutes loses its placement (closed as `disconnected`). When it answers again it returns to its last spot only if that placement was closed by the disconnect and no placed probe in the same bed lies within 1 ft; a Veg probe returns to Veg Room; otherwise it stays Unassigned.
  - Flower Room has two separate 4×4 ft beds side by side (Back Bed left, Front Bed right) with free placement. Veg Room placement is room-level.
  - Placement: drag a probe tag into a bed (drop position = `x_frac`/`y_frac`), onto Veg Room or onto Unassigned, or use the per-row `Unassigned` / `Back Bed` / `Front Bed` / `Veg Room` buttons. Routes: `PUT /api/sensors/registry/{id}/assignment` with `{kind: 'rs485', room: 'Flower Room', bed, x_frac, y_frac}` or `{kind: 'rs485', room: 'Veg Room'}`; `DELETE /api/sensors/registry/{id}/assignment` unassigns.
  - The RS485 page shows the bus banner from `GET /api/sensors/registry/rs485/bus-status`: `power_cycle_required` (with the reserved address), `collision` (several probes on address 1) or `no_free_address`.
  - The Soil-page heatmap (`GET /api/sensors/registry/rs485/heatmap?metric=`) gives current positions full weight; past positions fade linearly to zero over 72 h. Off / Moisture / Temperature / EC / pH, default Moisture.

## Lights

- Light intensity targets come from the DB SUN/DAY row.
- The editable sun target must match `day_target_intensity` / `schedule_sun_target_intensity` from zone-status, not only the scheduler nominal.
- Manual light controls are shown only in constant modes (`drying`, `sleep`).
- The light slider renders 0% at the right and 100% at the left.

## Monitoring

Native room Monitoring and Historical pages (`/flower/monitoring`, `/flower/historical`, `/vegetation/monitoring`, `/vegetation/historical`) are the in-house dashboards; room history/overview/control reads go through `monitoring-service` (`:8005` via Caddy). Visual and accessibility contracts live in `DESIGN.md`. Browser QA must use the guarded loopback fixture preview and must not contact production endpoints.

### Owner graph rules (R1–R6)

- **R1 Point budget.** Per displayed line: at most 4000 points (`MAX_POINTS_PER_LINE`) and at most 1 point per CSS pixel of the plot area. Grafana model: `max_points` = plot width (`u.bbox.width / uPlot.pxRatio`), interval = range ÷ max_points snapped to the nice ladder, minimum interval = the sample period. One room request serves every panel, so the page budget is the **minimum** plot width over the mounted charts. The first range load waits for a measured budget (placeholder frame width − 48 px per y axis − 16 px); later reports re-request only when the budget moves by more than 10 % (500 ms debounce). Sensor range, control history, control tail, the Historical overview, soil history and the relay/PID timeline all send or apply that budget; no fixed 1000/2000-point constants remain.
- **R2 Monitoring vs Historical.** Monitoring and Historical are separate pages with the same graph rules, except: Monitoring shows recorded **and projected** effective setpoints and reserves the future duration/9 area in live mode; Historical shows recorded effective setpoints only and never requests a projection, polls live values, or reserves future space. Historical is for analysis and retrospection, not day-to-day operation.
- **R3 Zoom.** A drag-zoom always zooms in place on the current page, at any resulting span ≥ 5 min. On Monitoring a selection ending at or after now becomes a live range of `max(5 min, now − selection start)` that keeps following live; a selection entirely before (or entirely after) now becomes a fixed range, and **Now** returns to live with the last live duration. On Historical a selection becomes the exact fixed range (end extended to the 5-minute floor) and **Reset zoom** pops the page-held zoom stack. The only cross-page navigation: requesting a Monitoring range longer than 7 days opens Historical with that range.
- **R4 Live data and buckets.** Live raw values are appended at the right edge (at most one raw point per bucket, `LiveTailBuffer`) and later replaced by completed buckets, bucketed view or not. Live mode refreshes sensor history incrementally (overlap of two buckets, at most every 5 s) and does a full reload on range change, budget change, or every 15 min. Bucket spans are visible: each chart header shows a `Buckets: <span>` badge (`raw` when unbucketed), the tooltip shows the bucket time span, sample count and min–max, and bucketed sensor lines draw a min–max band (none for single-sample buckets, the live tail, or setpoints).
- **Stale banner.** The range error clears on any successful sensor-history read (full or incremental) and on a successful control-history full read. A healthy control tail keeps control freshness current; after a failed full read, the first successful tail triggers an immediate full reconcile, so a recovered outage clears within about a second while a persistent history outage stays visible (retries every 30 s). A failed fixed-range read retries after 5 s, 15 s, then every 60 s.
- **R5 Photoperiod vs climate.** Photoperiod (SUN/MOON) and climate periods are independent; nothing derives one from the other. Sun/moon overlays come only from recorded or projected photoperiod.
- **R6 Data tables.** "View data as table" exists only in dev/fixture builds (`import.meta.env.DEV` or `VITE_MONITORING_DEBUG=1`); production builds render no button. The chart legend keeps its hidden semantic table.

- Historical photoperiod points remain separate from `projectionHistory`. Forecast refreshes cannot recolor the past; tail history replaces only its own half-open overlap and preserves phase/provenance/metadata boundaries.
- Historical background coverage ends at the earlier of Now and the history response end. Forecast bands apply only inside their publication window at or after Now. Missing evidence, recording gaps, and expired publications remain UNKNOWN and unpainted.
- SUN bands use fixed `rgba(251, 191, 36, 0.09)` and MOON bands fixed `rgba(129, 140, 248, 0.09)` (three quarters of the former 0.12, owner decision 2026-10-25) under the series, independent of theme, on every timeseries graph (Monitoring, Historical detail and overview, soil history, relay/PID timeline). The Control-tab climate timeline keeps its own stronger bands. Their exact-time rectangles are clipped to the plot bbox; chart and panel geometry is unchanged. Bucketed sensor min–max bands are painted over this backdrop, so where a wide band (typically RH) covers the plot the sun/moon tint reads weaker than in the empty future area; this is expected.
- `tests/monitoring/photoperiod.spec.ts` exercises absolute historical fixtures, toolbar-applied ranges, zoom, the Now seam, and actual canvases under all six themes at 1920x1080 and 1280x1440 on the guarded loopback preview.
- Both room views render environmental sensors, effective climate setpoints (heating/cooling/VPD targets) and per-device light intensity. Relay/device state, requested PID and duty belong to Automation. Room control history/tail requests use `scope=room` (climate setpoints + lights + photoperiod); the Historical overview adds `scope=climate` for setpoints and photoperiod; soil history and the relay/PID timeline read `scope=photoperiod` for sun/moon overlays.
- Historical presets are 5 days, 7 days, 2 weeks, one Toronto calendar month, three Toronto calendar months, and Current run; every preset stays on Historical. Current run uses the unique canonical calendar phase-group span for the room containing Toronto today, starts at local midnight, and ends at Now; absence/ambiguity never falls back to a guessed range. Fixed ranges up to 7 days use the detailed reader; longer ones use the overview.
- Long-view buckets are the finest whole-hour width from 1–12 hours that fits the actual intersecting bucket count, including clipped edges. The overview requests its measured plot-width budget (capped at 4000); the backend's generic default remains 600. Never widen beyond 12 hours or modify detailed sensor/Automation bucket policies to satisfy a long-view budget.
- Min/max envelopes and sensor statistic-table extrema remain available, including shorter bucketed ranges. Raw light ramp/step geometry and unknown gaps are preserved rather than averaged. The statistics table beneath the graphs is sensor-only.
- Keep the established 780px climate and 320px atmosphere/light chart regions. Their wrappers provide plot geometry and pointer interaction, not internal scrolling. R7 limits document scrolling to widths between 768 px and the 1280×720 floor; only the event-log body, the dashboard calendar inspector and a room overview's inline day detail scroll internally.
- Deployment requires verified Historical source coverage, not just service `/ready`. This release must not enable calendar compaction, raw deletion, age compression or Nextcloud/archive export.

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
