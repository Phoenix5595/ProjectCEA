
## 2026-07-05: Migration 006_add_control_history_manual_expires_at

### What was done
- Created `Infrastructure/automation-service/alembic/versions/006_add_control_history_manual_expires_at.py`
- Added `manual_expires_at TIMESTAMPTZ NULL` column to `control_history` table
- Added partial index: `idx_control_history_manual_expires ON control_history(manual_expires_at) WHERE manual_expires_at IS NOT NULL AND mode = 'manual'`

### Pattern followed
- Copied exact structure from `002_add_control_history_load_percent.py`
- Used `op.execute()` for all DDL (not SQLAlchemy column objects)
- `revision = "006_manual_expires_at"`, `down_revision = "005_phase5a_reconcile"`

### Verification
- Applied manually via `psql` (as postgres user) since local alembic env has credential issues
- Confirmed via `\d control_history`: column + partial index present
- Downgrade SQL verified: `DROP INDEX IF EXISTS idx_control_history_manual_expires; ALTER TABLE control_history DROP COLUMN IF EXISTS manual_expires_at;`

### Notes
- deploy.sh does NOT run alembic — the service runs `run_alembic_migrations()` on startup
- A failed migration logs a warning but does NOT fail the health check
- Verify migrations applied via `psql \d` after deploy, not via deploy.sh logs

## 2026-07-05: Migration 007_add_pid_parameters_per_room

### What was done
- Created `Infrastructure/automation-service/alembic/versions/007_add_pid_parameters_per_room.py`
- Added `location TEXT`, `cluster TEXT`, `binary_hysteresis REAL` columns to BOTH `pid_parameters` and `pid_parameter_history`
- Changed PK on `pid_parameters` from `device_type` to composite `(location, cluster, device_type)`
- Backfilled existing rows with `location='Flower Room', cluster='main', binary_hysteresis=0.1`

### Pattern followed
- Used `op.execute()` for all DDL (consistent with 002, 006)
- `revision = "007_pid_per_room"`, `down_revision = "006_manual_expires_at"`
- Backfill BEFORE dropping old PK to avoid NULL values in new PK columns

### Schema before
- `pid_parameters`: `device_type TEXT PRIMARY KEY`, plus `control_mode`, `hysteresis_high`, `hysteresis_low`
- `pid_parameter_history`: `id BIGSERIAL PRIMARY KEY`, `device_type TEXT NOT NULL`

### Schema after
- `pid_parameters`: composite PK `(location, cluster, device_type)`, columns `location TEXT NOT NULL`, `cluster TEXT NOT NULL`, `binary_hysteresis REAL`
- `pid_parameter_history`: columns `location TEXT`, `cluster TEXT`, `binary_hysteresis REAL` (no PK change — `id` remains PK)

### Verification
- `alembic upgrade head` applied successfully
- `psql \d pid_parameters` confirmed: new columns + composite PK
- `psql \d pid_parameter_history` confirmed: new columns present
- Round-trip verified: `alembic downgrade -1` → original schema restored → `alembic upgrade head` → new schema restored

### Notes
- Alembic must be run from `Infrastructure/` directory with `PYTHONPATH=/home/antoine/ProjectCEA/Infrastructure` because `shared.db_credentials` is at `Infrastructure/shared/`
- Database name is `cea_sensors` (not `projectcea`)
- Password sourced from `/opt/projectcea/shared/env/postgres.env`


## 2026-07-05: Wave 1 — Fix _log_control_action to read actual relay state

### Problem
`_log_control_action` was computing `new_state = 1 if control_output > 0.5 else 0` instead of reading the actual relay state. This caused spurious transition logs when hysteresis kept the relay in its current state but the raw control_output crossed the 0.5 threshold.

### Fix
Changed line 767 in `_log_control_action` from:
```python
new_state = 1 if control_output > 0.5 else 0
```
to:
```python
new_state = self.relay_manager.get_device_state(location, cluster, device_name) or 0
```

### Why this works
- `_apply_control_output` already captures `old_state` from `relay_manager.get_device_state` before calling `_control_binary_device`
- `_control_binary_device` applies hysteresis and may or may not change the actual relay state
- After `_control_binary_device` returns, `_log_control_action` now reads the ACTUAL relay state for `new_state`
- The repo method `log_control_action` short-circuits on `old_state == new_state` (lines 60-61)
- When hysteresis preserves the state, `old_state == new_state` and no spurious log is written

### Test cases
1. `test_off_in_band_no_log`: Device OFF, control_output=0.55 (in band) → hysteresis keeps OFF, old=0/new=0 → repo short-circuits
2. `test_off_above_band_logs_transition`: Device OFF, control_output=0.7 (above band) → hysteresis allows ON, old=0/new=1 → log written
3. `test_on_in_band_no_log`: Device ON, control_output=0.45 (in band) → hysteresis keeps ON, old=1/new=1 → repo short-circuits

### Verification
- pyright: 5 pre-existing errors on unrelated lines (10, 431, 868), 0 new errors
- pytest: 92/92 tests pass (including 3 new tests)


## 2026-07-05: Task 3 — Verify /api/hardware/relays/state timestamp accuracy

### Verification checklist
- [x] `GET /api/hardware/relays/state` returns `timestamps: (string|null)[]` array of 16 elements
- [x] Timestamps reflect actual relay transitions (not spurious in-band hysteresis output)
- [x] NO new endpoint was created (`grep -r "state-changes"` returns 0)
- [x] Evidence captured in `.omo/evidence/task-3-settings-relay-matrix-dfr-cleanup.txt`

### Endpoint analysis
**File:** `Infrastructure/automation-service/app/routes/hardware.py:98-151`

The `relay_state` endpoint:
1. Reads relay channel states from Redis cache (16 booleans)
2. Reads per-channel timestamps from Redis (`RELAY_TIMESTAMPS`) — initialized as `[None] * 16`
3. Falls back to TimescaleDB `control_action_repo.get_last_changed_per_channel()` only on cold start (all nulls)
4. Returns `{channels: bool[16], timestamps: (string|null)[16], mcp_connected: bool}`

### Live curl output
```json
{
    "channels": [false, false, false, false, false, false, false, false, false, false, false, false, false, false, false, false],
    "timestamps": [
        null, null,
        "2026-06-24T00:00:24.918995Z",
        "2026-07-05T14:00:00.811634Z",
        "2026-07-05T14:00:00.811634Z",
        "2026-07-05T14:00:00.811634Z",
        null, null, null, null, null, null, null, null, null, null
    ],
    "mcp_connected": true
}
```

- Array length: 16 elements ✓
- Types: ISO 8601 strings + null for never-changed channels ✓

### Why timestamps are accurate after Todo 2
Todo 2 fixed `_log_control_action` to read actual relay state after hysteresis is applied. The `control_action_repo.log_control_action` short-circuits on `old_state == new_state`, so no spurious log is written when hysteresis preserves state. The endpoint's DB fallback queries this clean history, and the Redis cache (`RELAY_TIMESTAMPS`) is updated by `hardware_batch` only on actual state changes.

### Frontend type check
**File:** `Infrastructure/frontend/src/services/api.ts:238`
```typescript
async getRelayBoardState(): Promise<{ channels: boolean[]; timestamps: (string | null)[]; mcp_connected: boolean; simulation: boolean }>
```
Frontend already consumes the `timestamps` field. No changes needed.

### No new endpoint
`grep -r "state-changes" Infrastructure/automation-service/app/routes/ Infrastructure/frontend/src/` → exit code 1 (no matches).
