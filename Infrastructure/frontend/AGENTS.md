# CEA Frontend

React 18 + TypeScript + Vite + Tailwind. The SPA is served as static `dist/` by `automation-service` after deploy.

## Entry Flow and Routes

`src/App.tsx` lazy-loads pages under `BrowserRouter` + `Layout`:

- `/` dashboard
- `/laboratory`, `/laboratory/climate`, `/laboratory/water`, `/laboratory/infrastructure`
- `/vegetation`, `/vegetation/monitoring`, `/vegetation/control`, `/vegetation/automation`
- `/flower`, `/flower/monitoring`, `/flower/control`, `/flower/automation`, `/flower/soil`
- `/devices` — device registry and relay/DFR management
- `/settings/calendar` — grow-calendar configuration

Native monitoring dashboards live at `/flower/monitoring` and `/vegetation/monitoring` and are owned by `src/features/monitoring/`.

## API / Generated Contract Boundaries

- `src/config/env.ts` is the single source of truth for API base URLs. Default routing goes through Caddy `:8080`; per-service `VITE_*_URL` overrides are emergency escape hatches only.
- `src/services/api.ts` builds the three axios clients (backend, automation, weather) and attaches domain method modules (`services/api/{devices,sensors,schedules,pid}.ts`).
- `src/generated/api.ts` is the OpenAPI-generated TypeScript contract. Regenerate it with `npm run api:generate`.
- `src/config/clusterTopology.ts` mirrors `Infrastructure/shared/cluster_topology.py`: device cluster is always `main`; Flower Room has sensor sub-clusters `front` and `back`; unsplit rooms reuse `main` as a sensor URL sentinel.
- Operational event SSE uses `fetch` with `Accept: text/event-stream` and `X-API-Key`. Native `EventSource` and query-token keys are not used.

## Shared Stores and Key Hooks

- `useControlSnapshot` (`src/hooks/useControlSnapshot.ts`) owns the shared `useSyncExternalStore` poller for `GET /api/devices/control-snapshot`; all device views read the same snapshot.
- Monitoring pages use `src/features/monitoring/state/monitoringStore.ts` (one store per room, 1 Hz live append) and `pages/useMonitoringStore.ts`.
- Event log store: `src/features/event-log/state/eventLogStore.ts`. One global store merges history and live rows by Redis ID and event UUID, caps resident rows at 5,000, and supports cursor reset, reconnect, and auth pause.
- Event log transport: `src/features/event-log/state/eventLogTransport.ts`. Loads the latest 200 rows, tails `/api/events/stream`, reconnects with backoff, and halts retries on 401/403.
- Presentation registry: `src/features/event-log/presentation/eventRegistry.ts` maps known event types to labels and severity.
- Dashboard integration: `src/pages/Dashboard.tsx` and `src/components/dashboard/DashboardZoneRow.tsx` mount the shared log with all-room filters.
- Room overview integration: `CalendarOverviewPage.tsx` (and the wrappers for Flower, Vegetation, Laboratory) mount the same shared log with a fixed room filter.

## Local Commands

```bash
cd Infrastructure/frontend
npm install
npm run dev        # port 3001
npm run build
npm run api:generate
npx tsc --noEmit
npx vitest run src/components/devices/__tests__/targetValidation.test.ts src/components/devices/__tests__/relaySnapshot.test.ts
```

Event-log verification also uses:

```bash
cd Infrastructure/frontend
npx tsc --noEmit
npm run build
npx vitest run src/features/event-log/__tests__
```

## Where to Look

| Topic | Document |
|-------|----------|
| Parent / safety rules | `ProjectCEA/AGENTS.md` |
| Frontend contracts | `REQUIREMENTS.md` |
| Monitoring design & QA | `src/features/monitoring/AGENTS.md` |
| Operational event runbook | `Infrastructure/automation-service/app/events/AGENTS.md` |
| Iskra / Grafana stack | `Infrastructure/iskra_stack/AGENTS.md` |

## Anti-Patterns

- Hardcode API URLs outside `src/config/env.ts`.
- Poll device endpoints with sensor sub-clusters (`front`/`back`).
- Run fixture or browser tests against production hosts/ports.
- Use native `EventSource` or query-string API keys for the operational event stream.
- Drive any control action from an event-log consumer.

---

*Last updated: 2026-09-03*
