# Decisions - Mode Switch Architecture Fix

## Architectural Decisions

## Implementation Choices

## Decisions
- Explicitly handled the 'previous_mode is None' case in pid_controller_manager.py to trigger a PID reset on the first loop iteration after restart.
- Updated control_engine.py comments to accurately reflect the dual-nature of ramp restoration (time-based vs Redis-based).
