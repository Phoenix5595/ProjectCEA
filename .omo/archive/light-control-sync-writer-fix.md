# Light Control Data Sync Writer Fix Plan

## Context
- Objective: fix light ramping failures caused by a data sync gap between mode_parameters (UI) and schedules (automation).
- Status: Climate setpoints were fixed; lights remain non-ramping due to NULL ramp durations in schedules for Flower Room.
- Decision from Metis: implement a writer fix (sync mode_parameters -> schedules) with safety features to preserve manual overrides.

## Goals and Success Criteria
- Ramp durations in Flower Room schedules are non-NULL (default 60 minutes) for DAY/NIGHT ramps.
- Updates to mode_parameters automatically trigger schedule regeneration, without destroying manual overrides.
- All ramps are properly exercised (rise and fall) within the scheduled window.
- No regression in climate setpoint logic.
- Tests cover writer path and non-destructive synchronization.

## Scope
- IN: Update flow from mode_parameters -> schedules; atomic transaction semantics; immediate scheduler reload; data migration for existing inconsistent entries; test coverage.
- OUT: No hardware changes; no global architectural rewrites; preserve manual overrides as a priority.

## Key Decisions
- Approach: Fix Writer (Writer path synchronization)
- Guardrails: Do not delete manual schedules; preserve user overrides; ensure atomicity; logs/audit trail for changes.
- Data handling: Non-null ramp durations must be enforced in schedules; any NULLs default to 60 minutes (or configured default).
- Rollout: Target Flower Room first, then verify Veg Room if needed.

## Plan Structure (Phases)
1. Analysis and risk assessment
2. Design and update points
3. Implementation tasks
4. Testing plan (TDD recommended)
5. Migration and data cleanup
6. Validation and roll-out
7. Documentation and handoff

-## Deliverables
- Code changes to:
  - update_room_parameters path to trigger schedule regeneration
  - new sync function to propagate from mode_parameters to schedules
  - transactional wrap for update + sync
  - broadcast scheduler reload after commit
  - prevent deletion of manual schedules; add guard to preserve created_by != 'system'
  - default ramp durations when NULL (60 minutes)
- Data migration script to fix Flower Room ramp durations
- Tests: unit tests for sync path; integration tests for end-to-end refresh; tests for manual overrides preservation
- Updated docs and migration notes

## Acceptance Criteria
- Flower Room light ramps now honor ramp_up_duration and ramp_down_duration > 0 (60 min default)
- UI updates to mode_parameters trigger schedule regeneration without wiping manual overrides
- Transactions ensure atomicity: partial failures rollback
- Scheduler reload occurs automatically after commit
- Tests pass (unit + integration)
- No change to climate ramp logic

- ## Plan Patched: Execution Details
- Patch 1: Writer wrapper around update_room_parameters in Infrastructure/automation-service/app/routes/room_modes.py
- Patch 2: New module Infrastructure/automation-service/app/sync/mode_parameters_sync.py with function sync_mode_parameters_to_schedules(location, cluster)
- Patch 3: Modify Infrastructure/automation-service/app/repositories/schedules.py to preserve manual schedules during sync and ensure ramp fields are populated
- Patch 4: Enforce non-null ramp durations in code paths and add a data migration to initialize missing ramp values in Flower Room
- Patch 5: Data migration script to fix Flower Room ramp durations and a one-time cleanup routine
- Patch 6: Tests: unit tests for sync path; integration tests for end-to-end write+sync; tests for manual override preservation
- Patch 7: Documentation updates and migration notes

## Next Steps
- Confirm with user: proceed with writer fix (Option 1) as planned
- Create and apply patches
- Run tests (unit/integration)
- Migrate data
- Validate in staging
- Roll out to production in controlled fashion

Plan saved to: .sisyphus/plans/light-control-sync-writer-fix.md
- Code changes to:
  - update_room_parameters path to trigger schedule regeneration
  - new sync function to propagate from mode_parameters to schedules
  - transactional wrap for update + sync
  - broadcast scheduler reload after commit
  - prevent deletion of manual schedules; add guard to preserve created_by != 'system'
  - default ramp durations when NULL (60 minutes)
- Data migration script to fix Flower Room ramp durations
- Tests: unit tests for sync path; integration tests for end-to-end refresh; tests for manual overrides preservation
- Updated docs and migration notes

## Acceptance Criteria
- Flower Room light ramps now honor ramp_up_duration and ramp_down_duration > 0 (60 min default)
- UI updates to mode_parameters trigger schedule regeneration without wiping manual overrides
- Transactions ensure atomicity: partial failures rollback
- Scheduler reload occurs automatically after commit
- Tests pass (unit + integration)
- No change to climate ramp logic

## Risk and Mitigations
- Risk: Overwriting manual schedules
 Mitigation: Modify the sync logic to identify and preserve "manual" schedules (needs a flag or heuristic, e.g., created_by != system).
- Risk: Data race between writer and scheduler reload
 Mitigation: Wrap in a DB transaction and explicit broadcast to scheduler
- Risk: Ramp Continuity
 Mitigation: Ensure ramp duration is passed correctly on mid-ramp changes

## Testing Strategy (TDD)
- Test 1: Non-destructive update path for mode_parameters -> schedules
- Test 2: Ramp durations correctly populated for all Flower Room lights
- Test 3: Manual override retention
- Test 4: Atomicity: both updates in a single transaction
- Test 5: Broadcast results cause scheduler reload
- Test 6: End-to-end: small integration test that simulates time-based ramp

## Implementation Plan (Proposed Patches)
- Patch 1: Modify update_room_parameters to call a new sync routine and wrap in transaction
- Patch 2: Implement sync routine in a new module: sync_mode_parameters_to_schedules
- Patch 3: Update schedules writer to preserve manual entries
- Patch 4: Enforce non-null ramp durations (default 60) wherever missing
- Patch 5: Data migration script to fix Flower Room
- Patch 6: Tests for sync path
- Patch 7: Documentation and API notes

## Next Steps
- Confirm with user: proceed with writer fix (Option 1) as planned
- Create and apply patches
- Run tests (unit/integration)
- Migrate data
- Validate in staging
- Roll out to production in controlled fashion

Plan saved to: .sisyphus/plans/light-control-sync-writer-fix.md
