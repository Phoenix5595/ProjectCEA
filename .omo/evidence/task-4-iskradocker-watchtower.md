# Task 4 Evidence: AGENTS.md Watchtower Documentation Update

## Verification Results

- watchtower in /home/antoine/docker/AGENTS.md: PASS
- watchtower in /home/antoine/docker/compose/AGENTS.md: PASS
- immich_postgres exclusion in compose/AGENTS.md: PASS
- rollback in /home/antoine/docker/AGENTS.md: PASS

---

## /home/antoine/docker/AGENTS.md (full content)

# Docker – Agent Context

## Conventions

| Convention | Value |
|------------|-------|
| Compose location | `~/docker/compose/` |
| Config persistence | `/srv/config/<service>/` |
| Media storage | `/srv/storage1/`, `/srv/storage2/` |
| Image preference | LinuxServer.io when available |
| User/Group | PUID=1000, PGID=1000 |

## Environment

All compose files use `~/docker/.env` for shared variables. Do not commit secrets; document where B2/Kopia credentials live (e.g. .env or a secrets file).

## Compose Layout

| File | Purpose | Services |
|------|---------|----------|
| media.yml | Media stack (Jellyfin-related) | Jellyfin, *arr, qBit, Jellyseerr |
| nextcloud.yml | Nextcloud | app, web, db, redis, cron |
| backup.yml | Kopia server | kopia-server (51515) |
| infra.yml | Infrastructure | Portainer, Homepage |
| monitoring.yml | Observability | Prometheus, Grafana, Uptime Kuma |
| photos.yml | Photo management | Immich |
| books.yml | E-books | Calibre-web |
| proxy.yml | CLI Proxy API | eceasy/cli-proxy-api |
| sync.yml | File sync | Syncthing |

**Kopia (backup.yml)**: Server connects to repo at startup. Client scripts: setup-kopia-client.sh (Linux), setup-kopia-client-windows.ps1 (Windows), setup-kopia-iskra-self.sh (iskra self). B2 offsite: see [docs/KOPIA-B2.md](docs/KOPIA-B2.md); credentials in .env or secrets file.

Stacks can be run via CLI (`docker compose -f <file>.yml up -d`) or deployed as Portainer stacks. The **media** stack is deployed as a Portainer stack (single source for Jellyfin-related containers).

## Portainer

Portainer runs on iskra:9000. Use it to create and manage stacks (e.g. paste compose content, bind env file or set env vars). When adding a stack, use the same paths as in the compose files (CONFIG_ROOT, STORAGE1, STORAGE2).

## Useful Commands

```bash
docker compose -f compose/<file>.yml up -d
docker compose -f compose/<file>.yml logs -f <service>
docker compose -f compose/<file>.yml down
```

## Documentation

- Full service list and ports: [docs/SERVICES.md](docs/SERVICES.md)
- Architecture and stacks: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Nextcloud:** Setup and troubleshooting: [NEXTCLOUD-SETUP.md](NEXTCLOUD-SETUP.md)
- **Grafana "No data":** Brief for docker-host agent: [GRAFANA-NO-DATA-BRIEF.md](GRAFANA-NO-DATA-BRIEF.md) (homelab job labels vs Prometheus scrape config; CEA PostgreSQL connectivity).
- Add or change services: document in SERVICES.md and update REQUIREMENTS.md when relevant.

## Auto-Update System (Watchtower)

Watchtower manages automatic container image updates on this host.

### How it works

- **Watchtower container**:  runs with .
- **Check frequency**: Every hour (3600 seconds).
- **Label-based**: Only containers with the label  are monitored.
- **Cleanup**: Old images are removed after a successful update (). Rollback is performed by pulling the previous version tag from Docker Hub.

### Auto-updated containers (labeled)

These containers will automatically update when a new image is published:

| Service | Compose file | Notes |
|---------|-------------|-------|
| nextcloud-app | nextcloud.yml | |
| nextcloud-web | nextcloud.yml | |
| nextcloud-redis | nextcloud.yml | |
| nextcloud-cron | nextcloud.yml | |
| jellyfin | media.yml | |
| immich-server | photos.yml | Only after VectorChord migration |
| immich-machine-learning | photos.yml | Only after VectorChord migration |
| immich-redis | photos.yml | Only after VectorChord migration |

### Manual-only containers (NEVER auto-updated)

These containers MUST NOT have the Watchtower label. They are updated manually, with runbook-governed procedures:

| Service | Compose file | Reason |
|---------|-------------|--------|
| nextcloud-db | nextcloud.yml | Database — manual update only |
| immich-postgres | photos.yml | Database — manual update only. Requires runbook-governed migration (see below). |

**DB exclusion rule**: MUST NOT auto-update  or . Both are manual-only, runbook-governed.

### Rollback procedure

If an update breaks a service:

1. Identify the previous working image tag (from Docker Hub or local history).
2. Pull the previous version: 
3. Update the compose file to pin that tag (or use  locally).
4. Recreate the container: 
5. Verify health (see service-specific health checks in docs).

### Immich migration runbook

The Immich database migration (pgvecto.rs → VectorChord) is documented at:


This migration MUST be completed before the Immich containers can safely auto-update. Auto-updating Immich server against a pgvecto.rs database will brick the installation.

### Monitoring updates

Check Watchtower logs for update activity:



## Auto-Update System (Watchtower)

Watchtower manages automatic container image updates on this host.

### How it works

- **Watchtower container**: `compose-watchtower-1` runs with `--interval 3600 --label-enable --cleanup`.
- **Check frequency**: Every hour (3600 seconds).
- **Label-based**: Only containers with the label `com.centurylinklabs.watchtower.enable=true` are monitored.
- **Cleanup**: Old images are removed after a successful update (`--cleanup`). Rollback is performed by pulling the previous version tag from Docker Hub.

### Auto-updated containers (labeled)

These containers will automatically update when a new image is published:

| Service | Compose file | Notes |
|---------|-------------|-------|
| nextcloud-app | nextcloud.yml | |
| nextcloud-web | nextcloud.yml | |
| nextcloud-redis | nextcloud.yml | |
| nextcloud-cron | nextcloud.yml | |
| jellyfin | media.yml | |
| immich-server | photos.yml | Only after VectorChord migration |
| immich-machine-learning | photos.yml | Only after VectorChord migration |
| immich-redis | photos.yml | Only after VectorChord migration |

### Manual-only containers (NEVER auto-updated)

These containers MUST NOT have the Watchtower label. They are updated manually, with runbook-governed procedures:

| Service | Compose file | Reason |
|---------|-------------|--------|
| nextcloud-db | nextcloud.yml | Database -- manual update only |
| immich-postgres | photos.yml | Database -- manual update only. Requires runbook-governed migration (see below). |

**DB exclusion rule**: MUST NOT auto-update `immich_postgres` or `nextcloud-db`. Both are manual-only, runbook-governed.

### Rollback procedure

If an update breaks a service:

1. Identify the previous working image tag (from Docker Hub or local history).
2. Pull the previous version: `docker pull <image>:<previous-version>`
3. Update the compose file to pin that tag (or use `docker tag` locally).
4. Recreate the container: `docker compose --env-file ~/docker/.env -f <file>.yml up -d <service>`
5. Verify health (see service-specific health checks in docs).

### Immich migration runbook

The Immich database migration (pgvecto.rs -> VectorChord) is documented at:
`compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`

This migration MUST be completed before the Immich containers can safely auto-update. Auto-updating Immich server against a pgvecto.rs database will brick the installation.

### Monitoring updates

Check Watchtower logs for update activity:

```bash
docker logs compose-watchtower-1 --tail 50
```

---

## /home/antoine/docker/compose/AGENTS.md (full content)

# Compose Files – Agent Context

## Stack List

| File | Services | Purpose |
|------|----------|---------|
| media.yml | Jellyfin, Sonarr, Radarr, Prowlarr, qBittorrent, Bazarr, Jellyseerr | Media acquisition and streaming (Jellyfin-related) |
| nextcloud.yml | nextcloud-app, nextcloud-web, nextcloud-db, nextcloud-redis, nextcloud-cron | Nextcloud |
| backup.yml | kopia-server, kopia-maintenance (profile) | Kopia backup server |
| infra.yml | Portainer, Homepage | Infrastructure |
| monitoring.yml | Prometheus, Grafana, node_exporter, cAdvisor, Uptime Kuma | Observability |
| photos.yml | Immich | Photo management |
| books.yml | Calibre-web | E-books |
| proxy.yml | CLI Proxy API | API for opencode |
| sync.yml | Syncthing | File sync |

## Jellyfin / Media Stack (Task 1)

The **media** stack (media.yml) is the single source of truth for all Jellyfin-related containers. It is deployed as a Portainer stack on iskra. Do not use the standalone `~/jellyfin/docker-compose.yml` for new deployments; that folder is legacy and may be removed.

## Conventions

1. All files use `~/docker/.env` for shared variables; use `${VARIABLE}` syntax.
2. Configs persist to `/srv/config/<service>/`.
3. Restart policy: `unless-stopped` for all services.
4. Use LinuxServer.io images when available.

## Commands

```bash
docker compose --env-file ~/docker/.env -f <file>.yml up -d
docker compose -f <file>.yml logs -f
docker compose -f <file>.yml down
```

## Adding Services

1. Add to the appropriate .yml file.
2. Use PUID/PGID/TZ from .env; mount config to /srv/config/<service>.
3. Document in ~/docs/SERVICES.md.

## Backup stack (iskra)

| File | Services | Purpose |
|------|----------|---------|
| kopia.yml | kopia | Kopia server (Windows clients + homelab `/backup/*` snapshots) |
| backup.yml | kopia-server (legacy alt) | Prefer **kopia.yml** for production |

**Documentation:** `~/docs/BACKUP.md`

**Homelab snapshot paths (read-only mounts in kopia.yml):** compose, nextcloud, PhotoVault, photos, books, MCBackups, projectcea dumps.

**Offsite:** `~/docker/kopia-sync-to-b2.sh` → Backblaze B2 (entire repo). Env: `KOPIA_*`, `B2_*` in `~/docker/.env`.

**Not used:** Restic/Backrest for this stack.

## Auto-Update System (Watchtower)

Watchtower manages automatic container image updates for the compose stacks.

### How it works

- **Watchtower container**:  runs with .
- **Check frequency**: Every hour (3600 seconds).
- **Label-based**: Only containers with the label  are monitored.
- **Cleanup**: Old images are removed after a successful update (). Rollback is performed by pulling the previous version tag from Docker Hub.

### Auto-updated containers (labeled)

These containers will automatically update when a new image is published:

| Service | Compose file | Notes |
|---------|-------------|-------|
| nextcloud-app | nextcloud.yml | |
| nextcloud-web | nextcloud.yml | |
| nextcloud-redis | nextcloud.yml | |
| nextcloud-cron | nextcloud.yml | |
| jellyfin | media.yml | |
| immich-server | photos.yml | Only after VectorChord migration |
| immich-machine-learning | photos.yml | Only after VectorChord migration |
| immich-redis | photos.yml | Only after VectorChord migration |

### Manual-only containers (NEVER auto-updated)

These containers MUST NOT have the Watchtower label. They are updated manually, with runbook-governed procedures:

| Service | Compose file | Reason |
|---------|-------------|--------|
| nextcloud-db | nextcloud.yml | Database — manual update only |
| immich-postgres | photos.yml | Database — manual update only. Requires runbook-governed migration. |

**DB exclusion rule**: MUST NOT auto-update  or . Both are manual-only, runbook-governed.

### Rollback procedure

If an update breaks a service:

1. Identify the previous working image tag (from Docker Hub or local history).
2. Pull the previous version: 
3. Update the compose file to pin that tag (or use  locally).
4. Recreate the container: 
5. Verify health (see service-specific health checks in docs).

### Immich migration runbook

The Immich database migration (pgvecto.rs → VectorChord) is documented at:


This migration MUST be completed before the Immich containers can safely auto-update. Auto-updating Immich server against a pgvecto.rs database will brick the installation.

### Monitoring updates

Check Watchtower logs for update activity:



## Auto-Update System (Watchtower)

Watchtower manages automatic container image updates for the compose stacks.

### How it works

- **Watchtower container**: `compose-watchtower-1` runs with `--interval 3600 --label-enable --cleanup`.
- **Check frequency**: Every hour (3600 seconds).
- **Label-based**: Only containers with the label `com.centurylinklabs.watchtower.enable=true` are monitored.
- **Cleanup**: Old images are removed after a successful update (`--cleanup`). Rollback is performed by pulling the previous version tag from Docker Hub.

### Auto-updated containers (labeled)

These containers will automatically update when a new image is published:

| Service | Compose file | Notes |
|---------|-------------|-------|
| nextcloud-app | nextcloud.yml | |
| nextcloud-web | nextcloud.yml | |
| nextcloud-redis | nextcloud.yml | |
| nextcloud-cron | nextcloud.yml | |
| jellyfin | media.yml | |
| immich-server | photos.yml | Only after VectorChord migration |
| immich-machine-learning | photos.yml | Only after VectorChord migration |
| immich-redis | photos.yml | Only after VectorChord migration |

### Manual-only containers (NEVER auto-updated)

These containers MUST NOT have the Watchtower label. They are updated manually, with runbook-governed procedures:

| Service | Compose file | Reason |
|---------|-------------|--------|
| nextcloud-db | nextcloud.yml | Database -- manual update only |
| immich-postgres | photos.yml | Database -- manual update only. Requires runbook-governed migration. |

**DB exclusion rule**: MUST NOT auto-update `immich_postgres` or `nextcloud-db`. Both are manual-only, runbook-governed.

### Rollback procedure

If an update breaks a service:

1. Identify the previous working image tag (from Docker Hub or local history).
2. Pull the previous version: `docker pull <image>:<previous-version>`
3. Update the compose file to pin that tag (or use `docker tag` locally).
4. Recreate the container: `docker compose --env-file ~/docker/.env -f <file>.yml up -d <service>`
5. Verify health (see service-specific health checks in docs).

### Immich migration runbook

The Immich database migration (pgvecto.rs -> VectorChord) is documented at:
`compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`

This migration MUST be completed before the Immich containers can safely auto-update. Auto-updating Immich server against a pgvecto.rs database will brick the installation.

### Monitoring updates

Check Watchtower logs for update activity:

```bash
docker logs compose-watchtower-1 --tail 50
```
