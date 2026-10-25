# CEA Monitoring Feature

Native Flower and Veg dashboards at `/flower|vegetation/monitoring` (day-to-day operation) and `/flower|vegetation/historical` (analysis and retrospection). The in-house dashboards are the only monitoring surface; room history/overview/control reads go through the read-only `monitoring-service` (`:8005` via Caddy).

## Graph Rules (owner, binding)

Full text: `Infrastructure/frontend/REQUIREMENTS.md` → "Owner graph rules (R1–R6)". Summary for code changes here:

- **R1** ≤ 4000 points and ≤ 1 point per CSS pixel per displayed line. The page budget is the minimum plot-area width over mounted charts (`data/pointBudget.ts` `requestBudget`, `pages/useMonitoringRangeBudget.ts`); no fixed point constants.
- **R2** Monitoring: recorded + projected effective setpoints, future duration/9 area in live mode. Historical (`MonitoringStore` `mode: 'historical'`): recorded only, no projection request, no live polling, no future space.
- **R3** Drag-zoom stays on the current page (≥ 5 min). Only a Monitoring range > 7 days navigates to Historical.
- **R4** Live raw values enter through `data/liveTail.ts` (`LiveTailBuffer`, ≤ 1 raw point per bucket) and are replaced by completed buckets from incremental refreshes; the alignment base never keys on `now`. Bucket badge, tooltip span and min–max band make buckets visible.
- **R5** Sun/moon overlays come only from recorded/projected photoperiod, never from climate periods.
- **R6** `ChartDataTable` renders only in dev/fixture builds.

## Boundary Parsing

- `api/client.ts` is the only fetch surface. Every response is runtime-parsed with a Zod schema before it crosses the feature boundary.
- `api/contracts.ts` and `api/contracts/{sensor,control,shared}.ts` own the request/response types.
- `api/errors.ts` defines typed errors (timeout, network, HTTP, parse, abort).

## Ownership

| Concern | File(s) |
|---------|---------|
| Room manifests | `config/flowerManifest.ts`, `config/vegManifest.ts`, `config/manifestTypes.ts` |
| Store + 1 Hz live loop | `state/monitoringStore.ts`, `state/monitoringStore.poller.ts` |
| Control-state merge | `state/monitoringStore.control.ts`, `state/monitoringStore.merge.ts` |
| Series alignment | `data/alignSeries.ts`, `data/alignSeries.*.ts` |
| uPlot charts | `charts/UPlotChart.tsx`, `charts/options/`, `charts/plugins/` |
| Tables | `components/ChartDataTable.tsx`, `components/StatisticsTable.tsx`, `components/RoomAveragesTable.tsx`, `components/SensorValueTable.tsx` |
| Time-range toolbar | `components/TimeRangeToolbar.tsx` and inputs/time helpers |
| Design/accessibility tokens | `designTokens.ts`, `accessibilityPolicy.ts` |

## Fixture Origin Guard

- `config/originGuard.ts` is the single source of truth for allowed origins.
- The fixture preview binds to `127.0.0.1` on `MONITORING_FIXTURE_PORT` (default 4187; allowed range 4187–4199).
- **Production and fixture origins must never mix.** Fixture/browser tests must never contact service ports `8000`, `8001`, `8003`, or `8080;` the exact-origin guard rejects every non-fixture host.
- `tests/monitoring/network-guard.spec.ts` and `__tests__/monitoringBrowserHarness.test.ts` enforce this.

## Design and Accessibility

Visual and accessibility contracts live in `DESIGN.md` at the frontend root. `designTokens.ts` defines required CSS variables; `__tests__/designTokens.test.ts` and `accessibilityPolicy.ts` keep every theme and primitive in parity.

## Local Commands

```bash
cd Infrastructure/frontend
npx tsc --noEmit
npm run build
npx vitest run src/features/monitoring
npx playwright test --config=playwright.monitoring.config.ts
```

The Playwright config builds `dist/` and serves it through `vite.monitoring.config.ts` on the configured loopback fixture port with deterministic fixtures and a restrictive CSP.

## Anti-Patterns

- Let a test build reach a production service URL.
- Skip Zod parsing and pass `Response` objects into components.
- Add dashboard series without updating the manifest parity test (`__tests__/canonicalManifestParity.test.ts`).
- Reference any external dashboard product or host as the current monitoring surface.
