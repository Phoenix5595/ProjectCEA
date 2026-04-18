# Infrastructure — Requirements

Single growing record of cross-service infrastructure requirements for ProjectCEA. Edit in place as the system evolves; do not split into per-phase files.

## Identity & secrets

- The single DB user for application services is `cea_user` on database `cea_sensors`.
- The `POSTGRES_PASSWORD` for `cea_user` lives in exactly one place at runtime: `/opt/projectcea/shared/env/postgres.env` (mode `0600`, owner `antoine:antoine` until Phase 3.8 migrates to `LoadCredential=`).
- All systemd services that connect to Postgres MUST source this file via `EnvironmentFile=-/opt/projectcea/shared/env/postgres.env` — never inline `Environment="POSTGRES_PASSWORD=..."` in any unit file. Repository contains no plaintext DB passwords.
- Bootstrap script `setup_timescaledb.sh` requires `CEA_USER_PASSWORD` env var at install time — no plaintext default.
- Rotation procedure (Phase 1.1b precedent, ~30 sec window):
  1. `ALTER USER cea_user WITH PASSWORD '<new>';` (existing pool conns survive).
  2. `echo "POSTGRES_PASSWORD=<new>" | sudo install -m 0600 -o antoine -g antoine /dev/stdin /opt/projectcea/shared/env/postgres.env`
  3. `sudo systemctl restart automation-service cea-backend soil-sensor-service weather-service onewire-worker can-processor`
  4. Verify: services `active`, no `password authentication failed` in journals, fresh data flowing in `measurement` table, `redis-cli ping` PONG.

## Redis durability

- AOF on, `appendfsync everysec`. Max 1 second of writes lost on crash.
- Configuration verified in `/etc/redis/redis.conf`. Do not regress without an explicit replacement durability story for `schedules:*`, `automation:degraded`, control-state keys.

## Time synchronization

- `systemd-timesyncd` active; `NTPSynchronized=yes` is required at deploy preflight (`Infrastructure/scripts/verify_time.sh`).
- Drift budget: < 100 ms. CAGG bucket boundaries, schedule start/stop, replication timestamps, Redis TTL all rely on this.

## Deploy concurrency

- `deploy.sh` and `rollback-deploy.sh` share an exclusive `flock(1)` on `/var/lock/projectcea-deploy.lock`. A deploy and a rollback cannot run simultaneously; a second deploy attempt while one is in flight aborts immediately.

## Systemd unit topology

- Base units in `Infrastructure/<name>.service` are the canonical source of truth for every CEA systemd unit. They now encode the full merged behaviour (drop-in + base) that was captured in Phase 0 and landed in Phase 2.1a, so a fresh Pi can be reproduced from the repo alone: `git clone` → `deploy.sh` → `Infrastructure/scripts/sync_systemd_units.sh`.
- `Infrastructure/services.yaml` is the single source of truth enumerating every service (name, unit filename, `repo_unit` path, `start_order`, `health_url`, `hardware_facing` flag, optional `deploy_managed: false`). `Infrastructure/scripts/service_list.py` parses it and is the only supported way for other scripts (deploy.sh, rollback-deploy.sh, sync_systemd_units.sh) to enumerate units.
- Runtime drop-ins under `/etc/systemd/system/<unit>.service.d/override.conf` (mirrored for audit under `Infrastructure/systemd-overrides/`) are still in force on the live Pi and override the base on a per-directive basis; they will be retired in Phase 2.1c (one service per deploy, automation-service last to keep lights-blast-radius minimal). Until then, `sync_systemd_units.sh` writing a new base unit is a runtime no-op — systemd merges them and drop-in values win for single-value keys.
- All hardware-facing app services run as `User=root` (GPIO / I2C / 1-Wire / RS485 / CAN) — flagged for Phase 3 hardening (non-root user + device capabilities).
- `WorkingDirectory` is always `/opt/projectcea/current/Infrastructure/<svc>` at runtime; `.venv/bin/python` is the only Python interpreter used by services.
- NEVER write `Environment="POSTGRES_PASSWORD=..."` inline in a base unit file. Even if the drop-in neutralizes it with `Environment=\n` reset, the on-disk unit is world-readable (`/etc/systemd/system/*.service` is mode `0644` by default) and the literal leaks to any account with shell access. The Phase 2.1a rewrite removed the last remaining literal (stale pre-rotation value in soil-sensor-service) and this rule is enforced by the repo unit files.

### Sync procedure

`sudo Infrastructure/scripts/sync_systemd_units.sh --dry-run` shows per-unit diffs. Running without `--dry-run` interactively confirms, then backs each changed file up to `/var/lib/projectcea/systemd-backup/<UTC-timestamp>/` and installs the repo version; `--reenable` additionally reenables every changed unit so `[Install]`-section changes (WantedBy/RequiredBy) take effect on boot. Rollback prints the rsync command against the backup dir. NOT wired into `deploy.sh` on purpose: auto-rollback only reverts the code symlink, and a bad unit file persisting in `/etc/systemd/system/` after a rollback would be a foot-gun.

## Offsite / replication

- Streaming replication target: `iskraprojectcea` (Tailscale `100.72.106.76`). Container `projectcea_database` (`timescale/timescaledb:2.23.1-pg15` — pin in Phase 0.5).
- Pi PG is `15.16` + TSDB `2.23.1`. The replica MUST match these versions or replication breaks; preflight `verify_iskra.sh` enforces this.
- `pg_stat_replication.replay_lag` healthy band: `< 1s` steady-state, `< 5s` transient. Anything sustained `> 30s` blocks Phase 5 DDL.
- Pi → iskraprojectcea sync of `Infrastructure/iskra_stack/dashboards/` and `provisioning/` is via `Infrastructure/scripts/sync_to_iskra.sh` (rsync, with `--delete` safety guard refusing >3 deletions without `CONFIRM=1`).

## Grafana topology (Phase 5 will change this)

Current state (pre-Phase-5):
- Pi `grafana-server` (v13.0.0) is installed but the operator does not use it (latency on raw `measurement` table is too high).
- Production Grafana is the `grafana` container on `iskradocker` (v12.3.2), embedded by the SPA's Monitoring tabs via the literal URL `http://iskradocker:3000` in `Infrastructure/frontend/src/components/GrafanaPanel.tsx`. Datasource points at `192.168.1.74:5432` (Pi LAN). Dashboard tree on iskradocker last synced Feb 2 — no automation.

Post-Phase-5b state:
- Production Grafana moves to `iskraprojectcea` (`projectcea_grafana` container in `~/docker-compose-projectcea-new.yml`). Datasource = `projectcea_database:5432` (Docker network loopback to local replica). Version pinned to match Pi.
- SPA URL is env-driven: `VITE_GRAFANA_EMBED_BASE_URL` (default `http://iskraprojectcea:3000`).
- Pi `grafana-server` is `inactive`/`disabled` (kept installed as escape hatch).
- iskradocker CEA datasource + bind-mount removed; backup tarball at `/var/lib/projectcea/backups/iskradocker-cea-grafana-pre-refactor.tgz`.

## Backups

Location: `/var/lib/projectcea/backups/` (mode `0750`, owner `antoine`).

Standing artifacts (never auto-pruned during the refactor campaign):
- `pre-refactor-<DATE>.dump` — `pg_dump -F c` of `cea_sensors`. ~836 MB.
- `dump-<DATE>.rdb` — Redis RDB snapshot.
- `appendonlydir-<DATE>/` — Redis AOF directory copy.
- `configs-<DATE>/*.yaml` — automation/soil/weather/onewire YAML configs.
- `deploy_manifest-<DATE>.json` — known-good rollback target identifier.
- `postgres.env.pre-rotate-<TIMESTAMP>` — every DB password rotation drops a backup of the previous env file (manual cleanup after 30 days post-rotation).
- `iskradocker-cea-grafana-pre-refactor.tgz` (created in Phase 0.5 before Phase 5c) — restores legacy iskradocker CEA Grafana state if needed.

## Service inventory (Phase 2.2 will canonicalize this)

Services that participate in deploy + rollback today:
| Service | Unit | Port | Notes |
|---|---|---|---|
| can-processor | `can-processor.service` | n/a | CAN bus → Redis stream + DB writer |
| cea-backend | `cea-backend.service` | 8000 | FastAPI; SPA backend; WS broadcaster |
| automation-service | `automation-service.service` | 8001 | Control loop; PID; schedules |
| soil-sensor-service | `soil-sensor-service.service` | 8002 | Modbus → Redis + DB |
| weather-service | `weather-service.service` | 8004 | External API → Redis + DB |
| onewire-worker | `onewire-worker.service` | n/a | 1-wire temperatures → Redis (no HTTP) |
| grafana-server | `grafana-server.service` | 3000 | Pi Grafana — disabled in Phase 5c |

## Security posture (Phase 3, in-progress)

Landed in Phase 3.3 / 3.5 / 3.6:
- **Secret redaction in logs**: `shared/infra_logging.SecretRedactionFilter` is auto-attached to every handler configured through `setup_structured_logging()`. It scrubs URL userinfo, `POSTGRES_PASSWORD=`, `X-API-Key:`, `Authorization: Bearer`, `token=`, `api_key=` *after* message rendering and *before* the formatter, so both JSON and console output are clean. Idempotent. False-positive risk intentionally > false-negative risk.
- **OpenAPI surface**: `shared/fastapi_helpers.docs_kwargs()` closes `/docs`, `/redoc`, `/openapi.json` on every service when `ENV=production` is set in the process environment. Default (ENV unset) = docs reachable. Single operator flag; no code redeploy needed to flip.
- **CORS**: `shared/middleware.setup_cors()` replaces per-service blocks. If `FRONTEND_ORIGINS=<comma-list>` is set, runs the explicit allow-list + credentials=True; else falls back to `allow_origins=["*"]` with credentials=False (browser-spec-correct). The unsafe `*`+`credentials=True` pair shipped by three services was silently rejected by browsers and is now gone.

Operator-controlled knobs (both reversible without a code deploy):
- `ENV=production` in each service's env → close `/docs` et al. Set via systemd drop-in `Environment=ENV=production` or (preferred) an `EnvironmentFile=/opt/projectcea/shared/env/cea.env` once the shared env file exists.
- `FRONTEND_ORIGINS=http://mothernode:5173,http://mothernode:8080` → locked-down CORS with cookie/credential support. Required the day the SPA moves behind Caddy (Phase 3.4b).

Landed in Phase 3.1 / 3.2 (gated, default off):
- **API-key enforcement**: `shared/auth.APIKeyAuthMiddleware` is installed on all five FastAPI apps (cea-backend, automation-service, soil-sensor-service, weather-service, onewire-worker). Enforcement is gated by `CEA_API_KEY_REQUIRE=true`; the middleware is a no-op otherwise. When enforcement is on, any HTTP request whose path matches the protected prefixes (`/api`, `/weather`, `/status` etc — see `_is_protected_path`) must carry `X-API-Key: <CEA_API_KEY>`; `OPTIONS` preflights, `/health`, `/ready`, `/docs`, `/openapi.json`, `/redoc`, `/ws` are always exempt. Comparison uses `hmac.compare_digest`. Missing `CEA_API_KEY` while enforcement is on returns 503 (explicit misconfig, not silent pass-through).
- **WebSocket auth + origin check**: `shared/auth.check_websocket_auth` accepts `?token=<key>` (hmac-equal) or a matching `Origin` in `FRONTEND_ORIGINS`; otherwise closes with 1008. Integrated into `/ws` (automation-service) and `/ws/{location}` (cea-backend). Same `CEA_API_KEY_REQUIRE` gate.
- **WebSocket connection cap**: `shared/auth.WebSocketConnectionLimiter` closes excess connections with 1013. `CEA_WS_MAX_CONNECTIONS` env (default 100) sets the per-process ceiling.

Landed in Phase 3.4a (additive, no behavior change):
- **Caddy reverse proxy**: `caddy` v2.11.x installed from the official Cloudsmith repo, enabled at boot. Repo-canonical config at `Infrastructure/caddy/Caddyfile` is installed to `/etc/caddy/Caddyfile`. Listens **only on :8080** for now; :80 and :443 remain closed (`auto_https off`). Log writer is `/var/log/caddy/caddy.log` (owner `caddy:caddy`, rotated at 10 MB × 5 files). Admin API disabled.
- **Routing rules** (specific-first):
  - `/api/sensors`, `/api/sensors/*`, `/api/sensor-data`, `/api/sensor-data/*` → `127.0.0.1:8000` (cea-backend)
  - `/ws/*` (any trailing path segment) → `127.0.0.1:8000` (backend per-location WS)
  - `/weather`, `/weather/*` → `127.0.0.1:8003` (weather-service)
  - `/grafana/*` (path-stripped) → `127.0.0.1:3000` (Pi Grafana; currently unused — see Phase 5)
  - `/svc/soil/*`, `/svc/onewire/*` (path-stripped) → `127.0.0.1:8002` / `:8004`
  - `/ws` exactly → `127.0.0.1:8001` (automation-service WS)
  - catch-all → `127.0.0.1:8001` (automation-service API + SPA static)
- **Validation** (done 3.4a): HTTP GET/POST to all three API ports, SPA static at `/`, and WebSocket `Upgrade: 101` on both `/ws` and `/ws/<location>` — all 200/101 through Caddy, bodies byte-matched direct vs proxied.
- **Invariants**: direct ports 8000/8001/8002/8003/8004 remain open until Phase 3.4d; SPA still hits them directly until Phase 3.4b; auto-HTTPS stays off until TLS is actually needed. WebSocket upgrades pass through unmodified (Caddy preserves `Connection: Upgrade`).

Landed in Phase 3.4b (frontend moves behind Caddy):
- **Central endpoint config**: `Infrastructure/frontend/src/config/env.ts` is the single source of truth for `BACKEND_API_URL`, `AUTOMATION_API_URL`, `WEATHER_API_URL`, `buildWebSocketUrl()`, and `CEA_API_KEY`. By default all three base URLs resolve to the Caddy entrypoint — `window.location.origin` when the SPA is already served from :8080 (same-origin, no CORS), or `http://<hostname>:8080` when served from anywhere else (legacy :8001 access path).
- **Per-service escape hatches preserved**: `VITE_BACKEND_API_URL`, `VITE_AUTOMATION_API_URL`, `VITE_WEATHER_API_URL`, `VITE_WEBSOCKET_URL` still win if set, so an operator can peel one client back to a direct port without a full rebuild.
- **X-API-Key wiring**: If `VITE_CEA_API_KEY` is non-empty at build time, every axios client sends `X-API-Key: <key>` and the WebSocket URL gets `?token=<key>` appended. Server-side enforcement stays gated by `CEA_API_KEY_REQUIRE=true`; the header is inert until 3.4c flips the gate.
- **No hardcoded ports remain in the built bundle** (verified by grepping `dist/assets/*.js`; only appearance is the SSR fallback `ws://localhost:8080/ws`).

Landed in Phase 3.7 (systemd hardening, additive drop-ins):
- **Seven exploit-containment directives** applied to every runtime service base unit (`automation-service`, `cea-backend`, `can-processor`, `onewire-worker`, `soil-sensor-service`, `weather-service`): `NoNewPrivileges=yes`, `LockPersonality=yes`, `RestrictSUIDSGID=yes`, `RestrictRealtime=yes`, `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectControlGroups=yes`. Services still run as root (required for direct hardware access); hardening narrows the *kernel* attack surface reachable from that root without touching hardware paths.
- **Drop-in cleanup**: the `NoNewPrivileges=false` line was removed from every `Infrastructure/systemd-overrides/*/override.conf` drop-in because it was explicitly neutralising the new base-unit setting. The other leftover `=false` knobs in the drop-ins (`PrivateTmp`, `ProtectSystem`, `ProtectHome`) were left in place — they match systemd's system-service defaults for these units, so removing them would be a no-op.
- **Validation**: `systemctl show -p NoNewPrivileges,LockPersonality,...` confirms all seven directives merge to `yes`; `systemd-analyze security` marks all seven ✓; no `permission denied` / `operation not permitted` / `EPERM` / `seccomp` in journals after a rolling restart; automation-service lights-restore path verified (`Batch execution complete: N success, 0 failures`).

Landed in Phase 3.8 (Postgres password via `LoadCredential=`):
- **Runtime secret flow**: `/opt/projectcea/shared/env/postgres_password` (mode `0600`, owner `antoine:antoine`, parent dir `0700`) is the single on-disk source. Every service base unit has `LoadCredential=postgres_password:/opt/projectcea/shared/env/postgres_password`; systemd copies it into the per-unit credential directory (`$CREDENTIALS_DIRECTORY`, mode `0400`, owned root) before handing control to the service.
- **App-side loader**: `shared.db_credentials.load_postgres_password()` is the single authoritative path for fetching the password. Priority: (1) `$CREDENTIALS_DIRECTORY/postgres_password`; (2) fallback to the `POSTGRES_PASSWORD` env var (loaded via `EnvironmentFile=-postgres.env`, kept for safety in case the credential file is missing); (3) raise `RuntimeError` if neither is populated.
- **Call sites migrated** (6 total): `backend/app/database.py`, `automation-service/app/database.py`, `automation-service/alembic/env.py`, `weather-service/app/database.py`, `soil-sensor-service/app/database.py`, `can-processor-service/app/writer.py`. All now call `load_postgres_password()` instead of `os.getenv("POSTGRES_PASSWORD")`.
- **Security benefit**: The password is no longer exposed via `systemctl show <unit> -p Environment` nor `/proc/<pid>/environ` once the env-var fallback is eventually retired. Rotation stays at a single file write plus `systemctl daemon-reload && restart`.
- **Rotation** (supersedes the Phase 1.1b procedure when the env-var fallback is retired): `sudo -u antoine bash -c 'printf "%s" "$NEW" > /opt/projectcea/shared/env/postgres_password.tmp && mv /opt/projectcea/shared/env/postgres_password.tmp /opt/projectcea/shared/env/postgres_password && chmod 0600 /opt/projectcea/shared/env/postgres_password'` → rotate the DB user → `systemctl daemon-reload && systemctl restart <units>`.

Still deferred (phase 3 remainder):
- 3.4c Flip `CEA_API_KEY_REQUIRE=true`; canary on weather-service first.
- 3.4d Bind services to `127.0.0.1`; close LAN boundary.
- Retire the `POSTGRES_PASSWORD` env-var fallback once 3.8 has soaked in production; drop the `EnvironmentFile=-postgres.env` line from every base unit and scrub the `POSTGRES_PASSWORD=` line from `postgres.env` itself (keep the file for other future keys).

## Frontend port / origin map

- SPA dev server: `http://mothernode:5173`
- API base (cea-backend): `http://mothernode:8000`
- Automation service direct: `http://mothernode:8001`
- Grafana embed: `http://iskradocker:3000` today → `http://iskraprojectcea:3000` post-5b → Caddy `/grafana` post-3.4 (when terminating TLS).
