# Infrastructure Requirements

Stable cross-service contracts for ProjectCEA. For current architecture, see `ARCHITECTURE.md`. For service-specific behavior, see the `REQUIREMENTS.md` under each service directory.

## Service Enumeration Authority

`Infrastructure/services.yaml` is the single source of truth for every managed systemd unit. It defines unit filename, repo path, start order, health URL, and hardware-facing flag. `Infrastructure/scripts/service_list.py` parses it; `deploy.sh`, `rollback-deploy.sh`, and `sync_systemd_units.sh` consume it. No script should hardcode a service list.

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

`systemd-timesyncd` must be active and `NTPSynchronized=yes` at deploy preflight (`Infrastructure/scripts/verify_time.sh`). Drift must stay under 100 ms. CAGG bucket boundaries, schedule start/stop, replication timestamps, and Redis TTL all depend on this.

## Deployment Safety

- `deploy.sh` and `rollback-deploy.sh` share an exclusive `flock(1)` on `/var/lib/projectcea-deploy.lock`.
- `deploy.sh` writes `/var/lib/projectcea/deploy_state.json` after a successful deploy; `rollback-deploy.sh` uses `rollback_to_path` from that file.
- `deploy.sh` writes `/opt/projectcea/current/deploy_manifest.json` with `release_id`, `git_sha`, `deployed_at`, and `health_ok`. The current deployed state is external; this repo describes the source surface, not a mutable snapshot.
- `sync_systemd_units.sh` is intentionally not wired into `deploy.sh`. Auto-rollback reverts the code symlink only; a bad unit file left in `/etc/systemd/system/` would survive rollback.

## Caddy Reverse Proxy

Caddy listens only on `:8080` (`auto_https off`). Repo-canonical config is `Infrastructure/caddy/Caddyfile`. Specific routing (first match wins):

- `/api/sensors`, `/api/sensors/*`, `/api/sensor-data`, `/api/sensor-data/*` → `127.0.0.1:8000`
- `/ws/*` → `127.0.0.1:8000`
- `/weather`, `/weather/*` → `127.0.0.1:8003`
- `/svc/soil/*` (path-stripped) → `127.0.0.1:8002`
- `/svc/onewire/*` (path-stripped) → `127.0.0.1:8004`
- `/ws` exactly → `127.0.0.1:8001`
- catch-all → `127.0.0.1:8001` (automation API + SPA static)

Direct ports 8000–8004 may still be reachable locally until a later hardening phase; clients should use `:8080`.

## Iskra Stack Ownership

Production Grafana runs in container `projectcea_grafana` on `iskraprojectcea:3001`. It reads the local streaming standby `projectcea_database` (TimescaleDB replica) for time-series and Redis (via `redis_sync`) for current-value panels.

- Provisioning and dashboards are mounted read-only from `Infrastructure/iskra_stack/provisioning/` and `Infrastructure/iskra_stack/dashboards/`.
- Sync from Pi to Iskra uses `Infrastructure/scripts/sync_to_iskra.sh`.
- Preflight health check uses `Infrastructure/scripts/verify_iskra.sh`.
- Replica recovery and any destructive re-base are operator-only actions. They are not performed by agents or automated verification.

## Validation Surface

Local verification gates live in `ARCHITECTURE.md`. Runtime validation uses service `/health` and `/ready` endpoints, logs, and Grafana. The repo retains focused local tests; production validation is not the only surface.
