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

## Frontend port / origin map

- SPA dev server: `http://mothernode:5173`
- API base (cea-backend): `http://mothernode:8000`
- Automation service direct: `http://mothernode:8001`
- Grafana embed: `http://iskradocker:3000` today → `http://iskraprojectcea:3000` post-5b → Caddy `/grafana` post-3.4 (when terminating TLS).
