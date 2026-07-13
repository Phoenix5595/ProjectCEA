---
slug: post-safety-fixup
status: awaiting-approval
intent: clear
pending-action: $start-work
approach: Run 3 pending alembic migrations against production cea_sensors, purge stale Redis keys, restart automation-service, verify control pages load
---

# Draft: post-safety-fixup

## Components (topology ledger)
| id | outcome | status | evidence |
|----|---------|--------|----------|
| DB migrations | alembic_version=04fbbb9b5ba4, device_registry seeded, light_target_intensity created | active | `sudo -u postgres psql -d cea_sensors -c "SELECT version_num FROM alembic_version;"` → currently 008_device_registry |
| Redis cleanup | stale schedule:state:* keys purged | active | `redis-cli --scan --pattern "schedule:state:*"` → currently returns 2 stale keys |
| Service restart | automation-service loads new schema into Scheduler caches | active | `journalctl -u automation-service | grep "no mode_params"` → currently every tick |
| API verification | /api/devices/Flower Room/main returns 200 (was 404) | active | `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/api/devices/Flower%20Room/main` → currently 404 |

## Open assumptions (announced defaults)
None — user answered all forks explicitly.

## Findings (cited - path:lines)
- `deploy.sh` has NO `alembic upgrade head` step (`grep alembic deploy.sh` returns nothing)
- `cea_sensors.alembic_version` = `008_device_registry` (3 migrations pending)
- `device_registry` count = 0 (migration 009 never ran)
- `light_target_intensity` table does not exist (migration 03fbbb9b5ba3 never ran)
- `/api/devices/Flower Room/main` returns 404 "Unknown location/cluster" (confirmed by user as the frontend error)
- `cluster_config.py:38` raises `HTTPException(404, "Unknown location/cluster")` when `location not in devices_config`
- `config.get_devices()` reads from device_registry (empty) → returns `{}`
- Scheduler logs "no mode_params for X/Y" every tick (failsafe mode)
- Hardware batch: "0 ok, 3 failed" every tick
- Stale Redis keys: `schedule:state:Flower Room:main`, `schedule:state:Veg Room:main`

## Decisions (with rationale)
- No deploy.sh changes — user explicitly chose manual migration control
- No frontend hardening — user explicitly scoped this out
- pg_dump backup before migration — irreversible production DB operation, mandatory safety
- Purge stale Redis keys — T10 acceptance criteria required it, was never executed

## Scope IN
- Run 3 alembic migrations (009, 03fbbb9b5ba3, 04fbbb9b5ba4) against cea_sensors
- Purge 3 Redis key patterns (schedule:state:*, cea:schedule:*:state, cea:schedule:state:*)
- Restart automation-service
- Verify all endpoints return 200 with real data

## Scope OUT (Must NOT have)
- No deploy.sh modifications
- No frontend code changes
- No backend code changes
- No ErrorBoundaries
- No auto-migration in deploy

## Open questions
None — all resolved by user answers.

## Approval gate
status: awaiting-approval
pending-action: $start-work
approach: Backup DB → run 3 migrations → purge Redis → restart service → verify endpoints
