---
slug: grafana-replacement-veg-flower-2
status: approved
intent: clear
review_required: false
pending-action: write .omo/plans/grafana-replacement-veg-flower-3.md
approach: Supersede patch-by-patch finalization with a dedicated, typed, replica-ready monitoring read service and a Grafana-contract-driven uPlot rebuild for Flower and Vegetation.
---

# Draft: grafana-replacement-veg-flower-2

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Fresh, versioned baseline separates current defects from stale evidence | active | `.omo/evidence/grafana-replacement-veg-flower/final-*.{txt,json}`, `.omo/debug/.debug-journal.md` |
| C2 | Ruff 0.16.1 is pinned everywhere and root `pyproject.toml` is the sole policy | active | `pyproject.toml`, `Infrastructure/pyproject.toml`, service requirements, `Infrastructure/.pre-commit-config.yaml` |
| C3 | PG15/TimescaleDB 2.28.3 test/reference pins and catalog adapter are verified | active | `Infrastructure/database/monitoring_read_models.sql`, database harness, Timescale image references |
| C4 | Photoperiod logger-to-reader round trip uses the real table and column | active | `monitoring_room_photoperiod`, logger, history/snapshot repositories, integration test |
| C5 | Frontend unit/chart/title behavior is regression-locked against current source | active | UPlot adapter/tests, toolbar/page tests, `index.html`, smoke spec |
| C6 | Fully parallel browser scenarios are isolated and one preview lifecycle owns port 4173 | active | Playwright config/specs, monitoring client context, Vite fixture middleware |
| C7 | Monitoring-local error/focus/status colors pass WCAG 2.2 AA across six themes | active | monitoring CSS, theme tokens, axe and visual QA |
| C8 | All local-only gates pass with fresh #2 evidence and a deploy-ready handoff | active | original final wave, project safety docs, new evidence directory |

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->

- None. All material forks were asked and resolved by the user.

## Findings (cited - path:lines)

- The original checklist leaves Todo 30 unchecked while its detailed change line is checked; focused and final evidence disagree (`.omo/plans/grafana-replacement-veg-flower.md:149-180,455-462`).
- Current source contains the later uPlot committed-draw and 30-second Vitest fixes, so `final-frontend.txt` is stale and must not drive edits without reproduction (`UPlotChart.tsx:220-231`, `UPlotChart.test.tsx:122-130`, `vitest.config.ts:12-18`).
- The product title is `Siberian Jungle`, while the placeholder smoke test expects another brand (`Infrastructure/frontend/index.html:10`, `tests/monitoring/monitoring.spec.ts:11-14`).
- Playwright is fully parallel and every invocation owns a strict, non-reused preview on port 4173; separate simultaneous commands can race before any test runs (`playwright.monitoring.config.ts:21-47`, old `30-performance-parity.json`).
- Fixture scenario state is mutable at both the browser module and preview-server levels; isolation must be proved and made explicit rather than inferred from worker count (`api/client.ts:27-33,60-69`, `vite.monitoring.config.ts:61,152-229`).
- The database creates `monitoring_room_photoperiod(observed_at, ...)`, and the logger writes it, but history/snapshot readers query nonexistent `monitoring_photoperiod_history.timestamp`; fakes mirror the wrong contract (`monitoring_read_models.sql:316-330`, `photoperiod_history_logger.py:70-81`, `monitoring_history_sql.py:40-44`, `monitoring_snapshot.py:238-240`, `monitoring_history_types.py:195-205`).
- Ruff policy/version state is fragmented across two configs, two 0.9.10 dev pins, one loose runtime pin, and pre-commit v0.1.6; official latest stable is 0.16.1 (`pyproject.toml`, `Infrastructure/pyproject.toml`, service requirements, `Infrastructure/.pre-commit-config.yaml:14-20`).
- PostgreSQL stays at 15; TimescaleDB 2.28.3 is the newest compatible release. PG16/TimescaleDB 2.29 is explicitly deferred to another plan.
- The native disposable PostgreSQL/TimescaleDB harness is the mandatory database gate. Docker is optional reference tooling only and its absence cannot block completion; no container is required on the Raspberry Pi.
- The database harness rejects inherited DB variables, non-loopback/production-looking targets, and non-`monitoring_test_` names, then drops its disposable DB/role on every exit (`Infrastructure/database/tests/test-monitoring-read-models.sh:21-84,161-278`).

## Decisions (with rationale)

- Standalone remediation plan; the original plan is reference/history only.
- Regression-first TDD and a fresh baseline before any fix.
- Keep the `Siberian Jungle` title and update the stale test.
- Canonical photoperiod table/column is `monitoring_room_photoperiod.observed_at`; repository SQL aliases `observed_at AS timestamp` at the row-model boundary so public timeline contracts remain unchanged.
- Keep Playwright fully parallel; replace global scenario selection with immutable per-page request context and key transient fixture counters by an explicit per-test fixture session.
- Run the complete Playwright monitoring suite in one invocation so exactly one preview owns port 4173.
- Keep functional browser coverage parallel, but run the timing-sensitive performance project with one worker so concurrent rendering cannot distort p95 measurements.
- Add monitoring-local semantic error tokens for all six themes; do not change global theme palettes.
- Upgrade only Ruff and TimescaleDB references: Ruff 0.16.1 everywhere, one root Ruff policy, PG15-compatible TimescaleDB 2.28.3. No unrelated dependency upgrades.
- Preserve/quarantine all pre-existing worktree dirt. No task may reset, clean, delete, or stage unrelated files.
- No commits during this remediation unless separately requested; produce a precise staging/handoff manifest instead.
- Stop at local deploy readiness; no production access or deployment.

## Scope IN

- Fresh v2 start SHA, worktree inventory, tool-version inventory, and baseline classification.
- Ruff 0.16.1 dependency/pre-commit/config migration and resulting source formatting/lint repair.
- Exact PG15 TimescaleDB 2.28.3 references and catalog/harness compatibility.
- Photoperiod relation + observed-at contract repair, fake alignment, and real sandbox round-trip.
- Reproduced frontend unit/chart/page/title failures only.
- Test-scoped browser fixture sessions, scenario propagation, preview lifecycle, network guard, parity, recovery, and performance.
- Monitoring-local six-theme accessibility and visual QA.
- Full local backend/automation/database/frontend/scope verification and deploy-ready handoff.

## Scope OUT (Must NOT have)

- Production database/API/Redis/hardware access, DDL application, backfill, policy activation, deploy, or reset.
- PostgreSQL 16 or TimescaleDB 2.29 migration.
- React/Vite/Vitest/Playwright/uPlot/Zod or unrelated Python dependency upgrades.
- Grafana dashboard/provisioning changes, `GrafanaPanel.tsx`, Flower Soil, Lab, Operations, device control, or hardware control.
- Destructive worktree cleanup, staging, commits, or attribution of existing files to plan #2.
- Architecture-document deployed timestamps; no deployment occurs in this plan.

## Open questions

- None.

## Approval gate
status: approved
approved-by-user: true
pending-action: write .omo/plans/grafana-replacement-veg-flower-3.md
implementation-authorized: false
approval-received-after-brief: true
metis-session: ses_03313d32fffeH7aqC3LT2LgxAf
metis-result: gaps found and folded into the plan
self-review-result: passed; TLDR filled last, template order preserved, eight todos include references, agent-executable acceptance criteria, happy/failure QA evidence, dependencies, and commit disposition
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->

## Superseding architecture brief (2026-08-12)

This section supersedes the earlier remediation-only direction above. The existing `grafana-replacement-veg-flower-2` plan remains historical evidence; it is not the execution plan for the new architecture.

### Components (topology lock)

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Provisioned Flower/Veg Grafana JSON becomes a versioned parity contract for panel grids, tables, series, axes, units, defaults, and overlays | active | `Infrastructure/iskra_stack/dashboards/{flower_sector/flower_sector,veg_sector/veg_sector}.json` |
| C2 | One dedicated typed monitoring read service owns sensor/control history, projections, live values, query budgets, and a replica-ready read-only pool | active | `Infrastructure/services.yaml`; current split repositories under `Infrastructure/backend/app/repositories/` and `Infrastructure/automation-service/app/repositories/` |
| C3 | uPlot reproduces the canonical chart behavior inside the ProjectCEA shell, including all target families and recorded/projected provenance | active | `Infrastructure/frontend/src/features/monitoring/`; `Infrastructure/frontend/DESIGN.md` |
| C4 | Grafana-equivalent tables, time controls, loading/empty/error states, responsive layouts, keyboard access, and combined legend toggle/isolate behavior are complete | active | canonical dashboard JSON; monitoring frontend tests and Playwright fixture |
| C5 | Production-schema/cardinality database tests, latency/plan budgets, same-origin Tailscale access, headed visual diffs, and guarded rollout prevent false-green release | active | `Infrastructure/database/tests/`; `Infrastructure/caddy/Caddyfile`; `Infrastructure/frontend/playwright.monitoring.config.ts` |

### Decisions

- Test strategy: hybrid TDD plus visual contract. Query/schema/API/store defects get red tests first; UI receives canonical reference screenshots and interaction contracts before implementation, followed by headed visual diffs.
- Legend: swatch toggles one series; label isolates it; Ctrl/Cmd-click adds/removes from isolation; Reset restores all; every action has keyboard and `aria-pressed` equivalents.
- Targets: design the wire and rendering contracts for every currently available climate and light target rather than hard-coding the canonical heating/cooling/VPD trio. Target kinds stay typed and additive; light targets retain device identity.
- Canonical authority: use only provisioned JSON under `Infrastructure/iskra_stack/dashboards/`; divergent frontend Grafana copies are not authority.
- Authentication: browser uses same-origin Caddy routes and no durable build-time API key. The owner-visible surface is Tailscale-only; internal services stay loopback-only and the monitoring database role is read-only.
- Database: primary is the initial read target; configuration supports a healthy read replica later. Fail over only through explicit health/lag policy—never silently serve stale replica data.
- Rollout: preserve existing Grafana until native parity, realistic performance, headed visual QA, and rollback gates pass. No destructive production SQL or replica recovery belongs in implementation.

### Architecture choice

- Preferred: dedicated monitoring read service. It removes expensive history/projection work from the hardware-facing automation loop, creates one typed frontend contract, gives monitoring an independently bounded pool and query budget, and supports primary-now/replica-later deployment.
- Rejected default: optimize the existing backend + automation split in place. It minimizes service churn but retains cross-service joins/fan-out, control-loop blast radius, duplicated monitoring boundaries, and fragmented observability.
- Rejected default: query through Grafana/Iskra or expose datasource access to the SPA. It reuses dashboard infrastructure but couples the replacement to Grafana/replica availability, weakens typed authorization boundaries, and cannot be the durable Grafana replacement.

### Scope IN

- New monitoring service inventory/unit/health/routing, typed contracts, separate read-only pool, statement/row/time limits, observability, primary/replica configuration, and safe fallback policy.
- Real-schema and production-cardinality fixtures; EXPLAIN-plan and latency assertions for predecessor, range, tail, statistics, tables, and projections.
- One frontend monitoring API boundary and store; full Flower/Veg canonical tables/panels; all currently available target families; photoperiod; time and zoom behavior; loading/partial/empty/error/stale states.
- Reference extraction from canonical Grafana JSON into reviewed fixtures/manifests; uPlot series/axis/legend/tooltip/selection behavior; 375/768/1280 headed visual and accessibility evidence.
- Same-origin Tailscale access without `VITE_CEA_API_KEY`; guarded candidate rollout with existing Grafana retained as rollback/reference until acceptance.

### Scope OUT / Must NOT have

- No Laboratory, Flower Soil, Operations, device-control, automation algorithm, hardware, Grafana dashboard redesign, or unrelated frontend work.
- No direct browser-to-database/Grafana datasource access, static bundled secret, broad public listener, silent stale-replica failover, unbounded query, or database work in the control loop.
- No tiny handwritten fixture as the sole database proof, screenshot-only acceptance, headless-only browser release gate, or test that can pass with zero cases/data.
- No `TRUNCATE`, `DELETE`, `DROP`, replica re-base, production data mutation, or production service action without a separately explicit owner-authorized execution step.

### Open questions

- None. The next decision is approval of this architecture brief; approval authorizes writing one decision-complete plan only, not implementation.
