# Draft: Explore Automation Lights End-to-End

## Purpose
- Diagnose end-to-end lights and setpoint path in the local automation system across multiple rooms with different schedules and intensities.
- Capture symptoms: lights not ramping down/up, lights staying on at night, effective setpoints not calculated.
- Scope: local codebase (Infrastructure/automation-service, backend, database), plus external docs and GitHub examples.

## Requirements (confirmed)
- End-to-end trace: data flows from source (DB/Redis) to scheduling/controls to actuators (CAN/relays).
- Multi-room schedules and variable light intensities supported.
- Identify root causes for nighttime lights staying on and stalled setpoint computations.
- Produce evidence-backed findings, gaps, and concrete next steps.
- Exhaustive local discovery + external docs + GitHub examples.

## Technical Decisions (confirmed)
- Exhaustive parallel exploration of codebase, tests, data paths, and external references.
- Evidence handling: cite file paths, logs, commands, and external sources to reproduce findings.
- End-to-end data path diagram included (text/ASCII) for clarity.
- Per-room models and interfaces documented explicitly.
- Guardrails: scope IN/OUT, data integrity, concurrency, failure, documentation.
- Verification: manual QA with concrete commands plus targeted automated hooks where feasible.

## Research Findings (consolidated)
- Parallel code exploration launched (local) and external references collected.
- External references include: lighting control best practices, CEA/photoperiod guides, CAN bus integration examples, TimescaleDB/Redis patterns, and greenhouse/automation community examples.
- Plan now structured with explicit acceptance criteria, edge cases, and quick wins.

## Open Questions (decisions needed)
- Data-path modeling depth: high-detail per-hop diagram vs high-level bullet map? (Plan provides both; you choose.)
- Per-room independence: are rooms fully independent or do shared resources introduce contention? (Plan assumes independence; confirm.)
- Verification approach: exhaustive manual QA only, or mix with lightweight automated checks? (Plan leans to manual QA with automated hooks where feasible.)
- Scheduling DST handling policy: UTC everywhere vs local time with DST? (Plan leaves as decision; recommend UTC with DST-aware schedule loading.)
- Quick-wins inclusion: do you want the low-risk high-value quick-win list in the plan? (Included by default.)

## Scope Boundaries
- IN: Local repo paths; per-room data models; per-room CAN interfaces; scheduling logic; data paths Redis/TimescaleDB; related tests and configs.
- OUT: Production hardware integration; non-light subsystems; deployments; physical hardware tests.

## Quick Wins (low-risk, high-value)
- Enable per-room time-stamped logs for setpoint writes (room_id, timestamp, new_value).
- Validate startup path loads each room’s schedule from the correct source.
- Confirm time zone configuration is consistent across Redis and TimescaleDB.
- Run a dry-run mode if supported to verify end-to-end path without actuating hardware.
- Create a mini end-to-end test script that simulates a single-room end-to-end flow and logs all steps.

## End-to-End Data Path (text/ASCII diagram)
```
Sources:
├─ Redis (sensor/state keys per room)
├─ TimescaleDB (measurements, historical setpoints)
│
Scheduler/Control Engine:
├─ Load room schedules per room_id
├─ Compute setpoints based on photoperiod, intensity, timezone, DST flag
│
Setpoint Computation:
├─ Target brightness/intensity per room
├─ Ramp profiles (on/off transitions)
│
Hardware Interface Abstraction:
├─ CAN message generation per room
└─ Relay/PWM commands to light actuators
```

## Per-Room Data Models and Interfaces
- Room context: room_id, schedule_id, intensity, ramp_curve, timezone, DST_flag.
- Data stores: Redis keys per room for current state; TimescaleDB tables for historical setpoints and measurements.
- Hardware interface points: CAN message structures per room; brightness/PWM mappings.
- Traceability: event_id linking source, timestamp, room_id, action, and outcome.

## Guardrails
- Scope IN/OUT as listed.
- Data integrity: require trace IDs for setpoint events; ensure proper time-zone handling and DST alignment.
- Concurrency: define whether room updates race and how to serialize (locks or ordering).
- Failure handling: define retry/backoff, fallback states when CAN messages fail or data is delayed.
- Verification: define concrete commands, logs, and artifacts to collect during verification.
- Documentation: each finding must cite exact files/sections/DB keys; avoid vague references.

## Edge Cases (prioritized)
- DST transitions causing schedule drift.
- Data staleness between Redis and TimescaleDB.
- Multiple schedulers updating the same room (if applicable).
- Rapid, non-monotonic lighting transitions or abrupt intensity changes.
- CAN bus message loss and subsequent fallback behavior.

## Decisions Needed (user to resolve)
- Confirm per-room independence (shared resources?) and concurrency policy.
- Choose verification approach (manual only vs manual + automated hooks).
- Set DST handling policy (UTC everywhere vs local time with DST).
- Confirm if quick-wins list should stay in the plan.

## Acceptance Criteria (key TODOs)
- AC1: Document end-to-end path per room with inputs/outputs at each hop.
- AC2: Confirm per-room schedules are loaded and honored (start, end, ramp).
- AC3: Verify setpoint computation path exists and is consumed by actuator interface.
- AC4: Validate end-to-end latency from schedule trigger to CAN command is within defined bound.
- AC5: Ensure logs provide traceability (source, timestamp, room_id, action, outcome).
- AC6: Edge-case handling documented (DST, lag, lost CAN messages).
- AC7: Quick-win verifications defined (low-risk, high-value).

## Evidence Sources (to be populated)
- Local code paths and module boundaries discovered in Infrastructure/automation-service, Infrastructure/backend.
- Data paths through Redis (sensor/state) and TimescaleDB (measurements).
- Hardware interface patterns via CAN bus or similar in the automation stack.
- External docs and GitHub examples: best practices, reference architectures, and failure-mode guides.

"I'm recording our exploration draft in .sisyphus/drafts/explore-automation-lights.md" 
