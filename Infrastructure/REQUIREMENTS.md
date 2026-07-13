# Infrastructure — Requirements

Single growing record of cross-service infrastructure requirements for ProjectCEA. Edit in place as the system evolves; do not split into per-phase files.

## Runtime-only validation contract (Phase 8)

- Validation surface is live `/health` + `/ready`, `journalctl`, and operator
  Grafana dashboards.
- The repository no longer retains a test suite for ongoing operation. Deploy
  and rollback checks are runtime health checks, not pytest/vitest gates.
- Data exports for analytics/AI still require explicit UTC ISO timestamp output
  from `TIMESTAMPTZ` fields.

## Cluster topology contract (Phase 5e)

- Two cluster *types* exist and are kept **strictly separate**:
  - **Device cluster** — room-wide actuator namespace; always `main` today. Exposed by `/api/devices/{room}/{cluster}`, `/api/lights/...`, `/api/control/...`.
  - **Sensor sub-cluster** — *physically* distinct sensor group. Only `Flower Room` is split (`front` + `back`). `Veg Room`, `Lab`, `Outside` have **no sensor sub-clusters** in the topology data; their sensors live directly under the room.
- Naming rule: `main` is a **device-cluster name only** — never registered as a sensor sub-cluster. `front` / `back` are **sensor sub-cluster names only** — never appear on the device plane. Hierarchy: device `main` is the parent; sensor `front` / `back` are children of Flower's `main`.
- For unsplit rooms (Veg / Lab / Outside) the `/api/sensors/{room}/{cluster}` URL slot reuses `main` as a *room-wide sentinel* meaning "this room has no sub-grouping". This is a transport detail of the URL shape — `sensor_subclusters_for("Veg Room")` deliberately returns `()` so the namespace separation stays visible in code.
- **Single source of truth (Python services):** `Infrastructure/shared/cluster_topology.py`.
- **Single source of truth (frontend):** `Infrastructure/frontend/src/config/clusterTopology.ts`.
- The two files are intentionally tiny mirrors. Any change to room → cluster mapping touches both, plus `ProjectCEA/AGENTS.md` → "Cluster Topology Contract". CI does not enforce parity yet.
- API contract:
  - `GET /api/devices/{room}/{cluster}` — `cluster` MUST be the room's device cluster (`main`). Sub-cluster names (`front`, `back`) → **400** with a hint that they are sensor sub-clusters reachable via `/api/sensors/...`.
  - `GET /api/sensors/{room}/{cluster}` — for split rooms (Flower) `cluster` MUST be one of the sub-clusters (`front`/`back`); the device cluster (`main`) → **400** with a hint pointing at the correct sub-cluster. For unsplit rooms (Veg/Lab/Outside) `cluster` MUST be the device-cluster sentinel (`main`); anything else → **400**.
  - Unknown room name → **404** (`UnknownRoomError`).
- Frontend rule: poll `/api/devices/...` over `ZONES` (device-plane); poll `/api/sensors/...` over `getSensorPollZones()` (which iterates `sensorUrlClustersFor`); reserve `getDashboardPollZones()` for the bulk-Redis-key fan-out where both planes' keys are mixed.

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
- **Dual-write contract (live key consolidation):** Until superseded keys are retired, current-value writers MUST populate both the short form (`sensor:{name}` and `sensor:{name}:ts`) and the fully qualified form (`cea:sensor:{location}:{cluster}:{sensor_type}` and `cea:sensor:{location}:{cluster}:{sensor_type}_ts`) for every measurement the topology expects. Schedule state writers MUST keep all three in-use schedule-state keys aligned: `schedule:state:{location}:{cluster}`, `cea:schedule:{location}:{cluster}:state`, and `cea:schedule:state:{location}:{cluster}`. Use `Infrastructure/shared/redis_keys.py` builders; do not add new ad-hoc key literals.
- Configuration verified in `/etc/redis/redis.conf`. Do not regress without an explicit replacement durability story for `schedules:*`, `automation:degraded`, control-state keys.

## Degraded mode and reconnect behavior

- The automation control loop MUST publish `automation:degraded` when the loop
  has three consecutive runtime failures and MUST clear it only after ten
  consecutive successful ticks. The key is a JSON object with `active`,
  `reason`, `failure_count`, `success_count`, and `updated_at` so the frontend,
  journals, and Redis CLI all report the same state.
- Degraded mode is an observability state, not an automatic lights-off command.
  The existing failsafe/interlock layers remain responsible for crop-safety
  actuation. Do not hide repeated loop failures behind log-only retries.
- CAN bus reads MUST attempt to bring `can0` back up and recreate the socketcan
  `Bus` after interface-down or recv errors, using capped exponential backoff.
  A transient CAN flap should not require a manual service restart.
- Backend Redis Stream consumers MUST route unprocessable config events to
  `sensor:dlq` before acknowledging them, including the original stream,
  message id, payload, and error string.
- Async services must not call blocking sensor/hardware reads directly inside
  the event loop. Use `asyncio.to_thread()` for 1-Wire file reads and other
  synchronous drivers.

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

### Replication durability (Iskra Grafana)

- Iskra Grafana time-series reads the **streaming physical standby** (`projectcea_database`). If replication breaks, dashboards show stale or empty data while the Pi primary keeps ingesting CAN data.
- Mothernode MUST expose a physical replication slot named **`iskra_recovery`**. The standby MUST consume it: `Infrastructure/iskra_stack/.env` on iskra **requires** `REPLICATION_SLOT=iskra_recovery`; `docker-entrypoint-replica.sh` refuses an empty value and always writes `primary_slot_name` into `postgresql.auto.conf` after `pg_basebackup`.
- The replica entrypoint MUST force **`listen_addresses = *`** (via `postgres -c`) so sibling containers (`projectcea_grafana`, `projectcea_redis_sync`) can use TCP to `projectcea_database:5432`. A base backup alone often leaves `listen_addresses = localhost` in PGDATA, which passes in-container healthchecks but yields **connection refused** for cross-container queries (Grafana “db query error”).
- Primary WAL policy (mothernode `cea_sensors`): `wal_keep_size = 4GB`, `max_slot_wal_keep_size = 16GB`, `max_wal_senders = 5` (see `Infrastructure/database/REQUIREMENTS.md`). If Iskra is offline longer than the slot can retain WAL under `max_slot_wal_keep_size`, the slot is dropped and the replica must be **re-based** (runbook: `Infrastructure/iskra_stack/README.md` section *Recovery: standby fell behind / WAL removed*).
- Grafana on iskra includes a provisioned alert (`provisioning/alerting/replication_slot.yaml`) that queries the Pi primary via datasource **CEA Primary (ops)**; mothernode `pg_hba.conf` must allow `cea_user` from iskra’s Tailscale IP for that path.
- Pi → iskraprojectcea sync of `Infrastructure/iskra_stack/dashboards/` and `provisioning/` is via `Infrastructure/scripts/sync_to_iskra.sh` (rsync, with `--delete` safety guard refusing >3 deletions without `CONFIRM=1`).
- `Infrastructure/scripts/verify_iskra.sh` is the supported preflight for the
  replica/Grafana host. `deploy.sh` may run the sync/verify path when
  `DEPLOY_ISKRA=1`; normal Pi deploys leave the offsite stack untouched.

## Grafana topology (Phase 5 will change this)

Current state (Phase 5c complete, 2026-04-19):
- Production Grafana is the `projectcea_grafana` container on `iskraprojectcea`, pinned to `grafana/grafana:11.6.0` (see `Infrastructure/iskra_stack/docker-compose.yml`). Datasource = `projectcea_database:5432` (Docker network loopback to the local WAL replica).
- SPA embed URL is env-driven via `VITE_GRAFANA_BASE_URL` (default `http://iskraprojectcea:3001`; host port 3001 because the container's 3000 conflicted with another homelab service on that VM).
- Grafana Postgres datasource pools are capped in provisioning:
  `maxOpenConns=32`, `maxIdleConns=8`, `connMaxLifetime=300` on each
  Postgres datasource. The Iskra replica MUST start with
  `POSTGRES_MAX_CONNECTIONS=150` (see `Infrastructure/iskra_stack/`) so 1s
  full-dashboard fan-out has enough headroom on the server while Grafana still
  cannot consume every replica connection.
- **Same performance contract as before Phase 5c:** live “current value” panels
  should lean on **Redis** (`projectcea_redis`); time-series stays on the **local
  WAL replica** so the Pi primary is not hammered (`iskra_stack/README.md` first
  paragraph). That architecture is unchanged.
- **Hot standby vs live dashboards:** Grafana and `redis_sync` issue many
  concurrent `SELECT`s on the replica while WAL replay removes dead row versions.
  Without **`hot_standby_feedback=on`** on the standby, PostgreSQL cancels queries
  with **`canceling statement due to conflict with recovery`**; `redis_sync`
  then retries (keys age out / panels show stale **back** or **front** cluster
  values even though `replay_lag` is tiny). The Iskra entrypoint enables
  feedback by default (see `Infrastructure/iskra_stack/docker-entrypoint-replica.sh`).
  Trade-off: the primary may defer vacuum cleanup slightly — keep mothernode
  autovacuum healthy and watch table bloat if the replica is offline for long
  periods.
- Frontend Monitoring embeds (`/flower/monitoring`, `/vegetation/monitoring`)
  MUST keep `refresh=1s`; live monitoring cadence is non-negotiable. They MUST
  preserve Grafana's normal time-range controls and support multiple operator
  time windows; do not force a single bounded window from the frontend. Speed
  improvements must come from query fixes, aggregate-aware SQL, panel
  reduction, datasource tuning, or replica capacity, not from removing live
  refresh or time-range flexibility.
- Grafana cluster-value tables should be dense operator readouts. Hide redundant
  `Sensor` / `Value` table headers on compact current-value panels when the
  panel title already identifies the cluster/table context.
- Grafana operator dashboards and database sessions must use the grow room's
  Quebec local timezone (`America/Toronto`) for wall-clock display and local
  schedule processing. Dashboard `timezone` is fixed to `America/Toronto`,
  Grafana's default timezone is configured as `America/Toronto` for
  anonymous/server-rendered views, and the Iskra replica database MUST default
  sessions to `America/Toronto` so SQL-formatted text timestamps and
  `timestamptz::time` schedule comparisons match the Pi primary.
- Historical exports for analytics, AI training, backups, or cross-system
  processing MUST explicitly emit UTC ISO timestamps from `timestamptz` columns
  (for example `time AT TIME ZONE 'UTC'` formatted with a trailing `Z`). Do not
  rely on the current DB session timezone when exporting data.
- Frontend light status badges are relay indicators. The adjacent intensity
  readout is separate dimmer telemetry and must not be used to reinterpret relay
  ON/OFF. In drying mode, scheduled light authority must force the light relays
  OFF and DFR0971 intensities to 0% so relay badges show moon/off and dimmer
  telemetry shows 0%; manual light controls remain available for explicit
  operator action. In constant modes that expose manual light controls
  (`drying`, `sleep`), render those controls in ZoneConfig's left light-control
  column where the circular photoperiod picker appears for scheduled modes,
  using the same card frame/title/flex sizing format as the circular picker.
- Unified alerting rules + the `Tony` email contact point + the Gmail SMTP relay were migrated from the Pi `grafana-server` to `projectcea_grafana` on 2026-04-19. JSON exports of the pre-migration Pi state are preserved under `Infrastructure/frontend/grafana/pi-decommission-backup-*/` (gitignored; the SMTP app password is in that folder at mode 0600).
- Sensor missing-data alert rules must distinguish missing samples from datasource
  execution failures. Keep `noDataState=NoData` so genuinely absent telemetry
  still emails, but set `execErrState=KeepLastState` so transient Grafana
  datasource/database/auth errors do not email as if a connected sensor cluster
  stopped reporting. Datasource health should be monitored separately by the
  operations dashboard/alerts.
- Pi `grafana-server` is **PERMANENTLY decommissioned**. Must NEVER be re-enabled. Package may be purged.
- The legacy `iskradocker` CEA datasource + bind-mount were removed earlier in Phase 5c; backup tarball at `/var/lib/projectcea/backups/iskradocker-cea-grafana-pre-refactor.tgz`.

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
| grafana-server | _decommissioned_ | _n/a_ | Pi Grafana — **PERMANENTLY decommissioned — DO NOT re-enable** since Phase 5c (2026-04-19). Production Grafana now runs as `projectcea_grafana` on `iskraprojectcea:3001` (Docker container; see `Infrastructure/iskra_stack/docker-compose.yml`). |

## Security posture (Phase 3 complete)

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
  - _(no local `/grafana/*` proxy — removed Phase 5c: the SPA embeds `projectcea_grafana` directly via `VITE_GRAFANA_BASE_URL` = `http://iskraprojectcea:3001`)_
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

Landed in Phase 3.4c/3.4d (API-key enforcement + localhost bind):
- **Protection surface is deny-by-default.** The API-key middleware (`shared.auth.APIKeyAuthMiddleware`) now protects *every* HTTP path except an allow-list: `/`, `/health`, `/ready`, `/status`, `/ws*` (auth is enforced inside the WebSocket handler), `/docs`, `/redoc`, `/openapi.json`, `/logo.png`, `/favicon.*`, `/assets/*`, `/static/*`. Boundary logic requires exact match, `/` separator, or `.` filename-suffix — `/healthz` / `/wsfoo` are therefore still protected. Flipped from the Phase 3.1 model that only gated `/api/*`, which was letting weather-service's `/weather/*` routes bypass enforcement.
- **One shared key across the fleet**: `/opt/projectcea/shared/env/api_key.env` (mode 0600 antoine:antoine) holds `CEA_API_KEY` + `CEA_API_KEY_REQUIRE=true`. Every runtime service base unit has `EnvironmentFile=-/opt/projectcea/shared/env/api_key.env`, so enforcement is uniform.
- **SPA wiring**: `Infrastructure/frontend/.env.production` (gitignored) mirrors the key as `VITE_CEA_API_KEY`. `npm run build` bakes it into exactly 1 chunk and every axios client sends `X-API-Key`. WebSocket URL gets `?token=<key>` appended. Verified: no trace of `VITE_CEA_API_KEY` in built bundle (Vite substituted at build time); key never appears in any journal line (secret redaction covers it).
- **Localhost binding (3.4d)**: every uvicorn process now runs with `--host 127.0.0.1` (`automation-service`, `cea-backend`, `onewire-worker`, `soil-sensor-service`, `weather-service`). Direct LAN access to :8000–:8004 is closed; Caddy on `*:8080` is the single LAN/Tailscale-reachable entrypoint. The `can-processor` daemon is already a non-listening writer, so unchanged.
- **Key rotation**: `printf 'CEA_API_KEY=%s\nCEA_API_KEY_REQUIRE=true\n' "$NEW" | sudo -u antoine tee /opt/projectcea/shared/env/api_key.env.tmp >/dev/null && sudo -u antoine mv /opt/projectcea/shared/env/api_key.env.tmp /opt/projectcea/shared/env/api_key.env && sudo chmod 0600 /opt/projectcea/shared/env/api_key.env` → update `frontend/.env.production` with the same value → `./deploy.sh` (rebuilds SPA with the new key) → `sudo systemctl restart <all services>`.
- **Rollback**: remove the `CEA_API_KEY_REQUIRE` line from `api_key.env` and restart; enforcement disables fleet-wide without a code redeploy. To also re-open the LAN boundary: revert each drop-in's `--host 127.0.0.1` back to `--host 0.0.0.0` — backups in `/var/lib/projectcea/systemd-backup/20260418T132354Z/dropins/pre-34d/`.

Remaining cleanup:
- Retire the `POSTGRES_PASSWORD` env-var fallback after the `LoadCredential=`
  path has soaked in production; drop `EnvironmentFile=-postgres.env` from every
  DB-using base unit and scrub `POSTGRES_PASSWORD=` from `postgres.env` itself.
  This is cleanup only: the authoritative Phase 3.8 runtime path is already
  `$CREDENTIALS_DIRECTORY/postgres_password` via
  `shared.db_credentials.load_postgres_password()`.

## Frontend port / origin map

- SPA dev server: `http://mothernode:5173`
- API base (cea-backend): `http://mothernode:8000`
- Automation service direct: `http://mothernode:8001`
- Grafana embed: `http://iskraprojectcea:3001` (canonical since Phase 5c, 2026-04-19). Previous stops on the migration path — `iskradocker:3000` and the Pi-local `grafana-server:3000` — are both fully decommissioned.
