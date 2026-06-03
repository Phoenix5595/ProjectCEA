# Task 8: Backend Repository Pattern

## Summary
Added repository pattern to backend service, extracting inline SQL from `database.py` and route logic into dedicated repository classes.

## Files Created
- `Infrastructure/backend/app/repositories/__init__.py` — package exports
- `Infrastructure/backend/app/repositories/base.py` — `BaseRepository` with pool injection, `_acquire()` context manager, `_execute()` helper
- `Infrastructure/backend/app/repositories/sensor_repository.py` — `SensorRepository` with `get_sensor_data()`, `get_live_sensors()`, aggregate tier ladder, sensor name patterns, downsampling
- `Infrastructure/backend/app/repositories/config_repository.py` — `ConfigRepository` with `get_full_config()`, `get_locations()`

## Files Modified
- `Infrastructure/backend/app/dependencies.py` — added `get_sensor_repository()`, `get_config_repository()` lazy getters
- `Infrastructure/backend/app/routes/sensors.py` — replaced `db.get_all_sensors_for_location()` with `sensor_repo.get_sensor_data()`, replaced inline sensor type enumeration with `sensor_repo.get_live_sensors()`, removed `get_sensor_suffix()` (now in repo)
- `Infrastructure/backend/app/routes/config.py` — replaced direct YAML file open with `config_repo.get_full_config()`, replaced `config_loader.get_locations()` with `config_repo.get_locations()`
- `Infrastructure/backend/app/database.py` — stripped to pool-only (409→42 lines). Removed: `_AggregateTier`, `_AGGREGATE_LADDER`, `_pick_aggregate_tier`, `get_all_sensors_for_location`, `_sensor_name_patterns`, `_get_node_id`, `_extract_sensors`, `_get_sensor_suffix`, `_downsample`. Kept: `__init__`, `_get_pool`, `close`.

## Design Decisions
- `SensorRepository` accepts `DatabaseManager` reference and overrides `_acquire()` to lazily get pool via `db._get_pool()`. This preserves the lazy pool initialization pattern while giving the repo clean connection access.
- `ConfigRepository` is file-based (no DB pool needed). Wraps YAML reads so routes don't open files directly.
- Aggregate tier ladder (`_pick_aggregate_tier`, `_AGGREGATE_LADDER`) moved verbatim — no logic changes.
- Dead code removed: `_extract_sensors`, `_get_node_id` (never called from anywhere).

## Verification
- `grep -rn "conn\.\(fetch\|execute\)" Infrastructure/backend/app/routes/ --include="*.py"` → **empty** ✅
- LSP diagnostics: 0 errors on all new/changed files (pre-existing `reportImplicitRelativeImport` warnings are codebase-wide convention, not introduced by this change)
