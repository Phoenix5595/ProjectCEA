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

## Shared Stores and Key Hooks

- `useControlSnapshot` (`src/hooks/useControlSnapshot.ts`) owns the shared `useSyncExternalStore` poller for `GET /api/devices/control-snapshot`; all device views read the same snapshot.
- Monitoring pages use `src/features/monitoring/state/monitoringStore.ts` (one store per room, 1 Hz live append) and `pages/useMonitoringStore.ts`.

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

## Where to Look

| Topic | Document |
|-------|----------|
| Parent / safety rules | `ProjectCEA/AGENTS.md` |
| Frontend contracts | `REQUIREMENTS.md` |
| Monitoring design & QA | `src/features/monitoring/AGENTS.md` |
| Iskra / Grafana stack | `Infrastructure/iskra_stack/AGENTS.md` |

## Anti-Patterns

- Hardcode API URLs outside `src/config/env.ts`.
- Poll device endpoints with sensor sub-clusters (`front`/`back`).
- Run fixture or browser tests against production hosts/ports.

---

*Last updated: 2026-08-10*
