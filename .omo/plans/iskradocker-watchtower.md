# iskradocker-watchtower - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Nextcloud, Jellyfin, and Immich on iskradocker will auto-update themselves using Watchtower (already running). When a new image is published, Watchtower pulls it within an hour, recreates the container, and cleans up the old image. If an update breaks something, you rollback by pulling the previous version tag from Docker Hub and recreating. Kopia continues hourly backups as the data-layer safety net. No scripts, no timers, no git — just Watchtower labels.

**Why this approach:** The existing Watchtower already does exactly this — it just needs labels added to the right containers. These are mature projects (Nextcloud, Jellyfin, Immich) with stable release channels; broken updates are rare and quickly fixed. Keeping `--cleanup` keeps disk clean; rollback is just `docker pull <old-version>` + recreate. A 200-line bash script was massive overkill for a homelab where Watchtower already exists and runs every hour.

**What it will NOT do:** touch the database containers (nextcloud-db, immich-postgres are manual-only, never auto-updated); touch cliproxyapi or the *arr stack; add the immich labels until the pgvecto → VectorChord DB migration is done (that migration requires your go-ahead); write any scripts or cron jobs.

**Effort:** Quick — 4 todos, 2 waves. The only manual step is the Immich DB migration, which pauses for your confirmation.
**Risk:** Low — everything is reversible via Docker Hub + Kopia, except the Immich DB migration (which has a DB dump + Kopia snapshot before it runs).
**Decisions to sanity-check:** (1) Keep `--cleanup` — old images are removed, but rollback is just pulling the previous version tag from Docker Hub. (2) Watchtower checks hourly — updates can happen at any time of day. (3) No email notifications — Watchtower logs to `docker logs compose-watchtower-1`.

Your next move: approve the plan, then run `$start-work`. Wave 1 (NC/JF labels) runs immediately. The Immich DB migration (T2) will pause for your explicit go-ahead before touching the database.

---

> TL;DR (machine): Quick effort, Low risk. 4 todos, 2 waves. Watchtower-based auto-update (keep --cleanup, add labels to NC+JF+immich). Immich gated on pgvecto→VectorChord migration (manual, pauses for user confirmation). No scripts, no timers, no git.

## Scope
### Must have
- Watchtower config is UNCHANGED — `--interval 3600 --label-enable --cleanup` stays as-is. User decided keeping `--cleanup` is fine; rollback is just pulling the previous version tag from Docker Hub.
- `com.centurylinklabs.watchtower.enable=true` label added to: nextcloud-app, nextcloud-web, nextcloud-redis, nextcloud-cron, jellyfin.
- Immich containers (immich-server, immich-machine-learning, immich-redis) get the label ONLY after the pgvecto → VectorChord DB migration completes (hard gate — Immich v3.0.0 removed pgvecto.rs support; auto-updating against the pinned pgvecto DB bricks Immich).
- Immich DB migration runbook already exists at iskradocker (`/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`).
- AGENTS.md docs updated to reflect the new auto-update mechanism.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **MUST NOT** add the Watchtower label to `immich_postgres` or `nextcloud-db` — both are manual-only, never auto-updated.
- **MUST NOT** add the Watchtower label to immich-server/ML/redis until the VectorChord migration completes (T2). Adding it early WILL brick Immich when Watchtower pulls `:release` v3.0.0+ against the pgvecto DB.
- **MUST NOT** modify Watchtower's command/args — keep `--interval 3600 --label-enable --cleanup` as-is.
- **MUST NOT** touch cliproxyapi — it already has the label and is being decommissioned; leave it alone.
- **MUST NOT** add labels to the *arr stack (qbittorrent, prowlarr, radarr, sonarr, bazarr, jellyseerr) — user only asked for Nextcloud, Jellyfin, and Immich.
- **MUST NOT** write a custom update script or systemd timer or cron job — Watchtower handles everything.
- **MUST NOT** use git on iskradocker — user explicitly does not want it.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after (docker inspect label checks + docker compose config validation)
- Evidence: .omo/evidence/task-<N>-iskradocker-watchtower.<ext>
- Health probes post-recreate:
  - nextcloud: `curl -fsS http://127.0.0.1:8082/status.php | jq -e '.installed==true and .maintenance==false'`
  - jellyfin: `curl -fsS http://127.0.0.1:8096/health | grep -q Healthy`
  - immich (post-migration): `curl -fsS http://127.0.0.1:2283/api/server/ping | jq -e '.res=="pong"'`
  - DBs untouched: `docker inspect nextcloud-db --format '{{.State.StartedAt}}'` unchanged after Watchtower runs; same for `immich_postgres`.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means under-split.

Wave 1 (Watchtower reconfig + Nextcloud/Jellyfin labels) and Wave 2 (Immich migration prep) are independent. Wave 3 (Immich labels + docs) depends on Wave 2.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 (reconfig Watchtower + NC/JF labels) | — | F1-F4 | T2 |
| T2 (immich DB migration, manual) | — | T3 | T1 |
| T3 (immich labels) | T2 | F1-F4 | — |
| T4 (AGENTS.md docs) | T1,T3 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Add Watchtower labels to Nextcloud + Jellyfin
  What to do / Must NOT do:
    (a) Edit `/home/antoine/docker/compose/nextcloud.yml`: add `labels: - "com.centurylinklabs.watchtower.enable=true"` to these 4 services: `nextcloud-app`, `nextcloud-web`, `nextcloud-redis`, `nextcloud-cron`. Do NOT add the label to `nextcloud-db`.
    (b) Edit `/home/antoine/docker/compose/media.yml`: add the same label to `jellyfin` ONLY. Do NOT add it to qbittorrent, prowlarr, radarr, sonarr, bazarr, or jellyseerr.
    (c) Recreate affected containers: `cd /home/antoine/docker/compose && docker compose --env-file ../.env -f nextcloud.yml -f media.yml up -d` (applies labels to NC/JF containers).
    (d) Verify health: `curl -fsS http://127.0.0.1:8082/status.php | jq -e '.installed==true and .maintenance==false'` and `curl -fsS http://127.0.0.1:8096/health | grep -q Healthy`.
    (e) Verify Watchtower logs show no errors: `docker logs compose-watchtower-1 --tail 20`.
  Must NOT: add label to nextcloud-db; add labels to *arr stack; add labels to immich (that's T3, gated on T2); modify Watchtower's command/args; write a script or timer.
  Parallelization: Wave 1 | Blocked by: none | Blocks: F1-F4
  References (executor has NO interview context):
    - All work is on iskradocker via SSH (host alias: `iskradocker`, user: `antoine`, sudo password: `Lenin1917`).
    - NEVER modify any file in `/home/antoine/ProjectCEA/` — work only on iskradocker.
    - Watchtower config is UNCHANGED — keep `--interval 3600 --label-enable --cleanup` as-is.
    - proxy.yml verified: Watchtower already running with `--cleanup`. User decided to keep it.
    - nextcloud.yml verified: 5 services (nextcloud-app, nextcloud-web, nextcloud-db, nextcloud-redis, nextcloud-cron). nextcloud-app and nextcloud-cron share `nextcloud:31-fpm` image.
    - media.yml verified: jellyfin uses `network_mode: host` + `/dev/dri` devices. 7 services total (jellyfin + 6 *arr services).
    - nextcloud-app and nextcloud-cron are currently running on an older image (tag moved to newer image); Watchtower will update them on first check after labels are added.
    - Compose project name: `compose`. Env file: `/home/antoine/docker/.env`.
    - Existing cliproxyapi label in proxy.yml is the pattern to follow.
  Acceptance criteria (agent-executable):
    - `ssh iskradocker 'docker inspect nextcloud-app --format "{{json .Config.Labels}}"' | jq -r '.["com.centurylinklabs.watchtower.enable"]'` == `true`.
    - Same label check for: nextcloud-web, nextcloud-redis, nextcloud-cron, jellyfin.
    - `ssh iskradocker 'docker inspect nextcloud-db --format "{{json .Config.Labels}}"' | jq -r '.["com.centurylinklabs.watchtower.enable"] // "absent"'` == `absent` (DB NOT labeled).
    - `ssh iskradocker 'curl -fsS http://127.0.0.1:8082/status.php' | jq -e '.installed==true and .maintenance==false'` exits 0.
    - `ssh iskradocker 'curl -fsS http://127.0.0.1:8096/health' | grep -q Healthy` exits 0.
  QA scenarios: happy: all 5 containers (4 NC + 1 JF) have the label, DBs don't, Watchtower config unchanged, health probes pass. failure: deliberately remove the label from nextcloud-app → `docker inspect` shows absent → should fail the acceptance check. Evidence: .omo/evidence/task-1-iskradocker-watchtower.txt (docker inspect output for all containers + health probes).
  Commit: N (no git on iskradocker per user request)

- [x] 2. Execute immich DB migration (pgvecto → VectorChord) — MANUAL, user-gated
  What to do / Must NOT do: Walk through the runbook at `/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md` step-by-step on iskradocker. This changes the database engine (cannot downgrade Immich below v1.133.0 after). Pause for user confirmation before step 5 (`up -d`). The runbook steps: (1) DB dump, (2) Kopia snapshot, (3) Edit photos.yml — change immich-postgres image from `ghcr.io/tensorchord/pgvecto-rs:pg14-v0.2.0` to `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` + add `shm_size: 128mb`, (4) Pull all 4 immich images together, (5) Recreate all 4 immich containers (PAUSE FOR USER HERE), (6) Monitor reindex logs, (7) Verify ping + vchord extension present, (8) Note: photos are never touched — only the database engine changes.
  Must NOT: run unattended (user must confirm before step 5); skip the DB dump; pull immich-server alone (always group the 4 containers).
  Parallelization: Wave 2 | Blocked by: none | Blocks: T3
  References (executor has NO interview context):
    - All work is on iskradocker via SSH (host alias: `iskradocker`, user: `antoine`, sudo password: `Lenin1917`).
    - NEVER modify any file in `/home/antoine/ProjectCEA/`.
    - Runbook EXISTS at `/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md` (verified — 8 steps, committed).
    - photos.yml verified: immich-postgres image is `ghcr.io/tensorchord/pgvecto-rs:pg14-v0.2.0` (must change to `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0`).
    - Immich v3.0.0 (June 2026) REMOVED pgvecto.rs support. Auto-updating immich-server:release against the pinned pgvecto DB WILL brick Immich.
    - `.env` has DB_USERNAME, DB_PASSWORD, DB_DATABASE_NAME, DB_DATA_LOCATION for immich.
    - Kopia covers /backup/photos (78.8 GB snapshot exists). Kopia container name: `kopia`.
    - This task is BLOCKED on user confirmation — mark checkbox as `- [~]` until the user gives explicit go-ahead to proceed with step 5.
  Acceptance criteria (agent-executable):
    - Before: `ssh iskradocker 'docker inspect immich_postgres --format "{{.Config.Image}}"'` == `ghcr.io/tensorchord/pgvecto-rs:pg14-v0.2.0`.
    - After: `ssh iskradocker 'docker inspect immich_postgres --format "{{.Config.Image}}"'` == `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0`.
    - After: `ssh iskradocker 'curl -fsS http://127.0.0.1:2283/api/server/ping'` returns `{"res":"pong"}`.
    - After: `ssh iskradocker 'docker exec immich_postgres psql -U immich -d immich -tAc "SELECT extname FROM pg_extension WHERE extname='"'"'vchord'"'"';"'` returns `vchord`.
    - After: `ssh iskradocker 'docker logs immich_server 2>&1 | tail -200 | grep -E "Reindexed (face|clip)_index"'` exits 0.
  QA scenarios: happy: immich_server comes up, ping=pong, vchord extension present, reindex logs present. failure: `docker logs immich_server` shows "extension not available" → runbook step 0 says stop + restore from Kopia. Evidence: .omo/evidence/task-2-iskradocker-watchtower.{txt,ndjson}.
  Commit: N (no git on iskradocker)

- [x] 3. Add Watchtower labels to Immich containers (after T2 migration)
  What to do / Must NOT do: Edit `/home/antoine/docker/compose/photos.yml`: add `labels: - "com.centurylinklabs.watchtower.enable=true"` to these 3 services: `immich-server`, `immich-machine-learning`, `immich-redis`. Do NOT add the label to `immich-postgres`. Recreate: `cd /home/antoine/docker/compose && docker compose --env-file ../.env -f photos.yml up -d immich-server immich-machine-learning immich-redis` (explicit service names — NEVER bare `up -d` which would recreate immich-postgres too). Verify health: `curl -fsS http://127.0.0.1:2283/api/server/ping | jq -e '.res=="pong"'`. Verify DB NOT recreated: `docker inspect immich_postgres --format '{{.State.StartedAt}}'` must be unchanged from before this todo.
  Must NOT: add label to immich-postgres (EVER); run before T2 completes; use bare `docker compose up -d`.
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: F1-F4
  References (executor has NO interview context):
    - All work is on iskradocker via SSH (host alias: `iskradocker`, user: `antoine`, sudo password: `Lenin1917`).
    - NEVER modify any file in `/home/antoine/ProjectCEA/`.
    - photos.yml verified: 4 services (immich-server, immich-machine-learning, immich-redis, immich-postgres). Label goes on the first 3 only.
    - After T2, immich-postgres runs `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` so immich-server:release auto-updates are now safe.
    - Compose project name: `compose`. Env file: `/home/antoine/docker/.env`.
  Acceptance criteria (agent-executable):
    - `ssh iskradocker 'docker inspect immich_server --format "{{json .Config.Labels}}"' | jq -r '.["com.centurylinklabs.watchtower.enable"]'` == `true`.
    - Same label check for: immich_machine_learning, immich_redis.
    - `ssh iskradocker 'docker inspect immich_postgres --format "{{json .Config.Labels}}"' | jq -r '.["com.centurylinklabs.watchtower.enable"] // "absent"'` == `absent` (DB NOT labeled).
    - `ssh iskradocker 'curl -fsS http://127.0.0.1:2283/api/server/ping'` returns `{"res":"pong"}`.
  QA scenarios: happy: 3 immich containers labeled, DB not labeled, ping=pong. failure: label on immich_postgres → acceptance check catches it. Evidence: .omo/evidence/task-3-iskradocker-watchtower.txt.
  Commit: N (no git on iskradocker)

- [x] 4. Update AGENTS.md docs to document the auto-update mechanism
  What to do / Must NOT do: Add a section to `/home/antoine/docker/AGENTS.md` and `/home/antoine/docker/compose/AGENTS.md` documenting: Watchtower manages auto-updates (hourly check, `--label-enable` without `--cleanup`); labeled containers = nextcloud-app/web/redis/cron, jellyfin, immich-server/ML/redis; unlabeled (manual-only) = nextcloud-db, immich-postgres; old images are retained as rollback (no `--cleanup`); rollback procedure = `docker tag <old-image-id> <image:tag> && docker compose up -d <service>`; immich migration runbook at `compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`.
  Must NOT: duplicate the runbook content; expand scope beyond Watchtower auto-update docs.
  Parallelization: Wave 3 | Blocked by: T1, T3 | Blocks: F1-F4
  References (executor has NO interview context):
    - All work is on iskradocker via SSH (host alias: `iskradocker`, user: `antoine`, sudo password: `Lenin1917`).
    - NEVER modify any file in `/home/antoine/ProjectCEA/`.
    - Existing docs: `/home/antoine/docker/AGENTS.md` + `/home/antoine/docker/compose/AGENTS.md` (already document the stack layout).
    - Runbook: `/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`.
  Acceptance criteria (agent-executable):
    - `ssh iskradocker 'grep -q "watchtower" /home/antoine/docker/AGENTS.md'` exits 0 (case-insensitive).
    - `ssh iskradocker 'grep -q "watchtower" /home/antoine/docker/compose/AGENTS.md'` exits 0.
    - `ssh iskradocker 'grep -qi "immich_postgres.*manual\|manual.*immich_postgres\|MUST NOT.*immich_postgres" /home/antoine/docker/compose/AGENTS.md'` exits 0 (exclusion documented).
    - `ssh iskradocker 'grep -q "rollback" /home/antoine/docker/AGENTS.md'` exits 0 (rollback procedure documented).
  QA scenarios: happy: both files updated, all grep checks pass. failure: delete the section → grep fails. Evidence: .omo/evidence/task-4-iskradocker-watchtower.md.
  Commit: N (no git on iskradocker)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — verify: Watchtower config unchanged (`--interval 3600 --label-enable --cleanup`); labels on exactly nextcloud-app, nextcloud-web, nextcloud-redis, nextcloud-cron, jellyfin, immich-server, immich-machine-learning, immich-redis; NO labels on nextcloud-db, immich-postgres; NO labels added/changed on cliproxyapi; NO labels on *arr stack. Tool: `docker inspect` each container + `docker inspect compose-watchtower-1 --format '{{json .Args}}'`.
- [x] F2. No leftover artifacts — verify: no leftover script at `/home/antoine/docker/scripts/iskradocker-autoupdate.sh`; no leftover systemd units at `/etc/systemd/system/iskradocker-autoupdate.*`; no `.git` directory in `/home/antoine/docker/compose/`. Tool: `ssh iskradocker 'ls /home/antoine/docker/scripts/iskradocker-autoupdate.sh /etc/systemd/system/iskradocker-autoupdate.* /home/antoine/docker/compose/.git 2>&1'` should show "No such file".
- [x] F3. Real manual QA — on iskradocker: verify all labeled containers are running and healthy; `docker logs compose-watchtower-1 --tail 50` shows Watchtower checking the right containers; confirm DB containers' `StartedAt` is unchanged (not recreated by Watchtower). Tool: `docker inspect` + `curl` health probes + `docker logs`.
- [x] F4. Scope fidelity — Watchtower still manages cliproxyapi (unchanged); *arr stack NOT labeled; only the approved containers are labeled; no custom scripts/timers left behind.

## Commit strategy
- No git on iskradocker (user explicitly does not want git). All changes are applied directly to compose files on iskradocker via SSH.
- The old plan's git init (T1) was reverted — `.git` directory removed from `/home/antoine/docker/compose/`.

## Success criteria
1. Watchtower config unchanged (`--interval 3600 --label-enable --cleanup`).
2. Nextcloud (app/web/redis/cron) + Jellyfin containers have the Watchtower label and will auto-update when new images are published.
3. Immich DB migrated to VectorChord; immich-server/ML/redis labeled for auto-update.
4. DB containers (nextcloud-db, immich-postgres) do NOT have the label — manual-only, never auto-updated.
5. Kopia continues hourly snapshots as the data-layer rollback safety net.
6. No custom scripts, no systemd timers, no cron jobs, no git — just Watchtower + labels.
7. AGENTS.md documents the mechanism + rollback procedure + DB exclusion rule.
8. If an update breaks something, rollback is: `docker pull <image>:<previous-version>` + `docker compose up -d <service>` (pull from Docker Hub).
