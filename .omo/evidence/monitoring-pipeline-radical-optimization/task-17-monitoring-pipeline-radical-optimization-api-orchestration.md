# T17 — frontend API orchestration and statistics traffic

## Implemented facts

- `sensorRange`, `sensorStats`, and recorded `controlRange` history accept an optional positional `maxPoints` before request options and serialize it as `max_points` only when supplied. Canonical room slugs still pass through `encodeURIComponent`, so `Flower Room` remains `Flower%20Room`.
- Exported interim budgets are `SENSOR_RANGE_MAX_POINTS = 2000` and `CONTROL_HISTORY_MAX_POINTS = 1000`. The range loader supplies both budgets; the reconciliation history read also receives the control budget.
- The backend control projection route accepts only `start` and `end` (`Infrastructure/monitoring-service/monitoring_service/control_routes.py:63-77`), so projection requests intentionally remain unbudgeted.
- Successful statistics responses are cached by the store's existing selected-range identity. A retry of the same range reuses the cached statistics while range/history/projection retain their existing retry behavior. Failed statistics responses are not cached, preserving retry recovery.

## Test characterization

- Added URL assertions for encoded Flower Room sensor range, stats, and control history budget parameters; existing no-budget URL assertions retain legacy absence coverage.
- Added store assertions for the 2000/1000 initial budgets and a retry of an unchanged range that reloads sensor history without a second statistics request. Existing sixty-second live-tail coverage confirms one statistics request across live ticks.
- Red run: 3 intended failures (missing sensor `max_points`, missing store budgets, and retry statistics refetch).
- Focused green run: `npx vitest run src/features/monitoring/api/__tests__/monitoringApi.test.ts src/features/monitoring/state/__tests__/monitoringStore.test.ts` — 2 files, 23/23 tests passed.

## Required gates

- Pending final frontend typecheck, build, full monitoring Vitest suite, and production-dist performance-marker scan.

## Required gate results

- `npx tsc --noEmit` — passed (exit 0).
- `npm run build` — passed (exit 0; 1,602 modules transformed).
- `npx vitest run src/features/monitoring` — passed: 19 files, 86/86 tests.
- Production `dist` scan — zero matches for both `__monitoringPerf` and `VITE_MONITORING_PERF`.
- LSP diagnostics — clean for all six changed TypeScript source/test files.

## T17 correction — embedded statistics range loading

- Removed the range loader's `sensorStats` request and `statisticsByRange` cache. Each range load now issues exactly three requests: sensor range, control history, and control projection; statistics come from `sensorRange.statistics`.
- Updated partial failure handling and store tests so a failed sensor range preserves last-good statistics only alongside the range error metadata. The standalone `MonitoringApi.sensorStats` method remains available.
- Focused API/store verification passed: 2 files, 24/24 tests. Full `npx vitest run src/features/monitoring` reached 92/94: 17 files passed; the two failures are pre-existing chart tests outside this correction's allowed scope.
- `npx tsc --noEmit` passed. `npm run build` passed with 1,603 modules transformed. Production `dist` marker scan is recorded after this correction.
