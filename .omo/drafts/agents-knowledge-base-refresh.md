# Agent Knowledge Base Refresh — Planning Draft

## Routing

- intent: clear
- review_required: false
- status: plan-written
- pending_action: user chooses `/start-work` or optional high-accuracy review
- execution_authorized: false

## Requested outcome

Regenerate the repository's hierarchical agent guidance and consolidate still-needed project documentation so it is concise, precise, source-backed, non-duplicative, and safe for the current OpenCode workflow. The executor must not modify production state or product behavior.

## Components ledger

| ID | Component | Outcome | Status | Evidence |
|---|---|---|---|---|
| C1 | Fact authority and safety | Checked-out source is the default truth; deployment-only facts are explicitly labeled; volatile runtime snapshots are removed | decided | `ARCHITECTURE.md:3-6`; deployed release evidence from `/opt/projectcea/current/deploy_manifest.json`; `AGENTS.md:15-22,208-230` |
| C2 | Hierarchical AGENTS.md | Focused 12-file hierarchy with parent/child deduplication and strict size limits | decided | Existing 10-file hierarchy; complexity hotspots at `Infrastructure/shared/` and `Infrastructure/frontend/src/features/monitoring/`; operational ownership in `Infrastructure/iskra_stack/` |
| C3 | Active project docs | Lean canonical set; merge duplicate architecture/Grafana/setpoint material; archive historical and one-off docs | decided | `Infrastructure/REQUIREMENTS.md`; service/frontend/database audits; stale root QA/debug artifacts |
| C4 | Local OpenCode workflow | Keep local and gitignored; disable only conflicting old orchestration; retain useful notification/worktree utilities | decided | `.opencode/opencode.jsonc:112-169`; `.opencode/plugin/workspace-plugin.ts:318-512`; `.opencode/plugin/background-agents.ts:1085-1124,1243-1283` |
| C5 | Verification and drift prevention | Tests-after using static/local checks only; no production HTTP, DB, Redis, I2C, or hardware access | decided | User decision; root safety contract; local test/config inventory |

## Owner decisions

1. Truth baseline: document checked-out code; label deployment-dependent facts separately.
2. Volatile facts: remove row counts, “currently empty,” pending-deploy notes, and similar snapshots; link to authoritative code/runbooks.
3. Instruction scope: audit all instruction surfaces, with implementation focused primarily on project guidance.
4. Hierarchy mode: regenerate rather than conservatively edit.
5. Production HTTP: no mutating probes, including fake-ID DELETE requests.
6. OpenCode direction: disable only conflicting orchestration; keep changes local/gitignored.
7. AGENTS hierarchy: approved focused 12-file layout.
8. Verification: tests-after, static/local.
9. Documentation model: lean canonical docs plus dated archive for historical/one-off material.
10. Companion docs: refresh still-needed docs and keep them concise enough not to confuse or flood LLM context.

## Approved AGENTS.md topology

Retain and regenerate:

- `AGENTS.md`
- `Infrastructure/AGENTS.md`
- `Infrastructure/automation-service/AGENTS.md`
- `Infrastructure/automation-service/app/control/AGENTS.md`
- `Infrastructure/backend/AGENTS.md`
- `Infrastructure/can-processor-service/AGENTS.md`
- `Infrastructure/database/AGENTS.md`
- `Infrastructure/frontend/AGENTS.md`
- `Sensor_Nodes/AGENTS.md`

Create:

- `Infrastructure/shared/AGENTS.md`
- `Infrastructure/frontend/src/features/monitoring/AGENTS.md`
- `Infrastructure/iskra_stack/AGENTS.md`

Remove after migrating valid content:

- `Infrastructure/frontend/grafana/AGENTS.md`

## Verified high-risk contradictions to resolve

- Root incident narrative gives two durations for the same event (`AGENTS.md:22` versus `AGENTS.md:210`); retain the safety rule, not an unverified duration.
- Root says heating-failure exhaust inhibition is mandatory (`AGENTS.md:67`), but the implementation says it is intentionally not configured (`Infrastructure/automation-service/app/automation/interlock_manager.py:36-40`; `automation_config.yaml:77`).
- DFR board IDs are decimal `88/89/90`, i.e. I2C `0x58/0x59/0x5A` (`automation_config.yaml:8-17`); existing AGENTS files incorrectly render them as `0x88/0x89/0x90`.
- Control tick is configured to 1 second (`automation_config.yaml:52`), while docs repeatedly claim a fixed 2-second tick.
- Grafana uses Redis for live/current tables and PostgreSQL for time series (`Infrastructure/iskra_stack/docker-compose.yml:51-100`; `Infrastructure/iskra_stack/README.md`); database guidance claiming PostgreSQL-only is false.
- `sensor:raw` retention is 100,000 entries (`Infrastructure/shared/redis_keys.py:79-86`), not 1.1 million.
- Grafana and backend have different aggregate-routing ladders; do not publish one flattened root rule (`database/grafana_performance_migration.sql`; `backend/app/repositories/sensor_repository.py`).
- `Infrastructure/REQUIREMENTS.md:195-196` swaps weather/one-wire ports; `Infrastructure/services.yaml:64-75` is authoritative.
- Requirements files claim tests were removed, but current Python, Vitest, SQL, shell, and Playwright suites exist.
- Control guidance references nonexistent files/methods; scheduler projection now installs through `Scheduler.install_snapshot()`.
- Current frontend guidance omits the 72-file native monitoring feature and references removed `CircularTimePicker.tsx` and `grafanaDashboards.ts`.
- Current FullV6 firmware README references removed receiver scripts and legacy SQLite-style tables; runtime decoding is in `Infrastructure/can-processor-service/app/decoder.py`, and history uses the unified TimescaleDB `measurement` hypertable.

## Documentation approach to plan

- Merge `ARCHITECTURE_SCHEMATIC.md` into one concise, diagram-bearing `ARCHITECTURE.md`; archive the superseded active copy and update `.cursor/rules/architecture-schematic.mdc`.
- Rewrite root/infrastructure/service requirements as current normative contracts, not phase histories.
- Keep operator procedures in runbooks, not AGENTS.md; every production-mutating command must be labeled operator-only and never used for agent QA.
- Consolidate Grafana operations under `Infrastructure/iskra_stack/`; archive stale frontend/Grafana tutorials after migrating valid alerting/query notes.
- Merge duplicate setpoint explanations into the database contract; archive the redundant files.
- Condense active hardware/service guides (PID, soil, one-wire, FullV6, power tracking) and archive one-off QA/debug material.
- Do not delete or alter product code, migration SQL, dashboards, service scripts, legacy firmware source, `.omo/`, `.codegraph/`, or installed `.agents/skills/` as part of documentation cleanup.

## OpenCode approach to plan

- Keep `.opencode/` gitignored and local.
- Disable the auto-discovered legacy orchestration plugins non-destructively so they cannot inject `delegate`, `plan_save`, or old build/plan mandates.
- Retain `notify.ts` and `worktree.ts`.
- Remove project overrides that force the old plan/build protocol only where necessary, and minimally correct directly conflicting custom-agent clauses (especially the ban on tests).
- Keep `.agents/skills/`, `.omo/`, `.codegraph/`, and other active current-workflow surfaces.
- Require a fresh-process config inspection because OpenCode config is loaded only at startup.

## Scope guardrails

### Must have

- Source/path citations for every non-obvious operational or architectural claim.
- Parent-child inheritance with no repeated blocks.
- Root AGENTS.md 50–150 lines; child AGENTS.md 30–80 lines.
- No phase/wave history, mutable row counts, “currently empty,” or pending-deployment prose in active AGENTS files.
- Explicit production safety and no-mutating-HTTP rule.
- Dirty-worktree preservation: current monitoring work and unrelated changes must not be reset, overwritten, staged, or committed.

### Must not have

- Production HTTP, database, Redis, I2C, GPIO, CAN, or systemd mutations as QA.
- Automatic registry reset, deploy, rollback, service restart, Grafana DB mutation, or dashboard deletion.
- New product behavior, service topology, schema, test infrastructure, or CI work.
- Wholesale `.opencode/` rewrite or tracking of local config.
- Deletion of `.omo/`, `.codegraph/`, `.agents/skills/`, archives, product scripts, dashboards, migrations, or legacy firmware source.

## Verification decision

Tests-after, static/local only:

- enumerate and validate the 12-file hierarchy;
- enforce line budgets and parent-child non-duplication;
- verify relative paths, links, command names, ports, and source references;
- scan active guidance for forbidden volatile/history markers and known stale claims;
- parse local OpenCode JSONC and inspect a fresh process to prove old orchestration is no longer injected;
- run Markdown/whitespace checks and `git diff --check`;
- verify archive moves and ensure no unrelated dirty paths were changed;
- independent documentation truth/safety review with evidence artifacts under `.omo/evidence/`.

## Approval gate

Pending user approval to write exactly one decision-complete plan at:

`.omo/plans/agents-knowledge-base-refresh.md`

Approval received. The decision-complete plan was written to `.omo/plans/agents-knowledge-base-refresh.md`. Execution remains a separate `/start-work` session.

## Review receipts

- Metis attempt 1: session `ses_013ba7a4dffen1BTgdPx6DdhR5`; stalled twice for 30 minutes without status or deliverable.
- Metis reduced retry: session `ses_0137ee3d0ffewEFg7UPrcEsBTN`; aborted without a deliverable.
- User explicitly reported repeated freezing; no further Metis invocation is permitted in this session.
- Fallback: direct self-review completed against template order, placeholder scan, hierarchy count, scope separation, dirty-worktree safeguards, acceptance criteria, happy/failure QA, evidence paths, and commit grouping.
- High-accuracy Momus + Oracle review: not requested and not run.
