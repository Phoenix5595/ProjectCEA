# Botanical Color Spectrum Monitoring Recovery — Draft

## State

- intent: clear
- classification: architecture
- review_required: false
- status: interviewing
- plan_path: `.omo/plans/botanical-color-spectrum-monitoring-recovery.md`
- pending-action: resolve owner decisions, present three approaches and approval brief, then write plan after explicit approval
- implementation_authorized: false

## Request constraints

- Plan only; never implement from this session.
- Delegate repository exploration to `explore` and external research to `librarian`; do not substitute planner exploration.
- Do not trust former plans without current evidence.
- Work from `/home/antoine/ProjectCEA`, not a disposable `/tmp` checkout.
- Preserve a visible one-second monitoring refresh cadence.
- Restore working uPlot lines and the requested Grafana-replacement behavior.
- Keep verification minimal and useful; avoid excessive tests.
- Do not request or run plan review.
- Do not contact or mutate production endpoints, databases, Redis, services, or hardware during planning.
- Refactor-first scope: remove accidental/unrequested monitoring complexity rather than layering patches over it.
- Owner accepts a broader technically sound monitoring refactor when it materially simplifies monitoring, analogous to the relay snapshot design that centralized authoritative state and inspection.
- Add concrete pre-steps for a future clean monitoring/automation split, but do not create a new monitoring service merely for architectural neatness.

## Components ledger

| ID | Component | One-line outcome | Status | Evidence |
|---|---|---|---|---|
| C1 | Monitoring data path | Real sensor/control responses become correctly aligned visible uPlot series | grounded; owner runtime details pending | delegated `bg_05e87fec`; frontend monitoring and backend/automation paths cited there |
| C2 | Historical/live backend | Existing backend and automation APIs serve 5m–7d sensor history, setpoint history/projections, and photoperiod | grounded; semantics pending | delegated `bg_c7463522` |
| C3 | Monitoring UX | Restore required charts, legends, axes, range/zoom, overlays, tables, and Botanical styling | grounded; source-of-truth pending | delegated `bg_81d63d13`; `bg_2d1d37ae` |
| C4 | Refresh/performance | One-second visible updates without refetching or rendering an excessive long-range raw dataset | decision pending | delegated `bg_2d1d37ae`; existing aggregate ladder from `bg_c7463522` |
| C5 | Workspace/deployment | Canonical changes and verification run from `/home/antoine/ProjectCEA`; deploy-source behavior is explicit | grounded | delegated `bg_dd196ff7` |
| C6 | Verification/data safety | Minimal tests exercise real API/database formats with a deliberate read-only safety boundary | owner decision pending | delegated `bg_dd196ff7`; `bg_6d3b031c` |

## Evidence ledger

- `bg_05e87fec`: `alignSeries.ts` consumes climate/lights but drops control `devices` and `pid`; manifest matching then makes state/PID series impossible to render. Existing tests do not cover that production-shaped path.
- `bg_05e87fec`: a light device's single intensity timeline can match both Duty Cycle and Intensity manifest labels, producing duplicated/mislabeled series.
- `bg_81d63d13`: active `Infrastructure/iskra_stack/dashboards/` and legacy `Infrastructure/frontend/grafana/dashboards/` disagree (6/4 panels versus 7/5); the legacy copy alone includes light-setpoint freshness and cluster selection.
- `bg_c7463522`: existing backend and automation-service already own sensor aggregates, effective-setpoint history/projection, and scheduled photoperiod history/projection; a new service is not currently justified.
- `bg_c7463522`: “SUN/MOON” currently means configured room photoperiod, not astronomical sunrise/moonrise; `monitoring_room_photoperiod` is reported as an unbounded plain table without retention.
- `bg_dd196ff7`: deploy scripts reportedly hardcode `/home/antoine/ProjectCEA`; historical `/tmp` candidate worktrees are prunable and a documented `SOURCE=/tmp/...` override was ineffective.
- `bg_2d1d37ae`: uPlot requires unique ascending shared x values (seconds by default), aligned equal-length y arrays/null gaps, a stable instance updated by `setData`, `setSize` for resize, `setSeries` for legend toggles, and draw hooks for photoperiod overlays.
- `bg_6d3b031c`: production read-only testing still creates load/lock risks; best practice is disposable real TimescaleDB for reproducibility plus a tightly bounded read-only smoke layer through the API, not direct browser-to-DB access.
- `bg_f9fcb35b`: automation control and control-monitoring HTTP currently share one process/event loop, asyncpg pool, and synchronous Redis client. `MonitoringSnapshotRepository` is the existing extraction seam, but `SnapshotRedis.read_light_effective_metadata` has no concrete implementation and production projection can raise `AttributeError`; runtime snapshot-version wiring also defaults to a non-authoritative value in current route construction.
- `bg_f9fcb35b`: monitoring contracts, projection functions, snapshot types, and history row conversion are pure/extractable; control engine, scheduler, device processing, relay snapshots, hardware, and write paths must remain in automation.
- `bg_b8b975e5`: accidental complexity includes dead `seekTo`, unused monitoring errors/branded types, over-exposed projection internals, display-string semantics, production-wired test instrumentation, debug/placeholder specs, and verified legacy dashboard copies.
- `bg_73d357f9`: only `Infrastructure/iskra_stack/dashboards/` and its provisioning are runtime-mounted. `Infrastructure/grafana/dashboards/` is orphaned; `Infrastructure/frontend/grafana/` dashboards/provisioning/scripts are stale/divergent and not mounted.
- `bg_e79aad8d`: the proportional architecture for one site is mild CQRS with shared committed data, bounded read workload, per-role/session timeouts, and a read replica only after measured need. Outbox/broker/projector infrastructure would solve no current dual-write problem.
- `bg_38a79a6e`: the existing API client is already the migration seam; do not add speculative adapter interfaces. Use typed semantic series fields, contract parity, branch-by-abstraction only when a second provider exists, and one canonical configuration source.

## Decisions

- Adopted: use a thin custom uPlot lifecycle (`setData`, `setSize`, `setSeries`) rather than recreate the chart every second or rely on deep prop diffing.
- Adopted: use existing backend and automation-service ownership unless verification uncovers a missing API contract that cannot fit either service.
- Adopted: use aggregate tiers for longer windows rather than plotting seven days of raw one-second samples.
- Adopted: frontend/browser verification must use the application API; it must not connect directly to PostgreSQL.
- Adopted: changes are made in `/home/antoine/ProjectCEA`; no `/tmp` source worktree is part of this plan.
- Owner decision (corrected): reproduce the active Iskra 6-Flower/4-Veg panel set. Do not add the legacy-only light-effective-setpoint freshness panels or cluster selector, and do not inherit unrelated additions from former plans merely because they exist in current code.
- Owner decision: SUN/MOON means the configured grow-room photoperiod already owned by automation-service, not astronomical periods.
- Owner decision: verification uses the deployed read-only GET APIs backed by the real Flower database. Browser/client verification goes through those APIs; direct PostgreSQL access is only a bounded diagnostic fallback, not the default test path.
- Owner decision: while zoomed into non-live history, the visible chart stays stable; background live ingestion/tailing continues so returning to live immediately shows accumulated current data.
- Owner observation: all charts are empty; this is unexpected only for Flower because Flower is currently the only room with real data.
- Owner decision: use minimal tests-after — one real-DB/API contract smoke path, focused alignment regressions, one browser smoke at one-second refresh, plus agent-executed QA; no broad test expansion.
- Pending owner decisions: none.

## Verification findings

- A verifier found no code-proven frontend defect that alone explains every Flower chart being empty. The plan must diagnose the real GET path first: connectivity/status, CAGG coverage and returned series, live values, control response, Zod parsing, alignment/filtering, then uPlot lifecycle.
- The dropped `devices`/`pid` alignment path is confirmed but explains only missing device/PID lines, not globally empty sensor charts.
- The existing store already implements range-once plus one-second sensor/control tailing, stable fixed-range zoom, and projection refresh. The preferred plan repairs and proves this path rather than replacing it.
- The existing fixture Playwright harness intentionally blocks production origins. Keep that guard intact; use agent-run browser QA against the deployed read-only page/API as separate evidence rather than weakening the fixture harness.

## Approach decision

- preferred: repair and verify the existing frontend + backend + automation-service path in place.
- robustness constraints: historical range and statistics are fetched once per range change from aggregate-backed read paths; the one-second hot path transfers only bounded live/tail deltas; fixed historical zoom continues background collection without redraw; projection reloads only on anchor/revision expiry; uPlot is updated in place and capped/coarsened rather than recreated; monitoring errors degrade only the UI and must never delay or change the automation control loop.
- automation isolation acceptance: prove the normal one-viewer 1 Hz monitoring profile does not change the configured one-second automation cadence; if it does, stop and use the fallback facade/read-model boundary rather than tuning the control loop around the UI.
- fallback trigger: introduce a single monitoring facade inside the existing backend only if real-GET diagnostics prove cross-service response composition is the global empty-chart root cause and cannot be corrected at the current contract boundaries.
- rejected by default: a new monitoring gateway/service; existing endpoints, aggregate tiers, tail cursors, and projections already cover the requested behavior, so a service would add deployment and duplication without solving a proven gap.
- future extraction seam: centralize frontend access behind typed sensor/control adapters and stable chart-ready contracts; retain cursor/timestamp/provenance semantics; keep control calculations out of the frontend and prohibit direct browser-to-DB access. A future monitoring service may implement the same boundary without rewriting panels or chart logic.
- scope revision pending delegated evidence: evaluate an immutable authoritative monitoring snapshot/read-model boundary, similar in intent to relay snapshots, if it reduces frontend cross-service composition and isolates the control loop without introducing stale dual truths.
- owner architecture decision supersedes pre-split-only approach: create a dedicated read-only monitoring service in this refactor, after establishing an authoritative immutable publication/snapshot boundary. Monitoring gets its own process, bounded read pool, Redis reader, API, and frontend ownership; automation remains sole owner of control decisions, hardware, state writes, and published current/future control facts.
- scope revision accepted: simplify frontend series around explicit semantic fields rather than labels/parsed keys; remove verified dead code, stale dashboards/scripts, production-wired test-only instrumentation, and debug placeholder tests.
- explicit non-goals: no outbox, broker, event sourcing, read replica, second database, direct monitoring writes, or duplicated control algorithms/source of truth. A read replica is a future measured optimization; an outbox is only for a future external consumer.
- canonical ownership: raw control tables and Redis state remain automation-owned source facts; Timescale continuous aggregates remain database-owned rebuildable projections; the neutral monitoring snapshot is immutable derived read state, never an authority for actuation.
- accuracy rule for the split: past values come only from committed recorded facts; current values come only from atomic versioned automation publications; future setpoints/photoperiod come only from an automation-owned versioned projection publication. Monitoring may transform for presentation but must never independently recalculate or guess control decisions. Missing/stale versions render unavailable/stale, never synthesized values.
- LLM-friendly refactor rules: prefer deletion and consolidation over new abstractions; one obvious folder per responsibility; direct imports instead of barrel indirection; explicit semantic fields instead of parsed strings/labels; one canonical dashboard/config source; small files only where they represent real cohesive concepts; every compatibility layer must have a current caller and deletion condition.

## Three-approach frame (pending interview)

1. Repair current two-service implementation in place against the active 6/4 dashboard contract — smallest change and preferred if current contracts are sufficient.
2. Add a monitoring aggregation endpoint/read facade inside the existing backend — simpler frontend contract at the cost of backend composition work.
3. Add a dedicated monitoring gateway/service — clean isolation but highest operational and duplication cost; justified only if cross-service polling cannot meet the one-second/runtime contract.

## Approval gate

- status: revised-plan-complete
- blocking: none
- selected approach: diagnose the deployed read-only GET path first, then minimally repair existing contracts/alignment/uPlot rendering; preserve active 6/4 panels and Botanical styling; verify with minimal tests-after plus real-API and deployed-browser evidence.
- approval authorizes: writing `.omo/plans/botanical-color-spectrum-monitoring-recovery.md` only
- approval does not authorize: implementation, deployment, production access, service restart, or data mutation
- approval received: owner replied “ok” to the selected Approach 1 and asked that future service extraction remain possible.
- completed revised plan: `.omo/plans/botanical-color-spectrum-monitoring-recovery.md`
- revised structural check: 28 implementation todos and 4 final-verification todos; required headings are ordered; all executable task rows are column-zero and grammar-compliant.
- revised execution model: six waves with disjoint file ownership and explicit dependency gates for safe parallel work.
- crossover control: every task now has an exclusive write/delete allowlist; references are read-only unless separately allowlisted; out-of-allowlist edits stop the task. The Redis cleanup/migration lane is sequential because it shares canonical key-definition files, while service/frontend/Grafana lanes remain parallel where write sets are disjoint.
- Redis cleanup rule: delete only after a reference/consumer ledger proves zero use; remove related constants/writers/readers/helpers/tests together; defer active dual-namespace deletion until post-deployment evidence.
- Owner scope addition: eliminate duplicate legacy/canonical Redis namespaces through a coordinated staged migration: prove canonical value parity, move every reader and writer, remove dual-write/compatibility code, and produce an exact dry-run purge allowlist. Physical deletion from production Redis remains gated on separate deploy/purge authorization.
- review: not requested and not run, per owner instruction.
- next action: execute separately with `$start-work botanical-color-spectrum-monitoring-recovery`.
