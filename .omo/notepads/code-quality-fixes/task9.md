# Task 9: StateManager Type Annotations

## File
`Infrastructure/automation-service/app/state/__init__.py` (1048 → 1094 lines)

## Changes Made

### Import
- Added `TypedDict` to `from typing import ...` (line 47)

### TypedDict Classes (after line 71)
- `PIDParams`: kp, ki, kd (float), updated_at (str | None)
- `RampState`: location, cluster, setpoint_type, start_value, target_value, start_time, end_time, ramp_minutes
- `AlarmDict`: type, message, severity, timestamp, acknowledged (str, str, str, str, bool)
- `FailsafeState(TypedDict, total=False)`: active, reason, timestamp, device, severity

### Return Type Replacements
| Method | Before | After |
|--------|--------|-------|
| `get_pid_params` | `dict[str, Any] \| None` | `PIDParams \| None` |
| `get_ramp_state` | `dict[str, Any] \| None` | `RampState \| None` |
| `get_stats` | `dict[str, Any]` | `dict[str, int \| float \| str]` |

### Type Ignore Comments
- `get_pid_params` line 284: `# pyright: ignore[reportReturnType]` on `{"raw": data_str}` fallback
- `get_ramp_state` line 387: `# pyright: ignore[reportReturnType]` on `{"raw": data_str}` fallback

These are intentional graceful degradation paths (corrupt JSON → return raw string wrapper) that don't match the TypedDict shape but must be preserved per spec.

## Pre-existing LSP Errors (not introduced by this change)
- Lines 53-54: Implicit relative imports for `app.redis.ttl` and `app.redis.validation` (inside `try/except` block — deliberate)
- Line 53: Type assignment mismatch in fallback `get_ttl_by_key_type`
- Line 165: `SchemaValidationMixin` argument to class (fallback mixin pattern)
- Lines 442, 754, 840, 879, 908, 980: Redis `ResponseT` type stub issues (redis-py async type stubs incomplete)

## Verification
- Smoke test: `python -c "from app.state import StateManager; ..."` → OK (exit 0)
- No new LSP errors introduced (2 new errors from TypedDict narrowing suppressed with `pyright: ignore`)

## Notes
- `get()` / `set()` kept as `Any` per spec (Redis data is arbitrary)
- `FailsafeState` defined but not yet used on method signatures (spec kept `get_failsafe → dict[str, Any] | None`)
- `AlarmDict` defined but not yet used on method signatures (spec kept `get_alarms → dict[str, dict[str, Any]]`)
- All other methods in the spec already had correct return types before this change
