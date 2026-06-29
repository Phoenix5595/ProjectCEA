# iskradocker-updates - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Nextcloud, Jellyfin, and Immich on `iskradocker` will update themselves automatically every Sunday at 04:00 (Montreal time), with a fresh backup taken first, the previous image kept as a one-click rollback for 4 weeks, an automatic rollback if the updated service fails its health check, and an email to you reporting success or failure. Your compose configs also get put under git so changes are tracked.

**Why this approach:** (1) A scheduled script with pre-tagged rollback images gives you a real safety net — your existing Watchtower only manages `cliproxyapi` (which you're decommissioning) and its `--cleanup` flag destroys old images so there's nothing to roll back to. (2) The DB containers are deliberately excluded from auto-update because their versions are tightly coupled to the app: `immich_postgres` runs `pgvecto-rs:pg14-v0.2.0`, which Immich **deprecated** in v1.133.0 and **removed support for** in v3.0.0 (June 2026) — auto-updating `immich_server:release` against that pinned DB would brick Immich, and roll back does not undo DB migrations. So Immich gets a one-time manual DB migration to VectorChord first, then joins the auto-update roster.

**What it will NOT do:** touch Watchtower or cliproxyapi (out of scope); auto-update `immich_postgres` or `nextcloud-db` (manual, runbook-governed, ever); run `docker image prune -a` (would delete rollback images); hardcode secrets in the script; send more than one notification email per run.

**Effort:** Medium — 9 todos across 3 waves; the Immich DB migration (Wave 2) is the only stateful/irreversible step and is gated on your manual confirmation.
**Risk:** Medium — driver is the immich pgvecto→VectorChord migration (irreversible, but preceeded by DB dump + Kopia snapshot); everything else is reversible via rollback tags or Kopia restore.
**Decisions to sanity-check:** (1) Immich DB migration must run before immich_server joins auto-update — this is a hard gate, not a soft one. (2) Nextcloud health check uses `/status.php` through the nginx web container (not a direct container probe) — if Caddy blips it could false-rollback; accepted because Caddy doesn't front these three services. (3) Stack update order is Nextcloud → Jellyfin → Immich, abort-on-failure (conservative).

Your next move: approve the plan, then run `$start-work` to kick off execution (Wave 1 first; the Immich DB migration in Wave 2 will pause for your explicit go-ahead before touching the DB). Full execution detail follows below.

---

> TL;DR (machine): Medium effort, Medium risk. 9 todos, 3 waves. Cron-based auto-update for Nextcloud+Jellyfin+Immich on iskradocker, Sun 04:00 ET, rollback-tagged, DBs excluded, immich gated on pgvecto→VectorChord migration (irreversible, manual). Watchtower untouched.

## Scope
### Must have
- A scheduled job (systemd timer, Sun 04:00 America/Toronto) running on `iskradocker` that safely auto-updates whitelisted Docker containers for Nextcloud, Jellyfin (and Immich server/ML/redis — **deferred until DB migration completes**, see Wave 2).
- Pre-update safety: on-demand Kopia snapshot of `/backup/compose /backup/nextcloud /backup/photos`, plus rollback-image pre-tag (`<image>:rollback-<ts>` by image ID, retained 28 days).
- Auto-rollback on health-check failure (restore pre-tagged image + recreate).
- Notification on every run (success digest + failure/rollback) via email (Gmail SMTP `smtp.gmail.com:587`, creds in `.env`).
- `git init` on `/home/antoine/docker/compose/` tracking compose files + script + runbooks, with `.gitignore` for `.env* *.bak* *.backup* *.disabled`.
- A documented manual runbook for the `immich_postgres` pgvecto→VectorChord migration + `nextcloud-db`/`nextcloud-app` major-version bumps.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **MUST NOT** add `immich_server` / `immich_machine_learning` to the auto-update whitelist until the pgvecto→VectorChord DB migration (Wave 2) is complete — Immich v3.0.0 (June 2026) **removed pgvecto.rs support**; auto-updating `:release` against the pinned `pgvecto-rs:pg14-v0.2.0` DB will brick Immich (rollback does NOT undo DB migrations). Evidence: librarian research citing docs.immich.app/install/upgrading#migrating-to-vectorchord.
- **MUST NOT** ever auto-update `immich_postgres` (pgvecto) or `nextcloud-db` (postgres:16-alpine). Both are manual-only, runbook-governed.
- **MUST NOT** touch Watchtower or `cliproxyapi` (out of scope; cliproxyapi being decommissioned).
- **MUST NOT** use bare `docker compose up -d` — always pass explicit whitelisted service names, to avoid recreating excluded DB containers (Metis gap 11).
- **MUST NOT** hardcode secrets in the script (script is in git); creds live in gitignored `.env`.
- **MUST NOT** use `docker image prune -a` (deletes rollback-tagged images); only `docker image prune` (dangling only).
- **MUST NOT** treat image rollback as state/DB rollback (documented limitation; recovery beyond 28 days is Kopia restore).
- **MUST NOT** fall back to `docker-compose` v1; abort + notify if `docker compose` v2 unavailable.
- **MUST NOT** expand the whitelist beyond the approved 8 containers.
- **MUST NOT** disable approvals/sandbox in any review tool.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after (shell script; `bats` if available, else `set -e` + `assert`-style functions) + a `--dry-run` mode that exercises the full pipeline except `up -d`.
- Evidence dir: `/home/antoine/ProjectCEA/.omo/evidence/task-<N>-iskradocker-updates.<ext>` (copies of captured stdout/NDJSON logs; the script's own runtime log lives on iskradocker at `/var/log/iskradocker-autoupdate.ndjson`).
- Health probes per service (executor runs these post-update):
  - nextcloud-app (via nextcloud-web): `curl -fsS "http://127.0.0.1:8082/status.php" | jq -e '.installed==true and .maintenance==false and .needsDbUpgrade==false'`
  - jellyfin: `curl -fsS "http://127.0.0.1:8096/health" | grep -q "Healthy"` AND `docker exec jellyfin ls /dev/dri/renderD128` AND `docker inspect jellyfin --format '{{.HostConfig.NetworkMode}}' | grep -q host`
  - immich_server (post-migration only): `curl -fsS "http://127.0.0.1:2283/api/server/ping" | jq -e '.res=="pong"'`
  - DB untouched: `docker exec nextcloud-db pg_isready -U nextcloud` AND `docker exec immich_postgres pg_isready -U ${DB_USERNAME:-immich}` both return "accepting connections".

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means under-split.

Wave 1 (bootstrap + lower-risk auto-update for Nextcloud + Jellyfin) and Wave 2 (Immich DB migration precondition — sequential, manual-gated) are **independent and parallelizable** in their prep steps; Wave 3 (ops hardening) depends on Wave 1. Wave 2 must complete before immich_server joins the auto-update whitelist.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 (git init) | — | T2,T6,T7,T8 | T3,T5,T9 |
| T2 (update script core) | T1 | T4,T6,T8 | T3,T5,T9 |
| T3 (systemd timer) | — | T4 | T1,T2,T5,T9 |
| T4 (dry-run + live enable NC+JF) | T2,T3 | F1-F4 | T5,T9 |
| T5 (immich rollup + runbook) | — | T6 | T1,T2,T3,T9 |
| T6 (immich DB migration, manual) | T1,T2,T5 | T7 | — |
| T7 (add immich to whitelist) | T6 | F1-F4 | T8,T9 |
| T8 (rollback GC + logging polish + notification wiring) | T1,T2 | F1-F4 | T7,T9 |
| T9 (update AGENTS.md docs) | T2,T3,T5 | F1-F4 | T7,T8 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [~] 1. Git-init compose dir + comprehensive .gitignore — **REMOVED per user request**
  What to do / Must NOT do: On iskradocker, `git init` in `/home/antoine/docker/compose/`. Write `.gitignore` covering AT MINIMUM: `.env`, `.env.*`, `*.bak`, `*.bak.*`, `*.backup*`, `*.disabled`, `Caddyfile-https` (if it carries secrets), `provisioning/` secrets if any. Commit existing compose files as the baseline (`Initial commit of iskra homelab compose configs`). Do NOT commit `.env*` under any circumstance (verify with `git status --porcelain` showing no `.env` staged). Do NOT add a remote unless the user asks (local-only repo).
  Parallelization: Wave 1 | Blocked by: none | Blocks: T2,T6,T7,T8
  References (executor has NO interview context):
    - Verified: `/home/antoine/docker` is NOT currently a git repo (`git rev-parse` → fatal).
    - Compose dir: `/home/antoine/docker/compose/` (contains nextcloud.yml, photos.yml, media.yml, proxy.yml, infra.yml, monitoring.yml, Caddyfile, books.yml, kopia.yml, backup.yml, etc.).
    - Secret-bearing files to exclude: `compose/.env`, `compose/.env.bak.1776520673`, `docker/.env`, `docker/.env.dashboard`, `compose/monitoring.yml.bak.20260418-153322`, `compose/proxy.yml.bak`, `compose/proxy.yml.backup-20260217-222043`, `compose/nextcloud.yml.backup`.
    - Metis gap 21: `.gitignore` must be reviewed for ALL secret-bearing files, not just `.env*`.
  Acceptance criteria (agent-executable):
    - `cd /home/antoine/docker/compose && git rev-parse --is-inside-work-tree` exits 0.
    - `git -C /home/antoine/docker/compose status --porcelain | grep -E '\.env' ` returns nothing (no .env tracked/staged).
    - `git -C /home/antoine/docker/compose log --oneline -1` shows a baseline commit.
    - `git -C /home/antoine/docker/compose check-ignore .env .env.bak .env.dashboard` exits 0 for each.
  QA scenarios (name the exact tool + invocation): happy: `git -C /home/antoine/docker/compose check-ignore -v .env` shows the .gitignore line. failure: stage `.env` manually (`git add -f .env`) → `git status --porcelain` must flag it; `git reset` then. Evidence: .omo/evidence/task-1-iskradocker-updates.txt (git status + check-ignore output).
  Commit: Y | chore(compose): git-init iskra homelab compose dir with secret-safe .gitignore

- [ ] 2. Write the update script core (`iskradocker-autoupdate.sh`)
  What to do / Must NOT do: Create `/home/antoine/docker/scripts/iskradocker-autoupdate.sh` (chmod +x). The script:
    1. `flock -n /var/lock/iskradocker-autoupdate.lock` — exit 0 immediately if held (Metis gap 12).
    2. Preflight: `docker compose version` exits 0 (Metis gap 8); else abort+notify.
    3. Read secrets from `/home/antoine/docker/.env` via `set -a; source /home/antoine/docker/.env; set +a` (Metis gap 7).
    4. Define `STACKS` array, each entry = `{ name, compose_file, services[], health_probe_cmd, pre_snapshot_paths[] }`:
       - nextcloud:        compose/nextcloud.yml, services=[nextcloud-app,nextcloud-cron,nextcloud-web,nextcloud-redis] (grouped atomically — Metis gap 4), probe=`curl -fsS http://127.0.0.1:8082/status.php | jq -e '.installed==true and .maintenance==false and .needsDbUpgrade==false'`, snapshot_paths=`/backup/compose /backup/nextcloud`
       - jellyfin:         compose/media.yml, services=[jellyfin], probe=`curl -fsS http://127.0.0.1:8096/health | grep -q Healthy && docker exec jellyfin ls /dev/dri/renderD128 && [ "$(docker inspect jellyfin --format '{{.HostConfig.NetworkMode}}')" = host ]`, snapshot_paths=`/backup/compose /srv/config/jellyfin`
       - (immich entry exists but is DISABLED via flag until Wave 2; `if [ "$IMMICH_AUTO_UPDATE_ENABLED" = "1" ]`.)
    5. Per stack: (a) trigger on-demand Kopia snapshot via `docker exec kopia kopia snapshot create ${snapshot_paths}` (Metis gap 9); verify exit 0 before proceeding. (b) For each service in services[]: `docker tag <current_image_id> <image_base>:rollback-<ts>` BEFORE pull (Metis gap 6). Get current image ID via `docker inspect <container> --format '{{.Image}}'` and base name via `docker inspect <container> --format '{{.Config.Image}}'`. (c) `docker compose --env-file /home/antoine/docker/.env -f /home/antoine/docker/compose/<file> pull <services>` — on failure abort stack + notify (Metis gap 18). (d) `docker compose --env-file /home/antoine/docker/.env -f <file> up -d <services>` — explicit service names, NEVER bare `up -d` (Metis gap 11). (e) Poll health probe up to `HEALTH_RETRY_COUNT=30` times, `HEALTH_RETRY_INTERVAL=20s` (start-period equivalent ~10 min for `occ upgrade`; Metis gap 2, 20). (f) On probe failure: `docker tag <image_base>:rollback-<ts> <image_base>` then `docker compose ... up -d <services>`, re-probe; if still failing → CRITICAL notification (Metis gap 17). (g) Log NDJSON to `/var/log/iskradocker-autoupdate.ndjson`: per-stack `{ts, stack, services, old_image_id, new_image_id, probe_status, rolled_back}` (Metis gap 14, 23).
    6. Stack order: nextcloud → jellyfin → immich(disabled). On nextcloud failure+rollback, ABORT remaining stacks (conservative; Metis gap 13).
    7. Support `--dry-run`: do steps a-d but NOT `up -d`; log intended actions (Metis gap 16).
  Must NOT: hardcode secrets; use `docker-compose` v1; use bare `up -d`; add immich_server to live config; prune with `-a`.
  Parallelization: Wave 1 | Blocked by: T1 (git, for tracking) | Blocks: T4,T6,T8
  References (executor has NO interview context):
    - Compose files verified source-of-truth (docker inspect labels: config_files=/home/antoine/docker/compose/{nextcloud,photos,media}.yml, project=compose).
    - nextcloud-app has NO compose healthcheck (Metis gap 2) — script's external probe via nextcloud-web port 8082 is the contract.
    - nextcloud-app + nextcloud-cron share `nextcloud:31-fpm` — atomic group (Metis gap 4).
    - Jellyfin `network_mode: host` + `/dev/dri` devices in media.yml — must preserve (Metis gap 5).
    - Kopia container name: `kopia`. Kopia snapshot paths verified: /backup/compose, /backup/nextcloud, /backup/photos covered hourly already.
    - Immich pgvecto deprecation: librarian-confirmed — `pgvecto-rs:pg14-v0.2.0` deprecated, VectorChord migration required, pgvecto support REMOVED in Immich v3.0.0.
  Acceptance criteria (agent-executable):
    - `bash -n /home/antoine/docker/scripts/iskradocker-autoupdate.sh` (syntax) exits 0.
    - `bash /home/antoine/docker/scripts/iskradocker-autoupdate.sh --dry-run` runs preflight + Kopia snapshot + pull + tags rollback, but `docker ps --filter "label=com.docker.compose.project=compose"` shows no NEW container IDs vs pre-run.
    - Simulate `docker compose` missing (`PATH=/empty docker compose version`): script aborts with exit !=0 and logs `"preflight":"fail"` to NDJSON.
    - Simulate concurrent run: `flock -n /var/lock/iskradocker-autoupdate.lock echo held &  bash iskradocker-autoupdate.sh --dry-run` exits 0 immediately with `"skip":"lock-held"`.
    - `grep -c 'docker compose up -d$\|docker compose up -d "' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` == 0 (no bare up -d).
  QA scenarios: happy: `--dry-run` produces NDJSON log with `"stack":"nextcloud","action":"pull-and-tag","dry_run":true`. failure: `docker compose pull` with a deliberately bad tag → script logs `"pull":"fail"` and does NOT call `up -d`. Evidence: .omo/evidence/task-2-iskradocker-updates.{ndjson,txt}.
  Commit: Y | feat(scripts): add iskradocker-autoupdate.sh — cron update engine with rollback

- [ ] 3. Systemd timer + service units (Sun 04:00 America/Toronto)
  What to do / Must NOT do: Write `/etc/systemd/system/iskradocker-autoupdate.service` (`Type=oneshot`, `User=antoine`, `Group=docker` if exists else `root`, `ExecStart=/home/antoine/docker/scripts/iskradocker-autoupdate.sh`, `EnvironmentFile=/home/antoine/docker/.env` is NOT needed — script sources it; `Nice=10`, `IOSchedulingClass=idle`). Write `/etc/systemd/system/iskradocker-autoupdate.timer` (`OnCalendar=Sun 04:00 America/Toronto`, `Persistent=false`, `RandomizedDelaySec=300` to avoid thundering herd). `systemctl daemon-reload && systemctl enable --now iskradocker-autoupdate.timer`. Do NOT use a plain crontab (Metis gap 15: systemd gives journalctl logging + retry semantics).
  Must NOT: set `Persistent=true` (we don't want missed runs firing on boot); use root crontab.
  Parallelization: Wave 1 | Blocked by: none | Blocks: T4
  References:
    - Existing cron pattern on iskradocker: `/etc/cron.d/kopia-backup` (so cron.d is established, but systemd timer is better for logging).
    - `id antoine` is sudoer; `groups antoine` includes docker access (verified: `docker ps` works as antoine).
    - TZ in .env = America/Montreal (same as Toronto for scheduling).
  Acceptance criteria (agent-executable):
    - `systemctl list-timers iskradocker-autoupdate.timer` shows the timer loaded with `Sun 04:00` next trigger.
    - `systemctl cat iskradocker-autoupdate.service` shows `ExecStart=/home/antoine/docker/scripts/iskradocker-autoupdate.sh`.
    - `systemd-analyze verify /etc/systemd/system/iskradocker-autoupdate.{service,timer}` exits 0.
    - `systemctl start iskradocker-autoupdate.service` runs the script once (in dry-run? No — real run; but T4 gates this). For T3, verify trigger only: `journalctl -u iskradocker-autoupdate.service --since "1 min ago"` shows the unit was invoked.
  QA scenarios: happy: `systemctl list-timers` shows next Sun 04:00 ET. failure: set `OnCalendar=*-*-* 99:99:99` → `systemd-analyze verify` fails; revert. Evidence: .omo/evidence/task-3-iskradocker-updates.txt (systemctl + systemd-analyze output).
  Commit: Y | chore(systemd): add iskradocker-autoupdate timer (Sun 04:00 ET)

- [ ] 4. Dry-run validation + live enablement (Nextcloud + Jellyfin only)
  What to do / Must NOT do: Run `iskradocker-autoupdate.sh --dry-run` and inspect NDJSON log. Then run for real (`systemctl start iskradocker-autoupdate.service` or `bash iskradocker-autoupdate.sh` without --dry-run). Confirm Nextcloud + Jellyfin updated cleanly; immich stays untouched (IMMICH_AUTO_UPDATE_ENABLED unset).
  Must NOT: enable immich in this todo; continue if nextcloud fails (abort policy).
  Parallelization: Wave 1 | Blocked by: T2,T3 | Blocks: F1-F4
  References:
    - nextcloud probe: `curl -fsS http://127.0.0.1:8082/status.php`.
    - jellyfin probe: `curl -fsS http://127.0.0.1:8096/health` + `/dev/dri` + host network.
    - Rollback tag naming: `<image_base>:rollback-<unix_ts>`.
  Acceptance criteria (agent-executable):
    - After live run: `curl -fsS http://127.0.0.1:8082/status.php | jq -e '.installed==true and .maintenance==false'` exits 0.
    - After live run: `docker exec jellyfin ls /dev/dri/renderD128` exits 0; `docker inspect jellyfin --format '{{.HostConfig.NetworkMode}}'` == `host`.
    - `docker exec nextcloud-db pg_isready -U nextcloud` returns "accepting connections" (DB NOT recreated — Metis gap 11 verified).
    - `docker images --filter "reference=*:rollback-*"` shows at least 2 rollback tags (nextcloud + jellyfin).
    - Tail of `/var/log/iskradocker-autoupdate.ndjson` shows `"stack":"nextcloud","probe_status":"ok"` and `"stack":"jellyfin","probe_status":"ok"`.
  QA scenarios: happy: NDJSON shows both stacks probe ok within 10 min. failure: temporarily break nextcloud-web (stop it), run script → NDJSON shows `"probe":"fail","rolled_back":true` and nextcloud-web restored; `status.php` returns ok within 60s. Evidence: .omo/evidence/task-4-iskradocker-updates.{ndjson,txt}.
  Commit: N (operational; script+units already committed in T2/T3)

- [x] 5. Immich pgvecto→VectorChord migration runbook + pre-flight
  What to do / Must NOT do: Write `/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`. Content (sourced from librarian-verified official docs at docs.immich.app/install/upgrading#migrating-to-vectorchord):
    0. Preconditions: confirm current `immich_server` < v1.133.0 OR has already auto-migrated; if `:release` pulled v3.0.0+ against pinned pgvecto, STOP — Immich is already bricked, restore immich_postgres volume from Kopia `docker exec kopia kopia snapshot create /backup/photos` (DB_DATA_LOCATION).
    1. `docker exec -t immich_postgres pg_dump --clean --if-exists --dbname=immich --username=postgres | gzip > /home/antoine/backups/immich-pre-vectorchord-$(date +%Y%m%d).sql.gz` (DB dump safety).
    2. Trigger on-demand Kopia snapshot: `docker exec kopia kopia snapshot create /backup/photos /backup/compose`.
    3. Edit `/home/antoine/docker/compose/photos.yml`: change `immich-postgres` image from `ghcr.io/tensorchord/pgvecto-rs:pg14-v0.2.0` to `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0` (cite: official docker-compose.yml) and add `shm_size: 128mb`. Commit to git.
    4. `cd /home/antoine/docker/compose && docker compose --env-file ../.env -f photos.yml pull immich-postgres immich-server immich-machine-learning immich-redis` (pull all 4 together — Metis gap 4/29).
    5. `docker compose --env-file ../.env -f photos.yml up -d immich-postgres immich-server immich-machine-learning immich-redis` (explicit names; Metis gap 11).
    6. Tail `docker logs -f immich_server` for "Reindexed face_index" and "Reindexed clip_index" — VectorChord auto-migration on startup (may take minutes for 78.8 GB photo lib).
    7. Post-migration verify: `curl -fsS http://127.0.0.1:2283/api/server/ping | jq -e '.res=="pong"'`; `docker exec immich_postgres psql -U immich -d immich -c "SELECT extname, extversion FROM pg_extension WHERE extname IN ('vchord','vectors');"` shows vchord present.
    8. Note: cannot downgrade Immich < v1.133.0 after this. Cannot use DB_VECTOR_EXTENSION=pgvecto.rs in v3.0.0+ (removed).
  Must NOT: skip the DB dump; pull immich-server alone (always group the 4 immich containers).
  Parallelization: Wave 2 prep (can start in parallel with Wave 1) | Blocked by: none | Blocks: T6
  References:
    - photos.yml verified (immich-postgres image pin + immich_server:release).
    - Immich docs: docs.immich.app/install/upgrading#migrating-to-vectorchord (librarian-cited).
    - Current recommended image: `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0`.
    - pgvecto support REMOVED in Immich v3.0.0 (June 2026) — Metis+librarian critical finding.
    - Kopia covers /backup/photos (verified — 78.8 GB snapshot exists).
  Acceptance criteria (agent-executable):
    - `test -f /home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md` exits 0.
    - `grep -q "pgvecto-rs:pg14-v0.2.0" /home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md` (documents current state) AND `grep -q "vectorchord0.4.3-pgvectors0.2.0" /home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md` (documents target).
    - `git -C /home/antoine/docker/compose log --oneline -- docs/runbooks/` shows the runbook commit.
  QA scenarios: happy: runbook renders as valid markdown; all 8 steps present; `grep -c "^## "` >= 8. failure: remove step 1 (DB dump) → review should flag (Metis gap 3 mitigation). Evidence: .omo/evidence/task-5-iskradocker-updates.md (runbook content + grep checks).
  Commit: Y | docs(runbooks): add immich pgvecto→VectorChord migration runbook

- [~] 6. Execute immich DB migration (MANUAL, user-gated)
  What to do / Must NOT do: Walk through runbook T5 step-by-step ON iskradocker. This is stateful and irreversible (cannot downgrade). Pause for user confirmation before step 5 (`up -d`). Capture before/after image digests.
  Must NOT: run unattended; skip the pg_dump; proceed if step 6 logs show errors.
  Parallelization: Wave 2 | Blocked by: T1 (git, to commit the photos.yml change), T2 (script exists for future immich auto-update), T5 (runbook) | Blocks: T7
  References:
    - photos.yml path + the new image tag from T5.
    - `.env` has DB_USERNAME, DB_PASSWORD, DB_DATABASE_NAME, DB_DATA_LOCATION for immich.
    - Rolling restart of 4 immich containers must be atomic (Metis gap 4/29).
  Acceptance criteria (agent-executable):
    - Before: `docker inspect immich_postgres --format '{{.Config.Image}}'` == `ghcr.io/tensorchord/pgvecto-rs:pg14-v0.2.0`.
    - After: `docker inspect immich_postgres --format '{{.Config.Image}}'` == `ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0`.
    - After: `curl -fsS http://127.0.0.1:2283/api/server/ping | jq -e '.res=="pong"'` exits 0.
    - After: `docker exec immich_postgres psql -U immich -d immich -tAc "SELECT extname FROM pg_extension WHERE extname='vchord';"` returns `vchord`.
    - `docker logs immich_server 2>&1 | tail -200 | grep -E "Reindexed (face|clip)_index"` exits 0.
    - `git -C /home/antoine/docker/compose diff photos.yml` shows only the image+shm_size change.
  QA scenarios: happy: immich_server comes up, ping=pong, vchord extension present, reindex logs present. failure (simulated, cannot fully test irreversibly): `docker logs immich_server` shows "extension not available" → runbook step 0 says stop + restore from Kopia; verify Kopia snapshot path `/backup/photos` restorable (`docker exec kopia kopia snapshot list /backup/photos | tail -1`). Evidence: .omo/evidence/task-6-iskradocker-updates.{txt,ndjson}.
  Commit: Y | chore(photos): migrate immich_postgres pgvecto-rs → VectorChord

- [ ] 7. Enable immich_server + immich_machine_learning + immich_redis in the cron whitelist
  What to do / Must NOT do: Edit `iskradocker-autoupdate.sh`: set `IMMICH_AUTO_UPDATE_ENABLED=1` (in `.env`, not hardcoded) and add the immich stack entry to the active STACKS array: compose/photos.yml, services=[immich_server,immich_machine_learning,immich_redis] (NOT immich_postgres — never), probe=`curl -fsS http://127.0.0.1:2283/api/server/ping | jq -e '.res=="pong"'`, snapshot_paths=`/backup/compose /backup/photos`. Run once via `systemctl start iskradocker-autoupdate.service` to validate.
  Must NOT: add `immich_postgres` to services[] ( EVER ); enable immich before T6 completes.
  Parallelization: Wave 2 | Blocked by: T6 | Blocks: F1-F4
  References:
    - photos.yml services: immich-server, immich-machine-learning, immich-redis, immich-postgres (EXCLUDE the last).
    - Probe: `api/server/ping` returns `{"res":"pong"}`.
    - VectorChord image from T6 means immich_server:release auto-updates are now safe (current Image release supports it).
  Acceptance criteria (agent-executable):
    - `grep -q 'IMMICH_AUTO_UPDATE_ENABLED' /home/antoine/docker/.env` AND value == 1.
    - `grep -q 'immich_postgres' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` → only matches in a comment or exclusion, NOT in any services[] array. (Inverse check: the immich services[] must contain exactly `immich_server immich_machine_learning immich_redis`.)
    - After `systemctl start iskradocker-autoupdate.service`: NDJSON log shows `"stack":"immich","probe_status":"ok"`.
    - `docker exec immich_postgres pg_isready -U immich` returns "accepting connections" (DB NOT recreated — Metis gap 11).
  QA scenarios: happy: immich stack pulls+recreates+probes ok. failure: stop immich_server, run script → NDJSON shows rollback + probe restored. Evidence: .omo/evidence/task-7-iskradocker-updates.ndjson.
  Commit: Y | feat(scripts): enable immich (server/ML/redis) in autoupdate whitelist

- [ ] 8. Rollback-tag GC + logging polish + notification wiring
  What to do / Must NOT do: Add to `iskradocker-autoupdate.sh`:
    (a) A GC routine at script END (after all stacks): `docker images --filter "reference=*:rollback-*" --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}'` → parse CreatedAt; `docker rmi` those older than 28 days (Metis gap 6 GC). Use `docker image rm` not `rmi -f`; skip if in use (error-tolerant).
    (b) PREREQUISITE — provision SMTP creds: add two new vars to `/home/antoine/docker/.env`: `SMTP_USER=antoine.olivier.dion@gmail.com` (the Gmail address already used by the iskra_stack Grafana on `iskraprojectcea`) and `SMTP_APP_PASSWORD=<16-char Gmail app password>` (obtain at https://myaccount.google.com/apppasswords; this is a NEW secret for iskradocker — the iskradocker `.env` currently has NO SMTP creds; verified: only `GRAFANA_PASSWORD` exists there). Verify `.gitignore` already excludes `.env*` (from T1) so this never commits.
    (c) Notification: end-of-run summary email via `curl --ssl-reqd --url 'smtp://smtp.gmail.com:587' --mail-from "$SMTP_USER" --mail-rcpt "$SMTP_USER" --user "$SMTP_USER:$SMTP_APP_PASSWORD" -T <summary_file>` (isp: smtp.gmail.com:587 is TCP-reachable from iskradocker — verified). Send on: success (summary of stacks+digests), failure+rollback (per stack), rollback-failed (CRITICAL). One batched email per run (Metis gap 7 — Gmail rate). Subject: `[iskradocker-autoupdate] <SUCCESS|PARTIAL|CRITICAL> <YYYY-MM-DD>`. The script MUST capture curl's exit code and write a line to the NDJSON log: `{"event":"notify","channel":"smtp","exit_code":<n>,"status":"sent"|"failed","ts":"<iso8601>"}` — this is the agent-executable evidence of notification (no mail.log or inbox check needed; verified: iskradocker has NO MTA — no postfix/sendmail/msmtp/mailx, no /var/log/mail.log).
    (d) Ensure NDJSON log rotation: add logrotate config `/etc/logrotate.d/iskradocker-autoupdate` (weekly, keep 12, compress).
  Must NOT: `docker image prune -a`; send >1 email per run; hardcode SMTP creds in the script (script is in git); hardcode the Gmail app password; claim credit for sending without checking curl's exit code.
  Parallelization: Wave 3 | Blocked by: T1 (git for .gitignore), T2 (script to extend) | Blocks: F1-F4
  References:
    - VERIFIED: `/home/antoine/docker/.env` currently has NO SMTP vars — only `GRAFANA_PASSWORD`. `SMTP_USER`/`SMTP_APP_PASSWORD`/`GRAFANA_SMTP_PASSWORD` all absent. Must be PROVISIONED as part of (b).
    - VERIFIED: the Gmail account `antoine.olivier.dion@gmail.com` + an app password is already used by the iskra_stack Grafana on `iskraprojectcea` (a different VM; its `.env` has `GRAFANA_SMTP_PASSWORD`). Reuse the SAME Gmail account + create/generate its app password anew for iskradocker.
    - VERIFIED: `curl 8.5.0` on iskradocker supports `smtp://`. TCP to `smtp.gmail.com:587` reachable (`</dev/tcp/smtp.gmail.com/587` succeeds).
    - VERIFIED: iskradocker has NO MTA — `which postfix sendmail msmtp mailx` all empty; `/var/log/mail.log` and `/var/log/maillog` do not exist. Therefore notification verification MUST be via the script's own NDJSON log entry, NOT a mail log or inbox.
    - NDJSON log path: /var/log/iskradocker-autoupdate.ndjson (established in T2).
  Acceptance criteria (agent-executable):
    - `source /home/antoine/docker/.env && [ -n "$SMTP_USER" ] && [ -n "$SMTP_APP_PASSWORD" ]` (creds provisioned) exits 0.
    - `git -C /home/antoine/docker/compose check-ignore /home/antoine/docker/.env` exits 0 (SMTP creds never committed — verified by T1's .gitignore).
    - `grep -q 'docker rmi\|docker image rm' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` (GC present).
    - `grep -q 'smtp.gmail.com:587' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` (notification wiring present).
    - `grep -q 'SMTP_APP_PASSWORD' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` AND `grep -c 'antoine.olivier.dion@gmail.com\|SMTP_USER' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` — the script references the env var, not a hardcoded secret; `grep -iE '[a-z0-9]{16}@gmail|password.*=.*[a-z0-9]{16}' /home/antoine/docker/scripts/iskradocker-autoupdate.sh` must return nothing (no hardcoded app password).
    - Create a fake 29-day-old rollback tag (`docker tag nginx:alpine test-rollback-$(date -d '29 days ago' +%s)`, set CreatedAt via `faketime` if available, else skip): run GC step → `docker images | grep test-rollback-` returns nothing.
    - `test -f /etc/logrotate.d/iskradocker-autoupdate` and `logrotate -d /etc/logrotate.d/iskradocker-autoupdate` (dry-run) exits 0.
    - Trigger a CRITICAL path (temporarily break a probe) then run: `grep '"status":"sent"' /var/log/iskradocker-autoupdate.ndjson | tail -1` returns a line with `"exit_code":0` AND `"channel":"smtp"` (notification sent via curl, agent-verified from the NDJSON log the script itself wrote — no mailbox/mail.log check, since iskradocker has no MTA).
  QA scenarios: happy: GC removes old tags; NDJSON shows `{"event":"notify","status":"sent","exit_code":0}`. failure: SMTP creds wrong (`SMTP_APP_PASSWORD=wrong` in a test env) → curl exits non-zero → NDJSON shows `{"event":"notify","status":"failed","exit_code":<n>}`; script still completes (non-fatal) and the update itself is untouched. Evidence: .omo/evidence/task-8-iskradocker-updates.{txt,ndjson}.
  Commit: Y | feat(scripts): add rollback-tag GC, SMTP notify (curl smtp://), log rotation

- [ ] 9. Update `~/docker/AGENTS.md` + `compose/AGENTS.md` to document the new system
  What to do / Must NOT do: Add a section to both `/home/antoine/docker/AGENTS.md` and `/home/antoine/docker/compose/AGENTS.md` documenting: the autoupdate mechanism (script path, timer schedule, whitelist, DB exclusions + pgvecto rule), the rollback procedure (`docker tag ...:rollback-<ts>`), the runbook location, and the "MUST NOT auto-update immich_postgres / nextcloud-db" rule. This mirrors the existing convention where those AGENTS.md files already document the stack layout.
  Must NOT: duplicate the runbook content; just cross-reference it.
  Parallelization: Wave 3 | Blocked by: T2 (script path to reference), T3 (timer to reference), T5 (runbook to cross-link) | Blocks: F1-F4 (docs must be done before final verification so F4 can audit the .gitignore/rules)
  References:
    - Existing docs: `/home/antoine/docker/AGENTS.md` (stack layout table) + `/home/antoine/docker/compose/AGENTS.md` (same, more detail).
    - Runbook: `/home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md`.
    - Script: `/home/antoine/docker/scripts/iskradocker-autoupdate.sh`.
    - Timer: `systemctl list-timers iskradocker-autoupdate.timer`.
  Acceptance criteria (agent-executable):
    - `grep -q 'iskradocker-autoupdate' /home/antoine/docker/AGENTS.md` AND `/home/antoine/docker/compose/AGENTS.md`.
    - `grep -q 'MUST NOT.*immich_postgres' /home/antoine/docker/compose/AGENTS.md` (exclusion documented).
    - `grep -q 'docs/runbooks/immich-pgvecto' /home/antoine/docker/AGENTS.md` (runbook referenced).
  QA scenarios: happy: both files updated, grep checks pass. failure: delete the section → grep fails → reviewer restores. Evidence: .omo/evidence/task-9-iskradocker-updates.md.
  Commit: Y | docs(agents): document iskradocker autoupdate system + DB exclusions

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — verify every todo T1-T9 is implemented as written (whitelist matches, DB exclusions enforced, no bare `docker compose up -d`, immich gating correct), every Metis gap has a mitigation, no scope creep. Tool: `grep`/`read` the script + units + runbook.
- [ ] F2. Code quality review — `shellcheck` on `iskradocker-autoupdate.sh`; `systemd-analyze verify` on units; markdown lint on runbook; no hardcoded secrets (`grep -iE 'password|token|key' iskradocker-autoupdate.sh` only matches `source .env`-style references).
- [ ] F3. Real manual QA — on iskradocker: trigger one full real run (`systemctl start iskradocker-autoupdate.service`); confirm Nextcloud, Jellyfin, (and after T7) Immich all pass their health probes; confirm rollback tags exist; confirm DB containers were NOT recreated (compare `docker inspect <db> --format '{{.State.StartedAt}}'` before/after, must be unchanged); confirm notification was sent via `grep '"status":"sent"' /var/log/iskradocker-autoupdate.ndjson | tail -1` (agent-executable — iskradocker has no MTA/mail.log, so the script's own NDJSON entry is the evidence).
- [ ] F4. Scope fidelity — Watchtower untouched; cliproxyapi untouched; only the 8 whitelisted containers managed; immich_postgres + nextcloud-db never in any services[] array; no `.env` committed to git.

## Commit strategy
- One commit per todo (T1, T2, T3, T5, T6, T7, T8, T9) in the git repo at `/home/antoine/docker/compose/` initialized in T1. T4 is operational (no commit).
- Commit messages follow the existing ProjectCEA convention: `<type>(<scope>): <summary>` (types: chore, feat, docs).
- The migration commit (T6) MUST be a single atomic commit containing only the `photos.yml` image tag change + shm_size — no other drift.
- Never commit `.env*`; the pre-commit safety check is `git -C /home/antoine/docker/compose status --porcelain | grep -E '\.env'` must be empty before every commit.

## Success criteria
1. `systemctl list-timers iskradocker-autoupdate.timer` shows next Sun 04:00 America/Toronto trigger.
2. A real end-to-end run updates Nextcloud + Jellyfin cleanly, leaves Immich untouched until T7, and `docker exec nextcloud-db pg_isready` + `docker exec immich_postgres pg_isready` both return "accepting connections" with UNCHANGED `StartedAt` (DBs not recreated).
3. Health probes pass for all updated services (status.php / health / ping per service).
4. Rollback tags (`<image>:rollback-<ts>`) exist for every updated service and are GC'd after 28 days.
5. The immich pgvecto→VectorChord migration (T6) succeeds; immich joins the whitelist (T7) only after.
6. A notification email is sent on every run (success/partial/critical).
7. `/home/antoine/docker/compose/` is a git repo; `git log --oneline` shows the per-todo commits; no secret file is tracked.
8. Watchtower + cliproxyapi remain exactly as they were (untouched).
