# Learnings - Mode Switch Architecture Fix

## Conventions Discovered

## Patterns Found

## Gotchas


## Mode Transition History Table
- Created `mode_transition_history` table to track environment mode changes.
- Uses `TIMESTAMPTZ` for timestamps and `JSONB` for parameter syncing details.
- Indexed by `(location, cluster, triggered_at DESC)` for efficient filtering.
- DB Access: Used `psql -h localhost -U cea_user -d cea_sensors` as peer authentication for `cea` or `cea_user` failed via Unix socket.
## Cache Invalidation in SetpointRepository
- Added `invalidate_cache_for_location_cluster(location, cluster)` to clear specific cache entries.
- Added `invalidate_all_cache()` for full repository cache clear.
- Cache keys for `get_setpoint` follow the pattern `get_setpoint:location:cluster:mode`.
- Invalidation uses `key.startswith("get_setpoint:")` and checks for `:$location:$cluster:` substring to avoid over-clearing or missing entries.

Added deterministic ordering to three LIMIT 1 queries in schedules.py using 'id ASC' as a tie-breaker.
- Added debug endpoints in Infrastructure/automation-service/app/routes/debug.py to facilitate mode switch troubleshooting.
- The endpoints provide a comparison between UI mode (PostgreSQL), ControlEngine derived mode (Scheduler logic), and actual engine state (memory).
- Ramps can be tracked across both light (Scheduler memory) and climate (Redis).
## Patterns and Conventions
- Ramp restoration: Light ramps are time-based (auto-resuming), climate ramps are Redis-based (restored).
- PID Reset: Integrators must reset on the first tick after service startup (previous_mode is None) to prevent windup from offline periods.
