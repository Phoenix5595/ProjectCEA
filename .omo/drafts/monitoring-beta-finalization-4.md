---
slug: monitoring-beta-finalization-4
status: approved
intent: clear
pending-action: execute .omo/plans/monitoring-beta-finalization-4.md in a separate worker session
approach: finish the existing native uPlot stack in the isolated candidate, close its backend/database dependencies, prove the full headed browser contract, then apply additive production migration and candidate deployment under explicit rollback gates
---

# Draft: monitoring-beta-finalization-4

## Components (topology ledger)

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Candidate branch contains every required source, migration, test, and deployment artifact and no unrelated dirty-root files | active | `.omo/evidence/monitoring-beta-finalization-4/task-1-*` |
| C2 | Native uPlot charts honor canonical manifests, axes, time ranges, UNKNOWN gaps, and stable status/store behavior | active | `.omo/evidence/monitoring-beta-finalization-4/task-{5,6,7}-*` |
| C3 | Monitoring projection/history services are wired without blocking or changing the deterministic 1-second control decisions | active | `.omo/evidence/monitoring-beta-finalization-4/task-{3,4}-*` |
| C4 | Additive TimescaleDB read models pass a disposable database harness and a supervised definitions/backfill/policy sequence | active | `.omo/evidence/monitoring-beta-finalization-4/task-{2,12,14,15,16}-*` |
| C5 | Full deterministic monitoring Playwright suite runs visibly with `headless:false`, one worker, display `:0`, and zero production network access | active | `.omo/evidence/monitoring-beta-finalization-4/task-{8,11}-*` |
| C6 | BETA pages deploy through the candidate workflow while Grafana primary routes remain unchanged and rollback remains available | active | `.omo/evidence/monitoring-beta-finalization-4/task-{13,17,18}-*` |

## Open assumptions (announced defaults)

| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Production database credentials and operator identity | Use the repository/deployment host's established non-printed credential path; never embed credentials in commands or evidence | Prevents secret disclosure and follows existing operational boundaries | yes |
| Historical photoperiod before observation starts | Return/render `UNKNOWN`, creating no synthetic SUN/MOON backfill | User explicitly rejected fabricated control history | yes, but only with a later authoritative data source |
| Aggregate backfill batching | Discover source bounds first and refresh chronologically in small supervised windows, recording one marker only after each aggregate reaches the captured high-water mark | Bounds lock duration and allows pause/retry without policy activation | yes |
| Full Playwright route contract | Point monitoring-owned specs at additive `/beta-monitoring` routes; retain explicit checks that primary `/monitoring` routes remain Grafana-backed | Matches approved additive rollout | yes |
| Production browser behavior | Headed browser is an owner-visible verification gate, while all requests are intercepted/recorded and mutating HTTP methods are aborted | Satisfies visible QA without production mutation | yes |

## Findings (cited - path:lines)

- The candidate branch advanced from the feature commit to documentation commit `16b0a5d30b39bff6be0ff63a386b491cd97a4648`; the branch reflog preserves the feature and deployment-isolation commits (`.git/logs/refs/heads/monitoring-beta-production-candidate:1-5`).
- The deterministic fixture is same-origin at `http://127.0.0.1:4173`, starts a fresh Vite preview, and currently permits parallel workers (`Infrastructure/frontend/playwright.monitoring.config.ts:18-55`). Revision 4 must retain the origin but override to one worker and visible Chromium.
- Every current browser spec still targets primary `/flower/monitoring` or `/vegetation/monitoring`, so an additive BETA release needs an explicit route-contract rewrite (`Infrastructure/frontend/tests/monitoring/{flower,veg,failures,parity,visual-a11y,performance}.spec.ts`).
- The old smoke script is visible but targets `localhost:3001`, waits forever, and is not the full suite (`Infrastructure/frontend/_pw-beta-smoke.mjs:15-103`); it cannot be used as the release gate.
- `MonitoringStore` guarantees a default 1,000 ms poll interval, but subscriber startup and range actions independently call `initialLoad`, so lifecycle races need focused regression coverage (`Infrastructure/frontend/src/features/monitoring/state/monitoringStore.ts:18-18,61-77,93-115,178-211`).
- The toolbar accepts a minimum 5-minute fixed range while presets start at 1 hour (`Infrastructure/frontend/src/features/monitoring/components/timeRangeToolbar.time.ts:27-33,135-177`).
- `buildScales` omits the x-axis and reverses the requested placement by assigning temperature side 1 and other families side 3; uPlot uses side 3 for left and side 1 for right (`Infrastructure/frontend/src/features/monitoring/charts/options/scales.ts:31-60`; uPlot options reference).
- Canonical manifests already contain per-series color, style, width, interpolation, decimals, and soft bounds, but aligned series and `buildSeries` discard those fields in favor of family defaults (`Infrastructure/frontend/src/features/monitoring/config/manifestTypes.ts:19-53`; `Infrastructure/frontend/src/features/monitoring/data/alignSeries.types.ts:38-50`; `Infrastructure/frontend/src/features/monitoring/charts/options/seriesOptions.ts:63-91`).
- `alignPhotoperiod` ignores UNKNOWN points and thereby lets the previous known phase fill an unknown interval (`Infrastructure/frontend/src/features/monitoring/data/alignSeries.series.ts:146-167`).
- Dismissible errors are keyed only by message text, so duplicate source errors collide (`Infrastructure/frontend/src/features/monitoring/components/MonitoringStatus.tsx:65-96`).
- The history logger is bounded and non-blocking (`put_nowait`, 256-row queue, 64-row batches), but the control engine only offers observations around moon-authority paths; normal final SUN/MOON decisions are missing from history (`Infrastructure/automation-service/app/services/photoperiod_history_logger.py:25-29,98-159`; `Infrastructure/automation-service/app/control/control_engine.py:208-234` plus current call sites).
- The database definitions are transactional, additive, materialized-only, and guarded to TimescaleDB 2.23-2.28.x; policy activation refuses to proceed without all six supervised backfill markers and adequate watermarks (`Infrastructure/database/monitoring_read_models.sql:1-149,316-365,517-556`; `Infrastructure/database/monitoring_read_models_activate_policies.sql:1-52`).
- The two SQL files and projection/logger dependencies exist only in the dirty root and are absent from the clean candidate; their bytes must be hashed and copied deliberately, never inherited through a broad dirty-worktree copy.
- The existing database harness targets a disposable Postgres/TimescaleDB environment and is the only acceptable automated SQL test boundary (`Infrastructure/database/AGENTS.md:45-47`; `Infrastructure/database/tests/test-monitoring-read-models.sh`).
- Candidate deployment already protects active candidate/last-good/rollback states and is covered by nine sandbox scenarios (`Infrastructure/scripts/tests/test-deploy-candidate.sh:198-359`).

## Decisions (with rationale)

1. Keep direct uPlot. The implementation, manifests, stores, fixtures, and tests already exist; changing libraries would multiply risk without solving the concrete defects.
2. Preserve additive BETA routing. Grafana remains the primary fallback at `/flower/monitoring` and `/vegetation/monitoring`; native pages stay at `/flower/beta-monitoring` and `/vegetation/beta-monitoring` until a later promotion decision.
3. Preserve exactly 1-second live refresh. The store default remains 1,000 ms and browser performance tests must prove no historical reload per tick.
4. Make canonical manifests the presentation authority. Distinct sensor/node colors and declared line/axis/bounds semantics flow into aligned series and uPlot options; family colors are only axis/semantic fallbacks.
5. Render one y-axis per metric family: temperature left, every other family right, with a visible x-axis and the primary family color.
6. Treat UNKNOWN as a first-class neutral interval. No inheritance across unknown history and no fabricated pre-observation SUN/MOON data.
7. Record every final resolved room phase after the control decision through the existing non-blocking queue. The hook may observe but must not alter, await, delay, or recompute relay/DFR decisions.
8. Migrate in three production gates: definitions, supervised bounded backfill with durable markers, then policy activation. Each gate is independently verified; policy activation is forbidden if any marker/watermark lags the captured source high-water mark.
9. Run all monitoring Playwright specs visibly with `headless:false`, `workers:1`, display `:0`, fresh same-origin fixture, and machine-verifiable nonzero discovery/pass counts. No Xvfb or headless fallback.
10. Deploy only the frozen clean candidate through the existing candidate/finalize/rollback workflow. Production verification is GET-only; any failed gate rolls back the application candidate while database additions remain dormant and additive.

## Scope IN

- Candidate reconciliation and exact changed-path/dependency manifest.
- Missing monitoring SQL, projection/history services, container wiring, routes/contracts, and focused tests.
- Chart presentation, axes, range, UNKNOWN gap, duplicate-error, and initial-load lifecycle fixes.
- Complete monitoring Vitest, Python pure, disposable SQL, TypeScript/build, and headed Playwright gates.
- Supervised additive production definitions/backfill/policy activation.
- Candidate deployment, GET-only production verification, finalize-or-rollback, architecture documentation.

## Scope OUT (Must NOT have)

- No replacement/removal of Grafana primary routes, no unified or Laboratory native page, no unrelated redesign.
- No change to control cadence, PID/VPD/light authority, relay/DFR command calculation, hardware addresses, or device registry.
- No synthetic historical SUN/MOON rows and no direct browser-to-database access.
- No destructive SQL, table truncation, schema drop, source-row rewrite, production test harness, or automated production mutation.
- No Xvfb/headless browser fallback, zero-test “green” result, fixture request to production ports, or broad browser matrix.
- No deployment from the dirty root, no staging/reset/clean/stash of unrelated files, and no production-side source editing.

## Open questions

None. The user approved the architecture, BETA route strategy, chart/axis contract, observation-only logger hook, additive supervised migration, 1-second refresh, full headed suite, and final verification wave.

## Approval gate

status: approved

User-approved constraints to carry into execution:

- “I want a 1second refresh rate. this is non negocitable since the fastest sensors send data at that rate”
- “ask questions instead of assuming.”
- Browser gate: `headless: false`, complete monitoring suite, real display `:0`, no fallback.
- Production authorization covers the observation-only hook and supervised additive database migration described above; it does not authorize destructive SQL or unrelated production mutation.
