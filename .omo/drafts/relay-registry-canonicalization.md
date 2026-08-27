---
slug: relay-registry-canonicalization
status: review-approved
intent: clear
review_required: true
pending-action: hand the approved eight-task replacement plan back to the owner; implementation starts only in a separate worker session
approach: Implement only the original three outcomes: registry-driven assignments for control/matrices, accurate relay GPIO state through one small snapshot, and reliable live registry CRUD followed by one guarded reset/manual rebuild.
---

# Draft: relay-registry-canonicalization

## Reinspection — 2026-07-29

- Execution stopped after the first parallel foundation tasks because the plan proved too coupled and too complex to orchestrate safely.
- Confirmed owner decision: retain a board-level relay snapshot rather than independent per-relay messages, but simplify it.
- Simplified relay-state contract: sample all 16 MCP GPIO bits together with two register reads; maintain one latest board state; update each channel's `changed_at` only on a boolean transition; polling an unchanged channel never resets its timer.
- Remove redundant public relay metadata unless a verified consumer requires it: no public `quality`, `source`, `mcp_connected`, `error`, or schema-version fields. `sampled_at` records the last successful board read; `channels: null` represents no valid sample. Hardware faults belong in health/logging.
- Preferred delivery shape under review: keep the latest board snapshot in the automation-service process, return it from the hardware API, and notify the frontend only when the board state changes. Redis persistence remains an open implementation question pending consumer verification.
- Confirmed owner preference: preserve per-channel `changed_at` timestamps across automation-service restarts when practical. Default implementation: persist the single simplified board snapshot in Redis on actual channel transitions, restore it at startup, immediately resample GPIOA/GPIOB, preserve unchanged timestamps, and timestamp only bits that differ. This is best-effort restart continuity, not permanent historical telemetry or a PostgreSQL audit log.
- Confirmed owner decision: saved registry edits apply immediately after one atomic commit and coherent runtime reload; no service restart and no separate Apply button.
- Owner wants a one-time clean device rebuild after the implementation is fixed, followed by recreating the three established Veg lights. The exact destructive table boundary remains unresolved and must be explicitly confirmed before the replacement plan is approved.
- Test concern: the prior migration/test workflow damaged production data and consumed excessive context. Any replacement verification strategy must forbid production DB/Redis/I2C/network access and avoid a broad new suite; the owner still needs to choose between small pure tests plus static gates and static gates alone.
- Confirmed reset boundary: clear `device_registry` only. Preserve schedules, room modes, climate periods, setpoints, programs, telemetry, and history.
- Confirmed verification strategy: add only small pure/in-memory tests plus existing Ruff, type-check, topology, and frontend build gates. Tests must not import production adapters, connect to PostgreSQL/Redis/I2C/network, or execute migrations.
- Discovered schema constraint requiring owner clarification: deleting light rows from `device_registry` cascades to `light_target_intensity` and device-specific `light_programs` through `ON DELETE CASCADE` in `alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py`. Preserving all dependent rows while deleting their referenced registry IDs therefore requires backup/remapping or retaining selected device rows.
- Owner clarified dependent-light behavior: do not restore old device-linked target rows after the registry reset. A newly entered light inherits its room/cluster photoperiod from the room's existing mode configuration. The stored initial target value remains unresolved below.
- Current code contradicts that decision: `app/services/schedule_auto_create.py` creates `10.0` rows and calls the unfiltered `get_room_modes()`, while `app/control/scheduler/light_intensity.py` and `ramp_calculator.py` hardcode a `10.0` missing-target/minimum fallback. The replacement plan must correct the room filtering and explicitly resolve the missing-target fallback semantics.
- Confirmed effective photoperiod rule: inside the room photoperiod, a light must never run below `10.0%`; outside the photoperiod it is `0.0%`. Missing/corrupt target data also uses the `10.0%` failsafe and raises/retains a visible warning.
- Do not rewrite the ramp calculator. Its existing overnight photoperiod, ramp-up, ramp-down, restart resume, and target-change behavior is working and remains out of scope. Enforce the 10–100 normal-target invariant at the Pydantic/API/frontend/database boundaries so the existing `min(MINIMUM_LIGHT_INTENSITY, target_intensity)` path naturally resolves to 10 for valid normal targets.
- Confirmed new-light target semantics: create and display `10.0%` target rows for every mode applicable to the assigned room/cluster. Effective output remains at least 10% inside photoperiod and 0% outside; there is no hidden “stored 0, effective 10” state.
- Confirmed normal target range: per-light automatic target-intensity rows and their frontend/API controls must reject values below `10.0%` and above `100.0%`. Zero is reserved for outside-photoperiod output and explicit OFF/manual safety behavior, not a valid normal photoperiod target.
- Confirmed target-slider behavior: initialize a newly created light's target control at `10%`; set the normal slider/input minimum to `10%`; prevent saving values below 10; if an invalid sub-10 value reaches validation, show a clear error and restore the editable target control to `10%` so the owner can save 10 or adjust upward. Backend validation remains authoritative even though the frontend prevents the common invalid path.
- Confirmed program exception: supplemental program targets remain `10–100%`; an explicit `override` light program may use `0%` to intentionally force a light off during photoperiod. Validation must encode this distinction rather than applying one range to both program types.
- Confirmed room-parameter invariant: the existing rooms already have `mode_parameters`, and supported code can overwrite but not delete them. The replacement plan therefore leaves photoperiod/program/ramp ordering unchanged, retains existing defensive 10% fallback code, rejects a new light only when its assigned room genuinely has no applicable mode rows, and adds no speculative missing-mode recovery subsystem.
- Confirmed conflict behavior: occupied relay assignment requires explicit confirmation and then performs one atomic steal/unbind; occupied DFR board/channel assignment is rejected and never steals.
- Confirmed post-reset actor: the worker must not recreate Veg lights. The reset ends with an empty registry; the owner adds the three Veg lights through the frontend after the fixed system is deployed.
- Confirmed cleanup requirement: obsolete runtime code is deleted, not merely deprecated or left as dormant alternatives. The replacement plan must name every retired route, helper, schema bootstrap, YAML device path, Redis key/serializer, frontend caller, and responsibility-empty wrapper; migrate all callers first; then prove zero executable references. Historical Alembic revisions remain as immutable migration history unless independently proven safe to squash—they are not runtime dead code.
- Confirmed scope reduction after recovering the original request: remove the entire commissioning subsystem, including room-completion rules, partial toggle, Lab completion, sessions/generations, tokens, and commissioning UI. A valid saved device becomes active through the normal registry/control path; absent devices simply do not exist.
- Confirmed Alembic boundary: Alembic may remain the runtime/schema-evolution mechanism. Neither plan execution nor verification may use Alembic, migration 009, a seed, downgrade, or upgrade to add, remove, clear, or recreate `device_registry` rows. Registry mutations use the canonical service/API; the one-time clear uses its own explicitly approved transaction. Any required Alembic revision must be schema-only and data-neutral with respect to existing device rows.
- Confirmed MCP failure behavior: do not immediately spin/retry inside one control tick. A failed write leaves RelayManager/current persisted state unchanged and is recorded as a failure; if demand remains, normal control evaluation retries next tick. A failed GPIO sample preserves the last successful snapshot/timestamp, changes no timer, writes no Redis, and retries next tick.
- Confirmed reset cleanup: the one-time clear deletes `device_states` and legacy `device_mappings` together with `device_registry`. These are current runtime/mapping rows, not historical data. `device_registry` deletion may cascade its device-linked `light_target_intensity` and device-specific `light_programs`; room-level programs and all schedules/modes/climate/setpoints/telemetry/history remain preserved. The operator artifact must back up every affected row for recovery.
- Confirmed reset delivery: use a small guarded operator script rather than a permanent destructive API or a token/generation state-machine CLI. It verifies exact DB identity, creates checksummed backups, stops automation, forces MCP relays OFF and DFR outputs 0, performs one explicit transaction, and restarts the API for frontend rebuilding. The script is never executed by verification or a subagent; production execution remains a separate explicit owner-approved operation.
- The prior plan and its earlier review approvals are superseded; they do not authorize further execution.
- The owner did not reject revision work; the owner directed further simplification because the plan had grown far beyond the original request. Continue reducing orchestration complexity without discarding confirmed requirements or stopping the revision.

## Components (topology ledger)

| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | `device_registry` is the only live device/relay/DFR assignment source, with one atomic mutation path and explicit relay-steal/DFR-conflict behavior | reinspection | `Infrastructure/automation-service/app/routes/devices_crud.py`; `app/routes/lights/light_crud.py`; `app/routes/lights/dfr_assignments.py`; `app/repositories/devices/` |
| C2 | A saved registry change is visible to every control consumer on the next tick through one simple coherent runtime snapshot reload | reinspection | `app/control/runtime_device_registry.py`; `runtime_device_snapshot.py`; `control_engine.py`; `relay_manager.py`; `scheduler/__init__.py` |
| C3 | Relay state uses one simplified 16-channel board snapshot with `sampled_at` and persistent per-channel `changed_at`, without redundant quality/source fields | confirmed | `app/hardware/mcp23017.py`; `app/control/relay_snapshot_publisher.py`; `app/routes/hardware.py`; frontend relay types/view-models |
| C4 | Room photoperiod and ramp behavior remain unchanged; normal light targets are constrained to 10–100%, default to 10%, and explicit override programs alone may use 0% | confirmed | `app/control/scheduler/photoperiod.py`; `light_intensity.py`; `ramp_calculator.py`; `app/services/schedule_auto_create.py`; frontend light target controls |
| C5 | Existing shared relay matrices and device/DFR UI are reused; only missing canonical writes, live refresh, target validation, and agreed partial-commissioning controls are added | reinspection | `frontend/src/components/DeviceManager.tsx`; `pages/ZoneConfig.tsx`; `components/devices/DeviceTable.tsx`; `DfrBoardsPanel.tsx`; `RelayChannelMatrix.tsx` |
| C6 | A one-time operator-approved registry-only reset preserves schedules, room/mode/climate/setpoint/telemetry/history data; the owner manually recreates Veg lights afterward | confirmed | `alembic/versions/03fbbb9b5ba3_add_light_targets_and_programs.py`; `app/repositories/devices/`; root `AGENTS.md` production safety rules |
| C7 | Partial foundation code and obsolete YAML/light/DFR mutation paths are either simplified into the final design or removed, with no dormant parallel architecture | reinspection | current `git diff`; `automation_config.yaml`; `app/schema/commissioning.py`; affected `AGENTS.md` files |

## Open assumptions (announced defaults)

| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Runtime registry caching | One immutable in-process device snapshot, replaced after committed CRUD/reset; retain a periodic DB refresh only as missed-event recovery | Avoid per-tick DB hits and eliminate the current static `RelayManager` mapping | yes |
| Registry-change propagation | Synchronous snapshot replacement is the success path; existing config events notify secondary consumers and refresh light caches | A dropped asynchronous event must not leave hardware mapping stale | yes |
| Relay state storage | One versioned Redis board snapshot, no Streams/Lua/distributed locks/extra daemon | Single writer, 16 channels, whole-board sample | yes |
| State freshness | No key-expiry interpretation; use `sampled_at` plus explicit `live/stale/unavailable` quality | Prevent stale Redis from being rendered as OFF | yes |
| Commissioning persistence | New DB-backed commissioning state: per-room progress/completion plus one global `allow_partial_commissioning` flag | Must survive service restarts and remain auditable | yes |
| Fresh reset delivery | Guarded admin commissioning/reset workflow, not an Alembic re-seed and not a permanent unguarded bulk-delete API | Migration 009 will not rerun and production data safety is critical | yes |

## Findings (cited - path:lines)

- `ConfigLoader.get_devices()` is DB repository-backed (`app/config.py:297-303`); migration 009 seeded once and will not repopulate deleted rows.
- `RelayManager` builds channel/device maps only in its constructor (`app/control/relay_manager.py:15-64`), so current live CRUD cannot update hardware routing safely.
- Non-light `Device.channel` and `DeviceCreate.channel` are non-nullable despite the nullable DB column (`app/models/device_registry.py:24-43,116-125`); row conversion has also represented NULL incorrectly.
- Light create already requires a DFR board/channel, while relay binding is optional (`app/models/device_registry.py:46-87`).
- Relay stealing clears one row before updating the target in separate operations (`app/routes/devices_crud.py:219-248,275-287`), so it is not atomic.
- The hardware state API trusts Redis whenever the 16-element array exists and reads MCP only on cache miss (`app/routes/hardware.py:217-240`).
- Redis is refreshed only after a non-empty hardware batch; `execute()` returns before publishing when no operations are queued (`app/control/hardware_batch.py:363-377,461-506`).
- MCP pin reads are logical active-low GPIO reads, not relay-contact/load feedback (`app/hardware/mcp23017.py:193-234`). Current all-channel sampling repeats per-channel reads (`:240-253`).
- Both matrices share `RelayChannelMatrix` and `buildRelayChannelViewModels`; the room page currently loads assignments once and omits modes/override expiries (`frontend/src/pages/ZoneConfig.tsx:101-152`).
- `display_name` is already preferred by the shared relay view model (`frontend/src/components/devices/relayViewModel.ts:114-127`).
- Legacy `/api/lights` mutations omit safeguards/cache behavior present in `/api/devices/registry`; frontend DFR mutations still call the legacy paths.
- Existing MCP hardware remains bus 0/address decimal 39 (`0x27`); DFR hardware remains bus 1 with configured board IDs/addresses (`automation_config.yaml:1-17`).

## Decisions (with rationale)

- Matrix primary ON/OFF means MCP GPIO pin-reported logical state only. Command-vs-report details and actual load feedback are deferred.
- Unassigned devices are valid. Binary devices without a relay are skipped and reported as unassigned without loop failure. Lights with a valid DFR pair continue dimming without a relay.
- Every light requires a complete, unique DFR board/channel pair; relay remains optional for every device.
- Use no new automated test suite; verification is static/local (`ruff`, `tsc --noEmit`, frontend build, topology/schema checks) with no mutating production QA.
- Remove YAML `devices:` from active configuration after preserving its corrected role inventory as a commissioning validation contract; never rerun migration 009.
- Retire duplicate light CRUD/config/DFR mutation paths after migrating frontend callers to the canonical registry service; keep distinct light read/control/test endpoints that are not registry mutations.
- Fresh device-only wipe remains required. Clear the registry plus current device-dependent state/config (including `device_states`, retired `device_mappings`, all light targets, and all device- or room-level light programs), while preserving room modes, mode parameters, climate periods, setpoints, schedules, measurement data, control history, automation history, and historical effective-setpoint telemetry.
- Use no new Alembic revision, Alembic command, YAML seed, automatic restore, or automatic device creation. Create commissioning schema through an idempotent direct-SQL bootstrap and apply registry constraints inside the guarded empty-registry reset. The main worker manually creates exactly the three established Veg lights through the canonical registry while partial commissioning is OFF; the user manually recreates every other desired device through DeviceTable.
- The old controller is external, invisible to this repo, and uses different relay hardware; no integration, handshake, or ownership model is in scope.
- The project continues using the same existing MCP23017 board; force it and all DFR outputs safe during reset.
- Commissioning completeness is per room: Flower and Veg each require 3 lights + 1 heater + 1 dehumidifier + 1 canonical `exhaust`. Every plugged fan is an exhaust device; it may cool or heat and may dry or humidify depending on outdoor versus indoor conditions, but its physical actuator type remains `exhaust`. `cooling` is reserved for genuine active-cooling equipment and never satisfies the fan requirement. Lab accepts any valid combination and requires explicit mark-complete.
- Add one persistent global registry toggle, `Allow partial commissioning`, rendered directly in the frontend `DeviceTable` component. When OFF, incomplete rooms are held relay-OFF/DFR-0 for automatic control. When ON, valid devices already added may run immediately; missing-role progress remains visible. Raw relay/DFR controls must obey the same safety state, with only explicit bounded commissioning tests permitted.
- Skip F3/manual operational QA in the final verification wave by explicit owner decision; F1 plan compliance, F2 code quality, and F4 scope fidelity remain required. Production commissioning evidence is still generated by the operator workflow but receives no separate F3 reviewer pass.
- Main matrix shows all 16 channels. Room matrices grey and disable foreign-room and unassigned relay tiles. Compact-specific layout remains intact.
- `display_name` is the frontend label on registry, DFR, main matrix, room matrix, light sliders, and related controls; canonical `device_name` remains machine identity.

## Scope IN

- Backend models, migration constraints, canonical registry service/repository transactions, live runtime snapshots, config events, RelayManager/interlock/Scheduler updates, safe nullable behavior, commissioning state/API/reset workflow, relay GPIO sampling/state API, and cache invalidation.
- Frontend API/types, registry forms and commissioning controls, DFR panel consolidation, shared relay state hook/view model, main/compact matrix synchronization, room greying, light-slider discovery, and display-name propagation.
- Removal/deprecation of active YAML device definitions and duplicate mutation routes/callers.
- Reversible production commissioning runbook with backups, explicit DB identity guard for `cea_sensors`, force-off verification, gradual manual rebuild, partial-mode behavior, per-room completion, and rollback artifacts.

## Scope OUT (Must NOT have)

- No integration with the external old controller.
- No new MCP/DFR hardware, contact sensors, current sensing, or claim that pin state proves load power.
- No Redis registry source of truth, MQTT/device shadow, Redis Streams for relay state, Lua, distributed locks, or separate reconciliation daemon.
- No deletion of sensor telemetry or historical control/automation/effective-setpoint data.
- No production POST/PUT/DELETE performed by QA agents; no Playwright against production.
- No Alembic use for this change, migration-009 rerun, direct YAML re-seed, automatic device creation, unsafe `TRUNCATE`, or incorrect I2C address/bus changes.
- No command-vs-pin UI details in this scope.

## Open questions

None.

## High-accuracy review ledger

- Replacement-plan mandatory Metis review: `ses_050937ea3ffeHjj4EfvkPYRpJB`. Accepted: 3-wave/approximately 12–15 todo decomposition, removal of sessions/tokens/fence/per-tick Redis/direct bootstrap, canonical transaction service, transition-only simplified relay snapshot, room-filtered targets, focused tests, and operator script. Rejected: Metis's suggestion to remove the already-approved partial-commissioning toggle; the owner explicitly directed preservation of the prior toggle semantics.
- Simplified replacement written at `.omo/plans/relay-registry-canonicalization.md`: 15 todos / 3 waves. Dual replacement-plan review pending; all earlier Momus/Oracle approvals apply only to the superseded 29-todo plan.
- Final scope reset: the owner supplied the original three-part request verbatim and approved removing commissioning extras. The plan is now 8 todos / 2 waves and is the only candidate for review; the 29- and 15-todo versions are superseded.
- Eight-task review round found and corrected only within existing todos: serialize commit plus snapshot publication; name the single relay sampler; retain mode/override metadata separately; sandbox reset QA; verify empty-registry startup; define safe-output proof semantics/permissions; safe-off ordinary registry mutations; and prevent false successful persistence while retrying failed automatic commands on the next tick. No todo was added.
- Final fresh replacement-plan review approved unconditionally: Momus `ses_05058c027ffesFW576jBa7MiRv` returned `OKAY`; Oracle `ses_05058bfbaffeUxE94dC43MYqYU` returned `OKAY`.
- Review status: the current 8-todo/2-wave plan is approved. All earlier review receipts refer to superseded versions.

- Earlier review rounds rejected the plan for an incorrect production DB user, undefined occupied-DFR behavior, non-executable operational Todos 23-29, an output-state exception in Todo 27, incomplete schema rollback, pre-reset relay conflict handling, and undefined default light targets.
- Corrections now specify `cea_sensors`/`cea_user`/deployed localhost:5432, reject occupied DFR slots with 409, perform application-level relay locking before and after reset, create 10.0 targets for every applicable room mode, restore captured schema before data, require unconditional MCP-OFF/DFR-0, and give exact actors/commands/artifacts for Todos 23-29.
- Fresh dual-review round rejected: Momus `ses_05106a974fferlff3Gk9bzW9Kt`; Oracle `ses_05106a921ffe3AY8BPxtgw3J9u`.
- Round fixes: add API-key headers/redaction to every protected production request; define a complete CLI including pure `validate-contract` scenarios; remove stale `--dry-run`/undefined fault flags; make the TypeScript negative probe exact; reopen the output fence after every no-commit failure; make close/export/rollback commands complete with a safety-default partial mode OFF; capture `BASE_SHA` before Todo 1 and use it in F1/F2/F4.
- Second fresh round rejected: Momus `ses_05101ba95ffeHCCnGa69NsYtq7`; Oracle `ses_05101b8c9ffejCCiKVd2MwZLeG`.
- Second-round fixes: inline all three Veg POST payloads; enumerate five-table backup/delete/restore ordering; add an unguarded positive TypeScript quality assertion; make API helpers prepend the local base URL; fully authenticate Todo 28 and spell out its export command; reorder Todo 29 to check before close and inline both destructive recovery commands; load `BASE_SHA` explicitly in F2.
- Final fresh round approved unconditionally: Momus `ses_050fb8881ffeUToO0sIe2vcdiT` returned `OKAY`; Oracle `ses_050fb859fffev5p898xZCgAzwI` returned `OKAY`.
- Owner clarification after that approval: all plugged fans are physically exhaust devices. Their thermal and moisture effects are contextual, based on outdoor-versus-indoor conditions; they must not be reclassified as `cooling`, `heating`, `dehumidifier`, or `humidifier` based on effect.
- Taxonomy correction: UI `fan` and `extraction fan` normalize to `exhaust`; `cooling` remains available only for genuine active-cooling equipment; Flower/Veg completion specifically requires `exhaust`.
- User requested explicit assurance that obsolete code and affected agent guides are cleaned up. The plan now enumerates retired backend/frontend symbols and routes, requires zero executable references and deletion of responsibility-empty modules/wrappers, and mandates updates to root, automation-service, control, frontend, and database `AGENTS.md` with an audit of all others.
- Fresh taxonomy review: Momus `ses_050efdefcffez2Yi5eGYg8s41w` rejected on the missing heating-failure exhaust interlock and an abbreviated Todo-28 command; Oracle `ses_050efde8effeFZG3zcGwD7nTNG` returned `OKAY`.
- Corrections: Todo 8 now implements and verifies heating-failure → exhaust inhibition; Todo 28 inlines the complete authenticated production-host export command.
- Cleanup review: Momus `ses_050e93363ffe2cuXz657lhWPMW` returned `OKAY`; Oracle `ses_050e93099ffe2Ufh8V6Wjw5aeE` rejected because two ConfigLoader mutators and direct repository light mutators were not explicitly retired/internalized, and raw/bounded channel writes were not explicitly covered by heating-failure exhaust inhibition.
- Corrections: Todo 20 now deletes both ConfigLoader mutators and internalizes transaction-bound light repository helpers behind `DeviceRegistryService`; Todos 8/12 now resolve raw and bounded channel requests through the runtime snapshot and block assigned exhaust across every write/recovery/startup path during heating failure.
- Latest cleanup review: Momus `ses_050e33768ffeLV36UgQdrAem10` rejected because Todo 27 called documented `GET /api/light-target-intensities` although the current route file lacks that GET; Oracle `ses_050e3372affe9oppeOVRahLd9j` returned `OKAY`.
- Correction: Todo 19 now explicitly implements the typed, filterable, read-only target-intensity GET and verifies that it returns every applicable mode row; Todo 27's authenticated commissioning check now has a planned endpoint.
- Target-endpoint review: Momus `ses_050ddae94ffearLoRFLEdnjY9D` returned `OKAY`; Oracle `ses_050ddabe0ffes1oL5NRnlRJLJV` rejected malformed rollback-shell quoting in Todo 29.
- Correction: Todo 29 now supersedes those fragments with two positional-argument-safe, fully quoted export-token and rollback commands and requires outputs to stay fenced on command failure.
- Final current-plan review approved unconditionally: Momus `ses_050d95523ffejYa3y23gD4l7M1` returned `OKAY`; Oracle `ses_050d952e3ffeFPzl64J91YvBoQ` returned `OKAY`.
- Review status: dual high-accuracy review passed for the current plan; it is ready for separate worker execution.

## Approval gate

status: approved-for-rewrite
pending action: replace the superseded 29-todo plan with the simpler decision-complete plan, run the required high-accuracy review, and hand it back without execution.

Approval: the owner explicitly said “revise it now” after the reinspection interview. This authorizes rewriting the plan only. It does not authorize implementation, deployment, production requests, registry deletion, or hardware changes.
