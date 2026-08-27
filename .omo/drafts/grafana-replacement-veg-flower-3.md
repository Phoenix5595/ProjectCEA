---
slug: grafana-replacement-veg-flower-3
status: final-review-closed-no-further-rounds
intent: clear
review_required: true
pending-action: no further review is authorized; report final receipts and corrected Oracle blockers to owner
approach: Unified monitoring domain inside the existing backend plus a canonical-Grafana-contract-driven uPlot rebuild; Caddy remains a minimal router only.
---

# Draft: grafana-replacement-veg-flower-3

## Components (topology ledger)

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Versioned canonical Grafana parity contract | active | `Infrastructure/iskra_stack/dashboards/{flower_sector/flower_sector,veg_sector/veg_sector}.json` |
| C2 | Unified bounded monitoring API inside existing backend | pending | `Infrastructure/backend`; existing backend/automation monitoring repositories to consolidate |
| C3 | Existing native uPlot dashboards are reworked to full canonical fidelity and all typed target families | active | `Infrastructure/frontend/src/features/monitoring/` |
| C4 | Tables, time controls, states, responsive behavior, and accessibility | active | canonical dashboard JSON; monitoring browser/unit tests |
| C5 | Real-cardinality performance, secure same-origin access, headed QA, and guarded rollout | active | database harness; Caddy; Playwright; deploy scripts |

## Open assumptions (announced defaults)

- Initial database target is the primary; the same service configuration supports a healthy, lag-bounded replica later.
- Existing Grafana remains available as the parity reference and rollback route until native acceptance; route promotion is guarded and reversible.
- The unified monitoring API uses existing backend port **8000**, a separate monitoring pool/semaphore, and one minimal Caddy route.

## Findings (cited - path:lines)

- Provisioned Flower has six panels and Veg four panels, both refresh every second (`Infrastructure/iskra_stack/dashboards/flower_sector/flower_sector.json`; `veg_sector/veg_sector.json`).
- Caddy currently routes sensor monitoring to backend 8000 and all unmatched automation/SPA traffic to 8001; comments explicitly describe the static bundle sending `X-API-Key` (`Infrastructure/caddy/Caddyfile:1-7,52-64,93-106`).
- Static frontend auth is built from `VITE_CEA_API_KEY` and monitoring attaches it as `X-API-Key` (`Infrastructure/frontend/src/config/env.ts:18-20,74`; `src/features/monitoring/api/client.ts:136`).
- Current monitoring is split between backend sensor routes/repositories and automation control history/projection routes; automation snapshot performs expensive predecessor reads inside one repeatable-read snapshot (`Infrastructure/automation-service/app/repositories/monitoring_snapshot.py:69-163`).
- Native uPlot, manifests, tables, toolbar, store, tooltip, overlays, and toggle/reset legend already exist; this is a fidelity rework and data-boundary consolidation, not a greenfield chart build (`Infrastructure/frontend/src/features/monitoring/AGENTS.md:3-22`).
- Existing monitoring read models already provide six materialized-only continuous aggregates, a watermark view, and photoperiod history; they are canonical reuse targets, not parallel infrastructure (`Infrastructure/database/monitoring_read_models.sql:136-365`).
- The candidate worktree preserves Grafana on `/flower/monitoring` and `/vegetation/monitoring` while native pages live at the beta routes (`/tmp/projectcea-monitoring-beta-production/Infrastructure/frontend/src/App.tsx:50-60`).
- Current production-scale predecessor queries exceeded acceptable interactive latency; the existing tiny fixtures did not detect the plan/cardinality failure (prior session production evidence and `Infrastructure/database/tests/fixtures/monitoring_test_fixture.sql`).
- Database requirements already define aggregate ladders, time filters, setpoint semantics, photoperiod overlays, and per-device light intensity (`Infrastructure/database/REQUIREMENTS.md:27-119`).

## Decisions (with rationale)

- Owner chose the existing backend over a dedicated microservice: for one Tailscale user, SQL/query design dominates speed; a separate bounded backend pool/semaphore gives sufficient isolation without another process, port, unit, credential set, or version/config drift risk.
- The backend monitoring domain absorbs current sensor reads plus automation monitoring history/snapshot reads. The automation photoperiod writer remains in automation; no write path moves. Historical views use recorded rows as action truth, while pure future projection functions are shared to prevent divergence.
- Reuse the existing native monitoring ladder and read models exactly: raw for 5m through 1h inclusive, 1-minute above 1h through 6h inclusive, and 5-minute above 6h through 7d inclusive; reject ranges outside 5m-7d. Do not create a fourth ladder or new CAGGs unless benchmark evidence proves an existing model insufficient.
- Local primary only. Replica support is removed from this plan and may be planned separately if ever needed.
- Hybrid TDD and visual contracts: red tests for data/query/API/store defects; reviewed canonical fixtures/screens before UI changes; headed diffs after implementation.
- All target families are typed and additive, including per-device light targets.
- Legend supports swatch toggle, label isolate, Ctrl/Cmd multi-isolate, keyboard equivalents, and Reset.
- Tailscale remains the access-control boundary. Caddy remains only a path router and receives exactly one new `/api/monitoring/v1/* → backend:8000` route.
- Existing API-key, Host/source, WebSocket, Caddy systemd, and authentication behavior remain unchanged; security hardening is explicitly outside this dashboard project.
- Existing Grafana remains untouched and available until native parity and release gates pass.
- Owner approved an additive nullable sequence-backed `measurement.monitoring_ingest_id` plus partial index. Existing historical rows stay NULL/range-only; new rows receive IDs, enabling lossless late-arrival sensor tails without rewriting history.
- Owner explicitly forbids Docker/Podman on the Pi. The earlier CI answer was a mistype and is void. Database QA must use already-installed native PostgreSQL/TimescaleDB binaries to create a separate temporary cluster/data directory/socket/random loopback port, never the running production cluster; stop and delete it after evidence export.
- Verified without a database connection: PostgreSQL 15.18 binaries are under `/usr/lib/postgresql/15/bin`; TimescaleDB control/module files provide 2.28.3 under `/usr/share/postgresql/15/extension` and `/usr/lib/postgresql/15/lib`. Local behavioral QA is therefore exactly 2.28.3; compatibility with 2.23–2.28 remains catalog/schema-guard coverage, not a false multi-version runtime claim.
- The rewritten API contract is inline and exact: response arrays/fields/enums, target capability reasons, cursor source mapping, HMAC wire format, readiness shape, and range/tail semantics.
- The new service dependency decision is exact direct pins plus a generated transitive hash lock; QA invokes only the recorded venv executables.
- Owner rejected the custom compliance/testing framework as unnecessary. The plan now uses only direct product tests, one safety-critical native DB harness, existing test runners/reports, headed visual QA, one performance/soak run, and the deploy sandbox.
- Owner explicitly requested root `AGENTS.md` guidance prohibiting operation ledgers, evidence sealers, reviewer scripts, evidence validators, generic failure-fixture frameworks, and tests-of-test-frameworks unless compliance/audit infrastructure is explicitly requested.
- Owner permits exactly one final dual plan review after simplification; no further review rounds may be started.
- Final Momus returned OKAY. Final Oracle found two concrete contract gaps: raw control/photoperiod points lacked physical row identity, and separate monitoring-service could not truthfully access automation’s process-local photoperiod flush health. Both are corrected in the plan without another review, per owner instruction.
- After review closure, owner explicitly selected **Existing backend** for the API home. This supersedes all dedicated-service/port-8005/systemd/credential/replica decisions. No new review is permitted or required by the owner.
- Capacity is explicitly capped at three concurrent browser clients, all owned by the same person on the same Tailnet. One-second HTTP tail polling is approved; public/multi-tenant scale, WebSocket redesign, and larger synthetic load are out of scope.

## Scope IN

- New backend monitoring domain, separately bounded read pool/semaphore, unified contracts, and one minimal Caddy route.
- Production-schema/cardinality fixtures, query rewrite/read models/indexes as proven necessary, EXPLAIN and latency gates.
- Canonical Flower/Veg tables, charts, series, axes, tooltips, legends, targets, overlays, time controls, and states.
- Frontend API/store consolidation, responsive/accessibility QA, and guarded candidate rollout/rollback.

## Scope OUT (Must NOT have)

- No Laboratory, Flower Soil, Operations, device control, automation/control logic, hardware behavior, or Grafana redesign.
- No direct SPA-to-DB/Grafana datasource access, bundled API key, public service listener, unbounded query, or silent stale replica.
- No destructive production SQL, replica re-base, production data mutation, or hardware/service operation without separate authorization.
- No custom QA compliance framework, operation ledger, evidence sealer, reviewer/validator scripts, or meta-tests.
- No monitoring microservice, port 8005, new service unit/credentials, replica support, or Caddy/auth/WebSocket hardening.

## Open questions

- None.

## Approval gate
status: approved
approved-by-user: true
approved-action: write `.omo/plans/grafana-replacement-veg-flower-3.md`
implementation-authorized: false
approval-received-after-brief: true
metis-session: ses_0024ed47affeqinPeRhGMM2KTW
metis-result: earlier native rewrite blockers were used only for source/semantic corrections; all custom compliance machinery introduced by that iteration has now been removed.
self-review-result: active plan is reduced from 17 machinery-heavy todos to 12 product-focused todos. It retains direct database isolation, SQL/API/frontend/browser/performance/security/deploy tests and removes operation/path/evidence/reviewer frameworks. Root AGENTS testing guidance is explicit.
momus-session: ses_00223abd2ffeBrK4iWUxEQVl8L
momus-result: final simplified review OKAY
oracle-session: ses_00223a8fdffelpqNSoz7dekX6g
oracle-result: final simplified review REJECT with two quick blockers; no further review is authorized
review-fix-summary: retained valid SQL/CAGG/tail/cardinality/commit-order fixes and physical `record_id`/Redis flush-health corrections. Owner’s post-review architecture decision removes dedicated-service, port-8005, replica, service-credential/unit, and Caddy-hardening work; active plan now uses existing backend port 8000, a separate bounded read pool, recorded DB rows as action truth, shared pure projections, and exactly one Caddy route. Custom QA compliance machinery remains removed.
