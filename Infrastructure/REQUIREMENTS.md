# Infrastructure Requirements

Stable cross-service contracts for ProjectCEA. For current architecture, see `ARCHITECTURE.md`. For service-specific behavior, see the `REQUIREMENTS.md` under each service directory. In-house monitoring is the native React dashboards plus the read-only `monitoring-service` (`:8005`); there is no external dashboard product.

## Service Enumeration Authority

`Infrastructure/services.yaml` is the single source of truth for every managed systemd unit. It defines unit filename, repo path, start order, health and readiness URLs, and the hardware-facing flag. `Infrastructure/scripts/service_list.py` parses it; `deploy.sh`, `rollback-deploy.sh`, and `sync_systemd_units.sh` consume it. No script should hardcode a service list.

## Secrets

- The DB password lives only in `/opt/projectcea/shared/env/postgres_password` (mode `0600`). Every DB-using service loads it via `LoadCredential=postgres_password:...`; systemd copies it into `$CREDENTIALS_DIRECTORY` before the service starts. The env-var fallback is transitional.
- The API key and enforcement flag live in `/opt/projectcea/shared/env/api_key.env` (mode `0600`). Services load it via `EnvironmentFile=-...`; `CEA_API_KEY_REQUIRE=true` enables enforcement fleet-wide.
- No plaintext password or API key may be committed to the repo or written inline in a unit file.

## Redis Durability

- AOF is on with `appendfsync everysec`. At most one second of writes is lost on crash.
- `sensor:raw` and `stream:control` cap at 100,000 entries each (`Infrastructure/shared/redis_keys.py`).
- Legacy dual-write contract: current-value writers populate both `sensor:{name}` and `cea:sensor:{location}:{cluster}:{sensor_type}` until the short form is retired. Schedule-state writers keep `schedule:state:{location}:{cluster}`, `cea:schedule:{location}:{cluster}:state`, and `cea:schedule:state:{location}:{cluster}` aligned.
- New code must use the builders in `Infrastructure/shared/redis_keys.py`; do not introduce new top-level key namespaces without adding them here.

## Time Synchronization

`systemd-timesyncd` must be active and `NTPSynchronized=yes` at deploy preflight (`Infrastructure/scripts/verify_time.sh`). Drift must stay under 100 ms. CAGG bucket boundaries, schedule start/stop, and Redis TTL all depend on this.

## Deployment Safety

- `deploy.sh` and `rollback-deploy.sh` share an exclusive `flock(1)` on `/var/lock/projectcea-deploy.lock`.
- Deploys export an exact committed revision, run read-only lint, and record `release_id`, full `git_sha`, `alembic_heads`, `migration_head`, `previous_schema`, `deployed_at`, and `health_ok` in the release manifest. `previous_schema` preserves the SQL and Alembic heads from immediately before deploy for rollback to pre-Phase-2 releases.
- Migrations apply before unit/drop-in and Caddy synchronization. Downtime migrations require a recent passing rehearsal and create `/var/lib/projectcea/backups/pre-<RELEASE_ID>.dump`.
- Per-release systemd and Caddy backups are restored by automatic or manual rollback. Manual rollback refuses newer SQL schema unless `--with-down-migrations` is given; Alembic head mismatches require restoring the pre-deploy dump.

## Caddy Reverse Proxy

Caddy listens only on `:8080` (`auto_https off`). Repo-canonical config is `Infrastructure/caddy/Caddyfile`. Specific routing (first match wins):

- `/api/sensors`, `/api/sensors/*`, `/api/sensor-data`, `/api/sensor-data/*` → `127.0.0.1:8000`
- `/api/sensors/monitoring/*` and `/api/monitoring/control/*` (documented GET reads) → `127.0.0.1:8005`
- `/api/archives`, `/api/archives/*` → `127.0.0.1:8006`
- `/ws/*` → `127.0.0.1:8000`
- `/weather`, `/weather/*` → `127.0.0.1:8003`
- `/svc/soil/*` (path-stripped) → `127.0.0.1:8002`
- `/svc/onewire/*` (path-stripped) → `127.0.0.1:8004`
- `/ws` exactly → `127.0.0.1:8001`
- catch-all → `127.0.0.1:8001` (automation API + SPA static)

Direct ports 8000–8006 may still be reachable locally until a later hardening phase; clients should use `:8080`.

## Validation Surface

Local verification gates live in `ARCHITECTURE.md`. Runtime validation uses service `/health` and `/ready` endpoints, logs, and the read-only monitoring APIs (`monitoring-service :8005`). The repo retains focused local tests; production validation is not the only surface.
