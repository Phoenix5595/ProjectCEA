# Staged migrations (not auto-loaded)

Alembic only auto-discovers `.py` files under `alembic/versions/`. Files in
this directory are **authored but not yet safe to apply** and are held here
until their code prerequisites land.

## Currently staged

### `004_drop_legacy_setpoint_columns.py`

Drops 22 legacy per-period setpoint columns from `mode_parameters`. The
replacement reader (`climate_periods` table) has shipped but is not yet the
sole consumer — the following files still reference the legacy columns and
would raise `UndefinedColumn` if the migration were applied:

- `automation-service/app/repositories/schedules.py`
- `automation-service/app/routes/room_modes.py`
- `automation-service/app/events/__init__.py`
- `automation-service/app/ai_export.py`

**Re-enable procedure** (Phase 6 candidate):

1. Finish migrating the 4 files above to read from `climate_periods` only.
2. Deploy and soak 24h to confirm no legacy-column read path is hit in logs.
3. `git mv alembic/staged/004_drop_legacy_setpoint_columns.py alembic/versions/`.
4. Pick a low-traffic window, take a Postgres dump first, then
   `alembic upgrade head` on the Pi.
5. Physical replication will replay the `ALTER TABLE DROP COLUMN` DDL to
   iskraprojectcea automatically — verify `replay_lag < 1s` afterwards.

**Why staged instead of deleted:** the migration is correct (idempotent,
with a working downgrade); moving it out of `versions/` merely prevents
`alembic upgrade head` from firing it before the code is ready.
