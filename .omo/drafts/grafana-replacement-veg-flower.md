# Grafana Replacement — Veg + Flower Draft

## Planning state

- intent: clear
- review_required: true
- size: architecture
- status: final-plan-ready-for-owner-start
- pending_action: user starts implementation with `/start-work` using `.omo/plans/grafana-replacement-veg-flower.md`
- implementation_authorized: false

## Components ledger

1. Monitoring sensor read API — historical tiers, min/max envelopes, exact statistics, and live append contract. Status: grounded. Evidence: `Infrastructure/backend/app/routes/sensors.py`, `Infrastructure/backend/app/repositories/sensor_repository.py`, `Infrastructure/database/grafana_performance_migration.sql`.
2. Control projection API — recorded effective history plus pure, read-only future simulation of saved modes, calendar transitions, climate periods/ramps, photoperiod, light targets, and light programs. Status: grounded with a new projection seam required. Evidence: `Infrastructure/automation-service/app/control/climate_resolver.py`, `setpoint_manager.py`, `repositories/setpoints.py`, scheduler modules.
3. uPlot chart system — typed imperative adapter, family scales/axes, legend toggles, drag zoom, overlays, tooltips, resize, streaming append, and accessible table alternative. Status: externally verified. Evidence: uPlot docs/demos/type declarations and npm 1.6.32.
4. Non-chart monitoring panels — Flower averages/front/back tables; Veg current sensor table; both statistics tables. Status: grounded. Evidence: canonical provisioned JSON under `Infrastructure/iskra_stack/dashboards/{flower_sector,veg_sector}`.
5. Product/design integration — Flower and Veg monitoring routes only, current six themes codified into `Infrastructure/frontend/DESIGN.md`, responsive and accessible primitives. Status: decided. Evidence: `Infrastructure/frontend/src/styles/index.css`, `themes.css`, monitoring pages.
6. Safe QA/cutover — TDD throughout, local/sandbox verification only, no production database writes or mutating HTTP, iterative debugging. Status: decided. Evidence: root and service `AGENTS.md` safety rules.

## Scope decisions

- IN: `/flower/monitoring` and `/vegetation/monitoring`; preserve their split dashboard composition and all active Flower/Veg graph and non-graph content.
- OUT: Flower Soil, Laboratory placeholders, Operations dashboard, Grafana alerting, Grafana decommissioning, and all non-monitoring routes.
- Keep Grafana available as parity reference and rollback during iteration.
- Refresh: load range once, append/replace live readings every second only when the range follows now; no full-range requery every second.
- Time selection: arbitrary past-only, crossing-now, or future-only intervals, duration 5 minutes through 7 days.
- Projection: use every saved schedule source, including calendar mode transitions, climate periods/ramps, photoperiod, per-light targets, and light programs; never mutate runtime scheduler/ramp/control state.
- History vs future: database-recorded effective values before now; projected values after now; future shown as explicitly projected while retaining dotted styling.
- Photoperiod history: derive legacy history from recorded per-light SUN/MOON rows; add a dedicated room-level photoperiod history table/logger for exact records going forward.
- Setpoints: heating, cooling, VPD, CO2, and per-light intensity; omit null/VPD-derived humidity. Preserve separate climate and CO2/pressure/device panels.
- Owner authorizes database table/read-model redesign where it materially improves correctness, provided migration is explicit, reversible, sandbox-proven, and never applied to production without later deployment approval.
- No general telemetry/event-log dashboard section in this phase. Keep only the dedicated photoperiod history required for accurate sun/moon overlays.
- Playwright/Chromium already works on this machine. Verification may launch the existing browser, but must not run `playwright install` or download/reinstall Chromium.
- Aggregated sensors: average line plus min/max envelope.
- Bucket policy: raw through 1h, 1-minute through 6h, 5-minute above 6h through 7d; never use hourly within the requested 7-day maximum.
- Statistics: exact min/max/average/stddev via additive statistics CAGGs containing count/sum/sum-of-squares; do not destructively replace existing CAGGs.
- Axes: metric-family scales; temperature family on left, all other families on right; family-colored axes; node variants use related distinguishable styles. Preserve every explicit Grafana soft bound and auto-range families with no configured bound.
- Design: codify and preserve all six existing themes; no isolated Grafana skin.
- Tests: TDD throughout. User requested no final named F-phase orchestration; retain only local, non-mutating verification required to prove each slice and the assembled result.

## Canonical evidence

- Provisioning: `Infrastructure/iskra_stack/provisioning/dashboards/dashboards.yaml`.
- Active Flower contract: `Infrastructure/iskra_stack/dashboards/flower_sector/flower_sector.json` — Averages, Temperature/RH/VPD, Front, Back, CO2/Pressure, Statistics; 1s refresh; Toronto timezone.
- Active Veg contract: `Infrastructure/iskra_stack/dashboards/veg_sector/veg_sector.json` — Sensor Values, Temperature/RH/VPD, Pressure/Devices, Statistics; 1s refresh; Toronto timezone.
- SPA replacement sites: `Infrastructure/frontend/src/pages/FlowerMonitoring.tsx`, `VegetationMonitoring.tsx`, and `components/GrafanaPanel.tsx`.
- Existing backend range contract: `Infrastructure/backend/app/routes/sensors.py`; existing tier implementation: `Infrastructure/backend/app/repositories/sensor_repository.py`.
- Effective history writes every ~5s and Redis immediately: `Infrastructure/automation-service/app/repositories/setpoints.py`.
- Climate projection inputs and ramp behavior: `Infrastructure/automation-service/app/control/climate_resolver.py` and `setpoint_manager.py`.
- Light effective history: `Infrastructure/automation-service/app/control/light_effective_setpoint_logging.py`.
- Existing themes/tokens: `Infrastructure/frontend/src/styles/index.css` and `themes.css`; no `DESIGN.md` exists.
- uPlot primary sources: `https://github.com/leeoniya/uPlot`, `https://github.com/leeoniya/uPlot/blob/master/docs/README.md`, `https://leeoniya.github.io/uPlot/demos/`, `https://github.com/leeoniya/uPlot/blob/master/dist/uPlot.d.ts`.
- Current package: `uplot` 1.6.32, MIT, no runtime dependencies (`https://www.npmjs.com/package/uplot`).
- Production pattern inspiration: SigNoz uPlot builder/read-model work (`https://github.com/SigNoz/signoz/pull/10069`, `https://github.com/SigNoz/signoz/pull/10207`).
- Accessibility warning: Grafana/uPlot canvas lacks an accessible alternative by default (`https://github.com/grafana/grafana/issues/118642`); plan must include semantic chart labelling and a keyboard-accessible table view.

## Three approaches considered

### A. Page-specific client composition

Reuse the current sensor endpoint, add a small projection endpoint, and assemble panel data directly inside the two page components.

- Advantages: shortest initial path; smallest backend diff; easy to throw away.
- Disadvantages: page code owns alignment, bucketing metadata, stats, error semantics, and cross-service races; duplicated Flower/Veg transforms; poor foundation for later rooms.
- Best when: the work is a disposable visual experiment.
- Wrong when: the chosen formula is expected to become the reusable monitoring architecture.

### B. Typed domain read models + reusable direct-uPlot primitives — preferred

Keep ownership explicit: the sensor backend returns typed range/live/stat payloads; automation returns typed recorded/projected control and photoperiod payloads. A small frontend monitoring store aligns the two payloads and drives reusable chart/table primitives through the direct uPlot API.

- Advantages: no control formula duplicated in React; source services retain ownership; responses expose tier/provenance/config revision; easy to test; enough abstraction for Flower/Veg without inventing a dashboard language; direct uPlot lifecycle preserves streaming performance.
- Disadvantages: two coordinated API calls; requires a carefully designed pure projection simulator and timestamp/provenance alignment.
- Preferred because: the user explicitly wants to iterate on two rooms until the formula is right, while retaining a clean path to expand later.
- Could be wrong when: a strict single-transaction snapshot across sensor and control domains is mandatory, or dozens of arbitrary dashboards must be authorable immediately.

### C. Generic Grafana-style dashboard engine

Create a panel-definition schema, one dashboard aggregation API, a query/transform engine, and config-driven chart/stat/table rendering.

- Advantages: closest to a true Grafana replacement; future rooms and Operations can be declarative; central caching and panel composition.
- Disadvantages: largest scope and risk; creates a DSL before the desired formula is known; duplicates much of Grafana; slows iteration; highest testing burden.
- Best when: many operators need to author/reconfigure many dashboard types without code changes.
- Wrong now because: only Flower and Veg are in scope and the purpose of this phase is to discover and refine the right formula.

## Proposed plan approach

Use Approach B. Build read-only, typed sensor and control-series boundaries first under TDD; add additive history/statistics structures without modifying production data during tests; implement a pure projection simulator using the same saved inputs and mathematical rules as control without touching runtime state; then build theme-aware uPlot and non-chart primitives; replace only the two monitoring routes; verify parity, 1-second append performance, 5m–7d range behavior, DST/overnight schedules, accessibility, and failure states locally. Keep the existing Grafana routes/config untouched as rollback/reference.

## Approval gate

Approval authorizes only creation of the comprehensive plan artifact. It does not authorize implementation, deployment, schema application, production HTTP calls, or production data changes.

## Approval receipt

- approved_by_user: true
- approved_scope: Flower and Veg monitoring only
- approved_action: create `.omo/plans/grafana-replacement-veg-flower.md`
- plan_path: `.omo/plans/grafana-replacement-veg-flower.md`
- plan_todos: 30 across 5 dependency waves
- self_review: passed header order, 30/30 references, 30/30 acceptance criteria, 30/30 happy QA, 30/30 failure QA, 30/30 commit boundaries

## High-accuracy review ledger

- Round 1 native Momus background task: `bg_bd755b3b` — rejected; final session id was not retained in the compacted planner context.
- Round 1 independent Oracle background task: `bg_d9628f47` — rejected; final session id was not retained in the compacted planner context.
- Round 1 fixes: corrected backend module/path ownership; moved sensor monitoring routes under the existing Caddy-routable prefix; kept the legacy naïve timestamp route unchanged and added aware UTC monitoring live output; defined half-open range/tier/raw-edge/tail contracts; added additive NUMERIC statistics and control-history CAGGs; required repeatable-read immutable snapshots and separately timed Redis anchors; separated origin/quality/aggregation provenance; specified Flower calendar, Veg current-mode, moon-authority, null-PID, and photoperiod precedence/gap semantics; added sensor/control/stat one-second tails and explicit live/fixed state; pinned compatible dependencies; made all 30 todo QA commands concrete and corrected evidence paths.
- Round 2 native Momus: session `ses_03ce93b8dffeZZ20nVc9ae3hgw`, background task `bg_667f9cd5` — rejected for three dependency-order defects.
- Round 2 independent Oracle: session `ses_03ce93b25ffe72alldw6F12nOE`, background task `bg_b5e698ed` — rejected with four critical and seven major findings.
- Round 2 gate: both must return unconditional `OKAY` before handoff; any finding triggers fixes and a fresh paired round.
- Round 2 Momus fixes: reordered the disposable TimescaleDB harness before SQL and statistics consumers; made the harness own URL export/cleanup for backend integration tests; moved the localhost Playwright config/fixture server/network guard into Todo 3 before page-level browser QA; retained Todo 30 as the final scenario/performance expansion.
- Round 2 Oracle fixes: isolated local browser QA to exact origin `127.0.0.1:4173` with all REST/WS/Grafana overrides and explicit production-port blocks; split DB projection revision from stable Redis anchor fingerprint; specified materialized-only sensor/control CAGG keys, nullable counts, timestamps, watermarks, refresh policy, late arrivals, and Decimal serialization; replaced timestamp-overlap correctness with ingestion-order cursors plus bounded recovery reconciliation; completed backend/automation/frontend QA tooling; moved photoperiod logger lifecycle to `ServiceContainer`; separated process-local runtime snapshot provenance; locked sensor-free climate fallback and current overnight weekday semantics; assigned runtime theme CSS tokens; replaced merge-base with durable implementation `START_SHA`; added backend Ruff and commit-time Ruff gates. User clarified that Playwright Chromium is already installed, so the plan verifies launch and explicitly forbids browser installation/download.
- Round 3 static preflight passed: template header order intact; 30 todos; 30/30 references, acceptance, happy QA, failure QA, and commit boundaries; no stale browser-install, timestamp-overlap, config-revision, event-log, or evidence-path text. Dispatch exactly one fresh Momus and one fresh Oracle against this revision; both must return unconditional `OKAY`.
- Round 3 native Momus: session `ses_03cd771e8ffexiB3Q0y0CiZsPe`, background task `bg_96233314` — in progress.
- Round 3 independent Oracle: session `ses_03cd76ea7ffeOsSSoblW4JeK2g`, background task `bg_c29b47c8` — in progress.
- Owner instruction during Round 3: this is the last review round; after its findings are folded in, hand off to implementation and fix residual issues iteratively rather than initiating another review round.
- Round 3 Momus result: rejected for non-unique effective-history cursors and failure-masking `tee` pipelines. Fixes folded in: future-only immutable `monitoring_ingest_id` on effective history, reuse of existing automation/photoperiod IDs, total-order cursors/deduplication and tied-timestamp tests; mandatory `set -euo pipefail` capture helper for every QA command.
- Round 3 Oracle result: session `ses_03cd76ea7ffeOsSSoblW4JeK2g`, background task `bg_c29b47c8` — rejected with 13 blockers. All cited fixes were folded in without another review round per owner instruction: pending-invalidation raw replacement before CAGG refresh; definitions/backfill/policy-activation separation; snapshot-consistent per-source paged cursors and race tests; nullable future-only effective ingest metadata; anchor quality/deadline expiry; projection-only GET; scheduler-authoritative light anchors and manual contamination test; explicit logger sink/health lifecycle injection; scoped `.gitignore` negations and tracked-test gates; dedicated Todo 11 tooling smoke tests plus exact pins; mandatory pipefail capture helper; Todo/final generated-API and root Ruff gates; CSP/request-log enforcement for visual QA with browser downloads disabled.
- Final static self-review after fixes: template header order intact; 30 todos; 30/30 references, acceptance, happy QA, failure QA, and commit boundaries; no stale browser-install, timestamp-overlap, config-revision, automatic-policy, ignored-test, or evidence-path text.
- Dual-review disclosure: the final reviewer verdicts were not unconditional `OKAY`; the owner explicitly directed that Round 3 be the last review and that remaining issues be fixed iteratively during implementation. Do not describe the review as passed.

## Metis gap-analysis receipt and resolutions

- session: `ses_03d202811ffecGBQ5Kg8Om0rnj`
- result: gaps found and folded into the plan
- Bucket mismatch: retain the user-approved dedicated monitoring policy (raw <=1h, 1min <=6h, 5min <=7d) in a new endpoint; do not change the legacy sensor endpoint or Grafana SQL. Canonical repo JSON is the parity baseline; the deployed file is stale and is not queried during QA.
- Live append: it is new frontend behavior over the existing per-zone `/live` endpoints, not a server append endpoint. Enforce one in-flight poll and no steady-state historical refetch.
- Device/PID history: assign `automation_state` reads to the new automation monitoring-history repository and API payload.
- Projection state: load an immutable read snapshot from DB/Redis sources; anchor active ramps from persisted ramp state and latest effective rows; never bind or mutate the runtime `Scheduler` or `RampManager`. Missing anchors produce typed estimated/unavailable provenance, never fabricated precision.
- Historical photoperiod: per-light SUN/MOON rows must agree within a bucket to derive room state; conflicts/missing evidence produce `unknown`. Add a room-level append-only history structure/logger for future exact records, but never apply it to production without a later owner-approved deploy.
- Statistics schema: implementation creates additive SQL plus a local TimescaleDB 2.23.1-pg15 harness and backfill procedure; it does not apply DDL to production. Exact `STDDEV_SAMP` is reconstructed from count/sum/sum-squares plus raw partial buckets.
- Shared iframe: leave `GrafanaPanel.tsx` unchanged; new native components are imported only by Flower/Veg monitoring pages.
- Tables: use semantic HTML tables and existing Tailwind/theme tokens; no table-library dependency.
- Parity: compare against canonical dashboard JSON and deterministic local fixtures only; no production Grafana/API request is an acceptance criterion.
- Performance: 1Hz visible updates, one poll in flight, zero historical refetches during steady live mode, p95 uPlot update <=16ms and local fixture timestamp-to-paint <=1s.
- DST: test America/Toronto spring-forward 2026-03-08, fall-back 2026-11-01, and 22:00-06:00 overnight windows with wall-clock semantics.
- Failures: preserve last-good data with stale/error provenance for partial API failure; never clear healthy sibling series; missing projection inputs produce explicit unavailable/estimated state.
- Accessibility: WCAG 2.2 AA target; semantic title/description, keyboard-operable controls/legend, pause-live control, accessible table alternative, and local axe/Playwright checks.
- Sandbox: pinned `timescale/timescaledb:2.23.1-pg15`, synthetic fixtures only, no production credentials/network.
- DESIGN.md: create before any monitoring primitive and codify all six existing themes, chart families, surfaces, states, motion, accessibility, and accepted debt.
- Panel split: retain the two existing chart regions; the second remains CO2/pressure/device/PID/light-intensity rather than moving lights elsewhere.

## Plan #2 approval gate — grafana-replacement-veg-flower-2

- intent: clear
- review_required: false
- size: architecture
- status: awaiting-approval
- pending_action: write `.omo/plans/grafana-replacement-veg-flower-2.md`
- implementation_authorized: false

### Components (topology ledger)

1. Fresh reproducible baseline — isolate current failures from stale or conflicting evidence. Status: active. Evidence: `.omo/evidence/grafana-replacement-veg-flower/final-frontend.txt`, `final-playwright.json`, `29-visual-a11y.json`, `30-performance-parity.json`, `.omo/debug/.debug-journal.md`.
2. Monitoring database contract — canonicalize `monitoring_room_photoperiod` across SQL, logger, history/snapshot readers, cursors, fakes, and sandbox integration. Status: active. Evidence: `Infrastructure/database/monitoring_read_models.sql:316-365`, `Infrastructure/automation-service/app/services/photoperiod_history_logger.py:70-81`, `app/repositories/monitoring_history_sql.py:40-44`, `app/repositories/monitoring_snapshot.py:238-240`.
3. Frontend correctness — preserve the committed uPlot draw lifecycle while fixing reproducible unit, page, legend, title, and recovery failures. Status: active. Evidence: `Infrastructure/frontend/src/features/monitoring/charts/UPlotChart.tsx:145-241`, `vitest.config.ts:12-18`, `index.html:10`, `tests/monitoring/monitoring.spec.ts:11-14`.
4. Parallel browser isolation and accessibility — keep Playwright fully parallel, replace process-global scenario state with test/run-scoped state, and fix monitoring-local WCAG contrast across all six themes. Status: active. Evidence: `Infrastructure/frontend/playwright.monitoring.config.ts:21-47`, `vite.monitoring.config.ts:61-63,137-249`, `tests/monitoring/visual-a11y.spec.ts`, `src/features/monitoring/styles/monitoring.css`, `src/styles/themes.css`.
5. Modern Python quality gate — pin Ruff 0.16.1 everywhere, make root `pyproject.toml` the single Ruff policy, update pre-commit/tooling declarations, and absorb formatter/rule migration under regression-first TDD. Status: active. Evidence: `pyproject.toml:7-46`, `Infrastructure/pyproject.toml:1-52`, `Infrastructure/.pre-commit-config.yaml:14-20`, backend/automation `requirements-dev.txt:5`, automation `requirements.txt:15-21`.
6. Safe final verification and handoff — run local-only backend, automation, PG15/TimescaleDB 2.28.3 sandbox, frontend, browser, visual, topology, scope, and dirty-worktree gates with fresh #2 evidence. Status: active. Evidence: original plan `Final verification wave`, `AGENTS.md`, `Infrastructure/database/tests/test-monitoring-read-models.sh:21-278`, `.omo/evidence/grafana-replacement-veg-flower/initial-worktree.txt`.

### Decisions

- Standalone concise remediation plan named `grafana-replacement-veg-flower-2`; the original plan remains implementation history and reference, not an execution dependency.
- Regression-first TDD; establish a clean fresh baseline before changing code and fix only reproducible defects or proven suite-order/state leaks.
- Keep the product title `Siberian Jungle`; repair the stale smoke assertion rather than rebrand.
- Preserve `fullyParallel`; isolate mutable fixture/scenario state rather than serializing tests.
- Accessibility fixes are monitoring-local; preserve global theme palettes while meeting serious/critical axe and WCAG 2.2 AA contrast requirements in all six themes.
- Canonical photoperiod relation is `monitoring_room_photoperiod`; update readers/tests and prove the real relation in the sandbox.
- Ruff target is latest stable 0.16.1; root `pyproject.toml` becomes the sole policy; dependency, pre-commit, local, and deploy-path tooling must report the same version.
- PostgreSQL 15 remains unchanged; update TimescaleDB test/reference compatibility to 2.28.3, the latest PG15-compatible release. PG16/TimescaleDB 2.29 is a separate future plan.
- Preserve and quarantine the dirty worktree. Never reset, clean, delete, or claim ownership of pre-existing `.omo`, screenshots, `.opencode`, or tracked `.venv` changes.
- Stop at deploy-ready handoff. No production HTTP, DB, Redis, hardware, schema application, backfill, policy activation, or deployment.

### Scope IN

- Reproducible baseline and evidence provenance.
- Photoperiod relation contract repair and sandbox integration coverage.
- Current Flower/Veg monitoring unit/E2E/performance/recovery/accessibility failures.
- Deterministic parallel fixture state and exact-origin network guard.
- Ruff 0.16.1 project-wide tooling/config migration.
- PG15/TimescaleDB 2.28.3 local sandbox verification.
- Fresh final evidence and deploy-ready owner handoff.

### Scope OUT (Must NOT have)

- PostgreSQL 16 or TimescaleDB 2.29 production migration.
- Unrelated dependency upgrades beyond Ruff and TimescaleDB test/reference pins.
- Changes to Flower Soil, Lab, Operations, Grafana dashboards/provisioning, `GrafanaPanel.tsx`, device control, hardware control, or production data.
- Destructive worktree cleanup or automated deployment.

### Open questions

- None. All owner decisions are resolved.

### Approval gate

- status: awaiting-approval
- pending_action: scaffold and write `.omo/plans/grafana-replacement-veg-flower-2.md`, then run mandatory Metis gap analysis and self-review.
- approval_scope: the six components and decisions above.
- approval_does_not_authorize: implementation, production access, schema application, deployment, or destructive cleanup.
