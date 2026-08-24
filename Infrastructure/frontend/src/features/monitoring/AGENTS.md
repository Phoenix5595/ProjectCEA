# CEA Monitoring Feature

Native Flower and Veg monitoring dashboards at `/flower/monitoring` and `/vegetation/monitoring`. Replaces Grafana iframes for those rooms.

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
- Fixture build and browser harness target exactly `http://127.0.0.1:4173`.
- **Production and fixture origins must never mix.** Fixture/browser tests must never contact ports `8000`, `8001`, `8003`, `8080` or host `iskraprojectcea`.
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

The Playwright config builds `dist/` and serves it through `vite.monitoring.config.ts` on `127.0.0.1:4173` with deterministic fixtures and a restrictive CSP.

## Anti-Patterns

- Let a test build reach a production service URL.
- Skip Zod parsing and pass `Response` objects into components.
- Add dashboard series without updating the manifest parity test (`__tests__/canonicalManifestParity.test.ts`).
- Reference old Grafana hosts (`localhost:3000`, `iskradocker:3000`) as current.
