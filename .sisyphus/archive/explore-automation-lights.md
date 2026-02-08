# Plan Generated: Explore Automation Lights End-to-End (Exhaustive Parallel Discovery)

## Context

- Original request: Diagnose end-to-end lights and setpoint path in the local automation system across multiple rooms with different schedules and intensities; verify why lights stay on at night and setpoints are not calculated; perform exhaustive search locally and via GitHub/external docs.
- Approach: Exhaustive, parallel exploration of codebase, data paths, tests, and external references (local repo + GitHub/open sources). No code changes in this phase; focus on planning and evidence gathering.

## Interview Summary

- Problem scope: End-to-end lighting control path from data sources (Redis/TimescaleDB) through scheduler/control engine to actuators (CAN/relays) with per-room schedules and light intensities.
- Symptoms to validate: lights not ramping up/down as expected, lights remaining on at night, setpoints not being calculated or applied.
- Scope: Local repository (Infrastructure/automation-service, backend), data paths (Redis/TimescaleDB), and external references (docs, GitHub examples).
- Evidence sources: In-progress code exploration results, librarian references, web searches, and related GitHub projects will feed into the final plan.

## Metis Review (Status)
- Metis consultation could not be executed in this environment due to unavailable skill. This plan proceeds with a synthesized, evidence-based gap analysis using parallel explorations and reference materials.

## Findings (Synthesis to be Completed in-phase with exploration)
- End-to-end data path uncertainties: exact routing from data sources to setpoint computation to hardware control across rooms.
- Room-level complexities: multiple schedules and intensities; potential per-room data models and per-room control channels.
- Potentially missing guardrails around setpoint calculation when schedules mismatch or data sources lag.
- Likely integration points: Scheduler/Control Engine, Redis keys for room state, TimescaleDB measurements, CAN bus commands.

## Work Objectives

### Core Objective
- Create a comprehensive, evidence-backed map of the end-to-end lights and setpoint path, identify root causes for nighttime lights staying on and failed setpoint calculations, and propose concrete verification steps and guardrails.

### Concrete Deliverables
- .sisyphus/plans/explore-automation-lights.md (this plan) containing: context, findings, gaps, guardrails, verification plan, and decisions needed.
- .sisyphus/drafts/explore-automation-lights.md (updated with findings and questions).
- A consolidated findings draft with concrete next steps and evidence references.

### Definition of Done
- All tasks executed and documented; root causes and likely failure modes identified; next steps clearly defined; evidence sources cited.

### Must Have
- Exhaustive mapping of end-to-end data paths for lights/setpoints; per-room schedules; references to file paths and data models; verification steps.

### Must NOT Have (Guardrails)
- Physical hardware tests; deployment changes; unbounded plan scope creep. Restrictions on not editing code in this phase.

---

## End-to-End Data Path Diagram (Text/ASCII)

```
[Data Sources]                [Processing]                [Actuators]
Redis (sensor/state)   -->   Scheduler/Control Engine   -->   CAN Bus/Relays
TimescaleDB (measurements)   |   - Setpoint computation   |   - Light intensity PWM
Room Configs (schedules)      |   - Per-room contexts      |   - Per-room channels
                               |   - Timezone/DST handling  |
                               V                           V
[Per-Room Branches]
Room-1: schedule_1 --> setpoint_1 --> CAN_msg_1 --> Light_1 (intensity_1)
Room-2: schedule_2 --> setpoint_2 --> CAN_msg_2 --> Light_2 (intensity_2)
Room-3: schedule_3 --> setpoint_3 --> CAN_msg_3 --> Light_3 (intensity_3)
```

### Path Steps (per room)
1. Load room schedule and intensity profile from config/DB
2. Compute current setpoint based on time, photoperiod, and ramp curve
3. Write setpoint to Redis (room-specific key) and TimescaleDB (historical)
4. Scheduler/Control Engine reads setpoint and triggers actuation
5. CAN bus command sent with target intensity and ramp parameters
6. Hardware interface applies PWM/DAC to light fixture
7. Feedback (if any) logged back to Redis/TimescaleDB

---

## Per-Room Data Models and Interfaces

### Room Context (expected fields)
- room_id: unique identifier
- schedule_id: reference to schedule definition
- intensity_profile: ramp curve or target intensities by time
- timezone: local time zone for the room
- dst_flag: daylight saving time handling flag
- current_setpoint: last computed setpoint
- last_update_timestamp: when setpoint was last applied

### Data Stores
- Redis keys (per room)
  - `room:{room_id}:setpoint` -> current setpoint value
  - `room:{room_id}:schedule` -> active schedule blob
  - `room:{room_id}:state` -> current state (on/off, ramping)
- TimescaleDB tables
  - `room_setpoints` (room_id, timestamp, setpoint_value, source)
  - `light_measurements` (room_id, timestamp, measured_intensity)

### Hardware Interface Points
- CAN message structure (per room)
  - room_id, target_intensity, ramp_duration, command_type
- PWM/DAC mapping
  - intensity_value (0-100%) -> PWM duty cycle or DAC voltage

---

## Verification Strategy (MANDATORY)

### Test Decision
- Infrastructure exists: Unknown (to be determined during exploration).
- User wants tests: Exhaustive (per user instruction).
- Framework: To be determined after exploration (likely manual QA plus unit-level checks during later phases).

### Verification Steps (Manual QA)
1. End-to-end trace
   - Command: `redis-cli GET room:{room_id}:setpoint` (verify current setpoint)
   - Command: `SELECT * FROM room_setpoints WHERE room_id = '{room_id}' ORDER BY timestamp DESC LIMIT 5` (verify recent setpoints)
   - Expected: Consistent values across Redis and TimescaleDB; timestamps within expected latency
2. Schedule loading
   - Command: `redis-cli GET room:{room_id}:schedule` (verify schedule loaded)
   - Expected: Valid schedule blob with correct timezone and intensity profile
3. Actuation path
   - Monitor CAN bus logs for room-specific messages
   - Expected: CAN messages with correct room_id, intensity, and ramp parameters
4. Timezone/DST handling
   - Simulate time change and verify schedule updates correctly
   - Expected: No drift in on/off times; proper ramp adjustments
5. Failure handling
   - Temporarily block CAN messages and verify fallback behavior
   - Expected: Graceful degradation, retries logged, state preserved

---

## Guardrails (Explicit Boundaries)

### Scope Guardrails
- IN: Local repo paths, per-room data models, per-room CAN interfaces, scheduling logic, data paths Redis/TimescaleDB
- OUT: Production hardware integration, non-light subsystems

### Data Integrity Guardrails
- Require trace IDs for setpoint events and end-to-end tracing
- Ensure proper time-zone handling and DST alignment

### Concurrency Guardrails
- Define whether room updates race and how to serialize (locks or ordering)

### Failure and Retry Guardrails
- Define retry/backoff, fallback states when CAN messages fail or data is delayed

### Verification Guardrails
- Define concrete commands, logs, and artifacts to collect during verification

### Documentation Guardrails
- Each finding must cite exact files/sections/DB keys

---

## Edge Cases Not Yet Covered (Priorities)

- DST transitions causing schedule drift
- Data staleness between Redis and TimescaleDB
- Multiple schedulers updating the same room (if applicable)
- Rapid, non-monotonic lighting transitions or abrupt intensity changes
- CAN bus message loss and subsequent fallback behavior

---

## Quick Wins (Low-Risk Items to Validate Early)

- Enable per-room time-stamped logs for setpoint writes (room_id, timestamp, new_value)
- Validate startup path loads each room's schedule from the correct source
- Confirm time zone configuration is consistent across Redis and TimescaleDB
- Run a dry-run mode if supported to verify end-to-end path without actuating hardware
- Create a mini end-to-end test script that simulates a single-room end-to-end flow and logs all steps

---

## Decisions Needed (With Options)

- Data-path modeling depth: high-detail per-hop diagram vs high-level bullet map? (Included both in plan; you pick)
- Per-room independence: Are rooms fully independent, or do some shared resources introduce cross-room contention?
- Verification approach: exhaustive manual QA only, or mix with lightweight automated checks? (Plan leans toward manual QA with possible automated hooks)
- Scheduling DST handling policy: UTC everywhere vs local time with DST; which should we encode in the plan?

---

## TODOs (Single Plan: All Tasks in One Plan)

- [ ] plan-1: Debrief Metis-like gap analysis from exploration results (compile gaps and guardrails)
  - Acceptance: List top 5 gaps with rationale and questions for user input
- [ ] plan-2: Map end-to-end data path for lights and setpoints across all rooms
  - Acceptance: Diagrammatic map or bulleted path with data stores, queues, and callbacks
- [ ] plan-3: Identify per-room data models and control interfaces; verify how room schedules are stored and loaded
  - Acceptance: List of room contexts and related keys/DB tables
- [ ] plan-4: Catalog all code module boundaries involved in lighting control (scheduler, control_engine, redis clients, db access)
  - Acceptance: Module map with references
- [ ] plan-5: Review external docs and GitHub examples for end-to-end lighting control patterns; extract guardrails and best practices
  - Acceptance: Summary with 5 key practices relevant to our system
- [ ] plan-6: Propose end-to-end verification steps (manual and, where possible, automated)
  - Acceptance: Step-by-step verification plan with commands/logs to capture
- [ ] plan-7: Draft initial Findings Draft (.sisyphus/drafts/explore-automation-lights.md) with sources
  - Acceptance: Draft includes evidence references and questions
- [ ] plan-8: Draft final plan for review and handoff; present to user for go-ahead to start execution
  - Acceptance: Plan saved to .sisyphus/plans/explore-automation-lights.md
- [ ] plan-9: If user agrees, initiate plan execution via /start-work (handoff guidance)

---

## Evidence References (To Be Populated During Exploration)

- Local code paths and module boundaries discovered in Infrastructure/automation-service, Infrastructure/backend
- Data paths through Redis (sensor/state) and TimescaleDB (measurements)
- Hardware interface patterns via CAN bus or similar in the automation stack
- External docs and GitHub examples (to be collated)

---

## Success Criteria

### Verification Commands
```bash
redis-cli GET room:{room_id}:setpoint  # Expected: current setpoint value
SELECT * FROM room_setpoints WHERE room_id = '{room_id}' ORDER BY timestamp DESC LIMIT 5  # Expected: recent setpoints
redis-cli GET room:{room_id}:schedule  # Expected: valid schedule blob
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] End-to-end path documented per room
- [ ] Verification steps defined and executable
- [ ] Guardrails and edge cases documented
- [ ] Quick wins identified and prioritized

Plan saved to: `.sisyphus/plans/explore-automation-lights.md`
