# relay-registry-control-snapshot-recovery — Planning Draft

intent: clear
review_required: false
status: approved-for-plan
pending_action: write `.omo/plans/relay-registry-control-snapshot-recovery.md`
approved_at: 2026-07-30

## Objective

Replace the failed final handoff of `.omo/plans/relay-registry-canonicalization.md` with one decision-complete plan that makes registry assignments, runtime control state, observed relay state, DFR commanded state, CRUD, frontend matrices, candidate deployment, and guarded reset/rebuild coherent and safe.

## Components ledger

| ID | Component | Outcome | Status | Evidence |
| --- | --- | --- | --- | --- |
| C1 | Registry projection | One strict PostgreSQL-backed assignment projection; DB errors never become empty | approved | `app/repositories/devices/`, `runtime_device_registry.py` |
| C2 | Control/hardware state | Split authority with desired-vs-observed reconciliation and alarms | approved | `relay_manager.py`, `relay_board_state_manager.py`, `control_engine.py` |
| C3 | Composite API/frontend store | One typed UI read model and one shared one-second store | approved | `routes/hardware.py`, `useDeviceRegistry.ts`, `DeviceManager.tsx`, `ZoneConfig.tsx` |
| C4 | Canonical live CRUD | Immutable generated identity, safe assignment changes, atomic device commands | approved | `device_registry_service.py`, `devices_crud.py`, `DeviceTable.tsx` |
| C5 | Empty/reset safety | Guarded backup/reset, exact data scope, empty-ready proof, owner rebuild | approved | `reset-device-registry.sh`, `safe_outputs.py` |
| C6 | Candidate deployment | Two-phase candidate/finalize-or-rollback with separate reset approval | approved | `deploy.sh`, `rollback-deploy.sh`, `/var/lib/projectcea/deploy_state.json` |

## Decision ledger

- PostgreSQL registry owns identity/assignments; MCP sampler owns observed relay state; control engine owns desired state/mode; DFR manager exposes acknowledged commanded intensity only.
- One versioned composite endpoint feeds Device Table, DFR panel, main matrix, and room matrices.
- Invalid legacy rows are hidden and excluded everywhere; startup all-OFF prevents their relay outputs remaining energized. Valid existing lights remain live until reset.
- A DB read failure fails startup or retains the last good live snapshot; only a successful zero-row query means empty.
- Empty registry requires all 16 relays observed OFF and all six DFR slots acknowledged at volatile 0% before readiness.
- EEPROM is strictly out of scope: no new EEPROM read/write/store operation, no reset EEPROM write, and no change to existing EEPROM-management code or policy.
- Existing photoperiod and ramp algorithms remain unchanged. Recreated lights join the current ramp position with new 10% targets for every existing room mode.
- MCP stale: immediate STALE, warning after 5s, critical after 30s; OFF attempts only until fresh. Persistent assigned mismatch becomes critical after 5s; unassigned mismatch is warning. Retry every tick and auto-clear with recovery log.
- Assigned commands become atomic `AUTO`, `MANUAL_OFF`, and `TIMED_ON(duration)` operations. Timer expiry restores prior mode. Restart cancels assigned/raw timers and returns assigned devices to AUTO.
- Physical relay labels use the verified R1–R16 mapping, never `channel + 1`.
- Relay is optional for every device. Relay conflict supports confirmed steal; DFR conflict rejects and identifies owner.
- Device name, room, and type are immutable; display name is required/editable; generated identity reuses the lowest free room/type index.
- DFR boards 0–2 and channels 0–1 always render; addresses are hidden. DFR panel retains status, Test, and Rename only. Assignment/move/delete live in Device Table.
- Delete uses simple confirmation, safely turns outputs off, removes agreed operational links, and preserves schedules.
- Reset clears registry, current device state/mappings, light targets, all light programs, effective/current device-linked control state, and exact Redis control/override keys. It preserves schedules, environmental setpoints, room modes/mode parameters, PID configuration, sensor data, and historical control actions.
- Reused machine names intentionally inherit preserved schedules and the UI displays inheritance.
- Reset startup failure auto-restores checksummed data and rolls back while the registry is still empty. A failure after rebuilding begins retains successful new devices.
- Candidate deployment is two-phase. The owner reports pass/fail; pass finalizes, fail rolls back code. Production QA CRUD/hardware changes remain in PostgreSQL across code rollback.
- Disposable PostgreSQL/Redis integration tests are allowed only with hard refusal of `cea_sensors`. Frontend automated gates are TypeScript and production build; no browser automation or Vitest is required for this plan.

## Must-NOT-Have ledger

- No EEPROM changes of any kind in this solution.
- No ramp, photoperiod, setpoint, PID, schedule, sensor-topology, or environmental-control redesign.
- No commissioning subsystem, YAML device definitions, extra service, distributed reconciliation daemon, or new frontend state dependency.
- No production data mutation by tests, subagents, or automated QA.
- No `TRUNCATE`, broad Redis flush/scan deletion, migration-based device reset, or automatic device recreation.
- No claim that DFR cached intensity is physical voltage readback or that MCP GPIO proves relay contacts/load current.

## Approval gate

The owner approved the architecture brief after the explicit EEPROM correction on 2026-07-30. Approval authorizes writing the plan only, not implementation.

## Metis gap analysis receipt

- Session: `ses_04d06ff16ffetLu2vK6S3Vo98u`
- Initial verdict: NOT READY
- Findings folded into the plan:
  - Added explicit atomic command/timer/restart lifecycle and tests.
  - Added desired-vs-observed reconciliation plus 5s/30s alarm criteria.
  - Chose startup failure on initial DB errors and last-snapshot retention for failed live replacement.
  - Added empty-registry volatile DFR command proof while keeping all EEPROM code/policy untouched.
  - Added success-path proof that `device_states` is no longer live authority.
  - Added exact frontend/backend DTO mismatch removal and generated OpenAPI types.
  - Added retained bounded diagnostic endpoint guardrail.
  - Added explicit candidate deploy/finalize/rollback implementation and stale deploy-state recovery.
  - Added schedule-inheritance, physical-label, empty-startup, reset, and deployment acceptance criteria.

## Plan generation status

status: plan-written
plan: `.omo/plans/relay-registry-control-snapshot-recovery.md`
review_required: false
next_action: offer start-work or optional dual high-accuracy review
