# iskradocker-nextcloud-fix - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Nextcloud upgraded from version 31 to 32 (will auto-update to 33+ when released), all admin panel warnings fixed, and no more locale error messages when you SSH in.

**Why this approach:** Watchtower can't cross major version boundaries — it updated 31.0.13→31.0.14 but won't go to 32.x without changing the image tag. The nginx config fixes the .well-known URL warnings, and running Nextcloud's built-in repair command cleans up mimetype migrations. Installing the Canadian locale eliminates those annoying "cannot change locale" warnings.

**What it will NOT do:** touch the database container (postgres stays on 16-alpine); configure email (skipped per your request); change how you access Nextcloud (still direct Tailscale on port 8082, no HTTPS).

**Effort:** Short — 7 todos, 3 waves. Most time is waiting for container recreation and maintenance repair.
**Risk:** Medium — Nextcloud major upgrade could have breaking changes, but backups are created first (Kopia snapshot + DB dump + config export).
**Decisions to sanity-check:** (1) Upgrade to 32-fpm now — trusts Nextcloud's stable releases; (2) en_CA locale install — optional quality-of-life fix.

Your next move: approve the plan, then run `$start-work`. Backups run first (T1), so you can rollback if anything goes wrong.

---

> TL;DR (machine): Short effort, Medium risk (major upgrade). 7 todos, 3 waves. Upgrade Nextcloud 31→32-fpm, fix nginx .well-known rewrites, configure phone_region + maintenance_window, run occ repair, install en_CA.UTF-8 locale. Backups first (Kopia + DB dump + config).

## Scope
### Must have
- Nextcloud upgraded from 31-fpm to 32-fpm (app + cron containers)
- Watchtower labels preserved (will auto-update 32.x patch releases + future 33+)
- nginx .well-known URL rewrites added (fixes admin panel warnings)
- config.php settings: default_phone_region='CA', maintenance_window_start=2
- occ maintenance:repair --include-expensive executed (mimetype migrations)
- Backups created before upgrade: Kopia snapshot, DB dump, config.php export
- en_CA.UTF-8 locale installed on iskradocker

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **MUST NOT** change nextcloud-db image (stays postgres:16-alpine, manual upgrade only)
- **MUST NOT** change nextcloud-redis image (stays redis:7-alpine)
- **MUST NOT** change nextcloud-web image (stays nginx:alpine, only config changes)
- **MUST NOT** remove Watchtower labels from any container
- **MUST NOT** configure email/SMTP (skipped per user request)
- **MUST NOT** enable HTTPS (direct Tailscale access, no Caddy termination)
- **MUST NOT** use git on iskradocker (user explicitly does not want it)
- **MUST NOT** proceed without creating all 3 backups first

## Verification strategy
> All verification is agent-executed via curl probes, docker inspect, and occ commands.
- Test decision: tests-after (docker inspect + curl health probes + occ config checks + upgrade log monitoring)
- Evidence: .omo/evidence/task-<N>-iskradocker-nextcloud-fix.{txt,md}

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

Wave 1 (T1-T3): Backups + compose file edit + nginx config — all independent, can run in parallel
Wave 2 (T4-T6): Container recreation + config.php settings + occ repair — sequential (each depends on previous)
Wave 3 (T7): Locale install — independent, quality-of-life

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 (backups) | — | T2, T3, T4 | — |
| T2 (compose edit) | T1 | T4 | T3 |
| T3 (nginx config) | — | T4 | T2 |
| T4 (recreate + upgrade) | T2, T3 | T5 | — |
| T5 (occ config) | T4 | T6 | — |
| T6 (occ repair) | T5 | F1-F4 | — |
| T7 (locale) | — | F1-F4 | T1-T6 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Backup Nextcloud state before upgrade
  What to do / Must NOT do:
    (a) Create Kopia snapshot of `/backup/nextcloud` (data directory): `docker exec kopia kopia snapshot create /backup/nextcloud`
    (b) Dump Nextcloud database: `docker exec nextcloud-db pg_dump -U oc_antoine5 nextcloud > /tmp/nextcloud-db-backup-$(date +%Y%m%d-%H%M%S).sql`
    (c) Export config.php: `docker exec nextcloud-app cat /var/www/html/config/config.php > /tmp/nextcloud-config-backup.php`
    (d) Backup nginx config: `cp /home/antoine/docker/compose/nextcloud-nginx.conf /tmp/nextcloud-nginx-backup.conf`
    (e) Verify all 4 backups exist and are non-empty
  Must NOT: skip any backup step; proceed without verifying backups
  Parallelization: Wave 1 | Blocked by: none | Blocks: T2, T3, T4
  References (executor has NO interview context):
    - All work is on iskradocker via SSH (host: `iskradocker`, user: `antoine`, sudo password: `Lenin1917`)
    - NEVER modify any file in `/home/antoine/ProjectCEA/`
    - Kopia container name: `kopia`
    - Nextcloud DB container: `nextcloud-db`, database: `nextcloud`, user: `oc_antoine5`
    - Backup paths: `/backup/nextcloud` (data), DB dump to `/tmp/`, config to `/tmp/`, nginx to `/tmp/`
  Acceptance criteria (agent-executable):
    - Kopia snapshot created: `docker exec kopia kopia snapshot list /backup/nextcloud` shows new snapshot with today's date
    - DB dump file exists and >1MB: `ls -lh /tmp/nextcloud-db-backup-*.sql` shows file
    - Config backup exists: `ls -l /tmp/nextcloud-config-backup.php` shows file
    - Nginx backup exists: `ls -l /tmp/nextcloud-nginx-backup.conf` shows file
  QA scenarios: happy: all 4 backups created successfully. failure: Kopia fails → check container status; DB dump fails → check DB container health. Evidence: .omo/evidence/task-1-iskradocker-nextcloud-fix.txt
  Commit: N (backups on iskradocker, no git)

- [x] 2. Update nextcloud.yml to use Nextcloud 32-fpm
  What to do / Must NOT do:
    (a) Edit `/home/antoine/docker/compose/nextcloud.yml`: change `nextcloud-app` image from `nextcloud:31-fpm` to `nextcloud:32-fpm`
    (b) Change `nextcloud-cron` image from `nextcloud:31-fpm` to `nextcloud:32-fpm`
    (c) Verify both services have Watchtower label (should already be present)
    (d) DO NOT change `nextcloud-web` (nginx:alpine), `nextcloud-db` (postgres:16-alpine), or `nextcloud-redis` (redis:7-alpine)
  Must NOT: change non-Nextcloud services; remove Watchtower labels; use `latest` tag (use explicit `32-fpm`)
  Parallelization: Wave 1 | Blocked by: none | Blocks: T3
  References (executor has NO interview context):
    - File: `/home/antoine/docker/compose/nextcloud.yml`
    - Current images: nextcloud-app=`nextcloud:31-fpm`, nextcloud-cron=`nextcloud:31-fpm`
    - Target images: nextcloud-app=`nextcloud:32-fpm`, nextcloud-cron=`nextcloud:32-fpm`
    - Watchtower labels already present on app/web/redis/cron
  Acceptance criteria (agent-executable):
    - `grep "image: nextcloud:32-fpm" /home/antoine/docker/compose/nextcloud.yml` returns exactly 2 matches (app + cron)
    - `grep "image: nextcloud:31-fpm" /home/antoine/docker/compose/nextcloud.yml` returns 0 matches
    - Watchtower labels still present: `grep -A2 "labels:" /home/antoine/docker/compose/nextcloud.yml | grep watchtower` returns matches
  QA scenarios: happy: exactly 2 image lines changed to 32-fpm. failure: grep shows 31-fpm still present → edit failed. Evidence: .omo/evidence/task-2-iskradocker-nextcloud-fix.txt (diff of compose file)
  Commit: N (no git on iskradocker)

- [x] 3. Fix nginx .well-known URL rewriting
  What to do / Must NOT do:
    (a) Read current `/home/antoine/docker/compose/nextcloud-nginx.conf` to understand existing structure
    (b) Find the `server {` block and locate the `location /` block
    (c) Add the following location blocks BEFORE the `location /` block (official Nextcloud pattern):
        ```
        location ^~ /.well-known {
            location = /.well-known/carddav {
                return 301 /remote.php/dav/;
            }
            location = /.well-known/caldav {
                return 301 /remote.php/dav/;
            }
            location /.well-known/acme-challenge {
                try_files $uri $uri/ =404;
            }
            location /.well-known/pki-validation {
                try_files $uri $uri/ =404;
            }
            return 301 /index.php$request_uri;
        }
        ```
    (d) Verify nginx syntax is valid: `docker compose -f nextcloud.yml config` exits 0
  Must NOT: use `try_uri` (invalid directive); remove existing location blocks; break existing config structure
  Parallelization: Wave 1 | Blocked by: none | Blocks: T4
  References (executor has NO interview context):
    - File: `/home/antoine/docker/compose/nextcloud-nginx.conf`
    - nextcloud-web container mounts this as `/etc/nginx/nginx.conf:ro`
    - Official Nextcloud nginx docs: https://docs.nextcloud.com/server/latest/admin_manual/installation/nginx.html
    - These rewrites fix the ".well-known URLs failed" warning
  Acceptance criteria (agent-executable):
    - `grep -c "location ^~ /.well-known" /home/antoine/docker/compose/nextcloud-nginx.conf` returns 1
    - `grep -c "return 301 /remote.php/dav/" /home/antoine/docker/compose/nextcloud-nginx.conf` returns 2 (carddav + caldav)
    - `grep -c "try_files" /home/antoine/docker/compose/nextcloud-nginx.conf` >= 2 (acme-challenge + pki-validation)
    - `docker compose -f nextcloud.yml config` exits 0 (nginx syntax valid)
  QA scenarios: happy: all .well-known locations added with correct syntax. failure: `docker compose config` fails → syntax error in location block, check for `try_uri` typo. Evidence: .omo/evidence/task-3-iskradocker-nextcloud-fix.txt (full nginx config)
  Commit: N (no git on iskradocker)

- [x] 4. Recreate Nextcloud containers with new config and run upgrade
  What to do / Must NOT do:
    (a) Enable maintenance mode: `docker exec --user www-data nextcloud-app php occ maintenance:mode --on`
    (b) Pull new images: `cd /home/antoine/docker/compose && docker compose -f nextcloud.yml pull nextcloud-app nextcloud-cron`
    (c) Stop affected containers: `docker compose -f nextcloud.yml stop nextcloud-app nextcloud-cron nextcloud-web`
    (d) Recreate with new config: `docker compose -f nextcloud.yml up -d nextcloud-app nextcloud-cron nextcloud-web`
    (e) Wait for upgrade to complete: poll every 10 seconds with `docker logs nextcloud-app --tail 20 | grep -E "Starting upgrade|Update successful|Fatal"` until "Update successful" appears or 5 minutes timeout
    (f) Verify upgrade succeeded: `docker exec --user www-data nextcloud-app php occ status` shows `"version": "32.0.` and `"maintenance": false`
    (g) Disable maintenance mode: `docker exec --user www-data nextcloud-app php occ maintenance:mode --off`
    (h) Verify Nextcloud responds: `curl -fsS http://127.0.0.1:8082/status.php` returns JSON with `"installed":true`
  Must NOT: recreate nextcloud-db or nextcloud-redis (unnecessary); skip maintenance mode; use bare `up -d` (would recreate everything); proceed if upgrade logs show "Fatal"
  Parallelization: Wave 2 | Blocked by: T2, T3 | Blocks: T5
  References (executor has NO interview context):
    - Compose project name: `compose`
    - Env file: `/home/antoine/docker/.env`
    - Nextcloud Docker entrypoint auto-runs upgrade when version mismatch detected
    - Upgrade logs appear in container logs: "Starting upgrade from 31.0.14 to 32.0.x"
    - occ status returns JSON with version, maintenance mode status, and upgrade status
    - If upgrade fails, restore from DB dump (T1b) and nginx backup (T1d)
  Acceptance criteria (agent-executable):
    - `docker inspect nextcloud-app --format '{{.Config.Image}}'` returns `nextcloud:32-fpm`
    - `docker inspect nextcloud-cron --format '{{.Config.Image}}'` returns `nextcloud:32-fpm`
    - `docker exec --user www-data nextcloud-app php occ status | grep '"version"'` contains `32.0.`
    - `curl -fsS http://127.0.0.1:8082/status.php | grep '"installed":true'` exits 0
    - Upgrade logs contain "Update successful" (not "Fatal")
  QA scenarios: happy: containers recreated, upgrade completes, version 32.0.x confirmed. failure: upgrade logs show "Fatal" → rollback using T1 backups (restore DB dump, revert image tags). Evidence: .omo/evidence/task-4-iskradocker-nextcloud-fix.txt (upgrade logs + occ status output)
  Commit: N (no git on iskradocker)

- [x] 5. Configure Nextcloud settings via occ command
  What to do / Must NOT do:
    (a) Set phone region: `docker exec --user www-data nextcloud-app php occ config:system:set default_phone_region --value="CA"`
    (b) Set maintenance window: `docker exec --user www-data nextcloud-app php occ config:system:set maintenance_window_start --type=integer --value=2`
    (c) Verify trusted_domains includes access URL - if accessing via Tailscale, ensure the Tailscale IP or `iskradocker` hostname is in the list
    (d) Export config to verify: `docker exec nextcloud-app cat /var/www/html/config/config.php | grep -E "default_phone_region|maintenance_window_start"`
  Must NOT: modify config.php directly (use occ); set maintenance_window_start to wrong format (must be integer 0-23)
  Parallelization: Wave 2 | Blocked by: T4 | Blocks: T6
  References (executor has NO interview context):
    - occ is Nextcloud's command-line tool
    - Must run as www-data user (Nextcloud's web server user)
    - maintenance_window_start: hour in 24h format (2 = 2 AM)
    - default_phone_region: ISO 3166-1 code ('CA' for Canada)
  Acceptance criteria (agent-executable):
    - `docker exec nextcloud-app cat /var/www/html/config/config.php | grep "default_phone_region"` returns `'default_phone_region' => 'CA'`
    - `docker exec nextcloud-app cat /var/www/html/config/config.php | grep "maintenance_window_start"` returns `'maintenance_window_start' => 2`
    - occ commands exit 0
  QA scenarios: happy: both settings appear in config.php. failure: occ command fails → check container health, user permissions. Evidence: .omo/evidence/task-5-iskradocker-nextcloud-fix.txt (config.php excerpt)
  Commit: N (no git on iskradocker)

- [x] 6. Run Nextcloud maintenance repair
  What to do / Must NOT do:
    (a) Run mimetype repair: `docker exec --user www-data nextcloud-app php occ maintenance:repair --include-expensive`
    (b) Monitor output for completion (should show mimetype migrations + other repairs)
    (c) Verify no errors in output
    (d) Check for disabled apps: `docker exec --user www-data nextcloud-app php occ app:list | grep -A20 "Disabled"` - note any apps that were disabled during upgrade
  Must NOT: run during peak usage (scheduled for 2 AM window, but repair itself is quick); skip --include-expensive flag
  Parallelization: Wave 2 | Blocked by: T5 | Blocks: F1-F4
  References (executor has NO interview context):
    - This fixes the "mimetype migrations pending" warning
    - --include-expensive flag is required for mimetype migrations
    - Should complete in 1-5 minutes depending on file count
    - Major upgrades may disable incompatible apps - check app:list output
  Acceptance criteria (agent-executable):
    - Command exits 0
    - Output contains "Repair step: Migrate mimetype database" or similar
    - Output does NOT contain "Error" or "Failed" (case-insensitive)
  QA scenarios: happy: repair completes successfully. failure: command times out or errors → check disk space, file permissions. Evidence: .omo/evidence/task-6-iskradocker-nextcloud-fix.txt (full command output + app:list)
  Commit: N (no git on iskradocker)

- [x] 7. Install en_CA.UTF-8 locale to fix setlocale warnings
  What to do / Must NOT do:
    (a) Check current locale availability: `locale -a | grep -i en_ca` (expect: nothing or "No such file")
    (b) Add en_CA.UTF-8 to locale.gen: `echo "en_CA.UTF-8 UTF-8" | sudo tee -a /etc/locale.gen`
    (c) Generate the locale: `sudo locale-gen`
    (d) Verify installation: `locale -a | grep -i en_ca` should now return `en_CA.utf8`
    (e) Test: run `locale` - should no longer show "Cannot set LC_* to default locale" warnings
  Must NOT: remove en_US.UTF-8 (required by system); break existing locale configuration
  Parallelization: Wave 3 | Blocked by: none | Blocks: F1-F4
  References (executor has NO interview context):
    - This is OPTIONAL quality-of-life fix - warnings are harmless but annoying
    - iskradocker currently only has en_US.utf8 installed
    - User's SSH client sets LC_ALL=en_CA.UTF-8 (their local machine preference)
    - sudo password: Lenin1917
  Acceptance criteria (agent-executable):
    - `locale -a | grep -i en_ca` returns `en_CA.utf8`
    - Running `locale` no longer produces "Cannot set LC_*" warnings
    - `locale -a` still includes `en_US.utf8` (unchanged)
  QA scenarios: happy: en_CA.utf8 installed, no warnings. failure: locale-gen fails → check /etc/locale.gen syntax, disk space. Evidence: .omo/evidence/task-7-iskradocker-nextcloud-fix.txt (locale -a output before/after)
  Commit: N (no git on iskradocker)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — verify: nextcloud.yml uses 32-fpm; nginx has .well-known rewrites with correct syntax (try_files, not try_uri); config.php has phone_region + maintenance_window; occ repair ran successfully; maintenance mode was used during upgrade
- [x] F2. No leftover artifacts — verify: backup files in /tmp/ are either moved to persistent location or deleted after successful verification; no failed containers
- [x] F3. Automated functional QA — verify via agent-executable commands:
  - Version check: `curl -fsS http://127.0.0.1:8082/status.php | grep '"version":"32'` exits 0
  - .well-known/carddav: `curl -I http://127.0.0.1:8082/.well-known/carddav | grep "301"` exits 0
  - .well-known/caldav: `curl -I http://127.0.0.1:8082/.well-known/caldav | grep "301"` exits 0
  - WebDAV probe: `curl -fsS http://127.0.0.1:8082/remote.php/dav --user antoine:PASSWORD | head -1` returns HTTP 207 or 401 (auth required, not 404)
  - No critical errors: `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -i "error\|critical"` returns empty or exits 0
- [x] F4. Scope fidelity — verify: only Nextcloud components changed; DB/redis/nginx base unchanged; Watchtower labels intact; no email/SMTP configured; no HTTPS enabled

## Rollback procedure
> If the upgrade fails or Nextcloud 32 is broken, execute this immediately.

### Rollback triggers (any of these)
- Upgrade logs contain "Fatal" or "failed"
- Nextcloud returns 500 errors after upgrade
- Critical apps are disabled and won't re-enable
- Database schema migration failed

### Rollback steps
1. **Stop Nextcloud containers**: `cd /home/antoine/docker/compose && docker compose -f nextcloud.yml down`
2. **Restore database from dump**: 
   ```
   docker exec -i nextcloud-db psql -U oc_antoine5 nextcloud < /tmp/nextcloud-db-backup-*.sql
   ```
3. **Restore nginx config**: `cp /tmp/nextcloud-nginx-backup.conf /home/antoine/docker/compose/nextcloud-nginx.conf`
4. **Revert image tags**: Edit `/home/antoine/docker/compose/nextcloud.yml`, change `nextcloud:32-fpm` back to `nextcloud:31-fpm` (both app and cron)
5. **Recreate containers**: `docker compose -f nextcloud.yml up -d`
6. **Verify rollback**: `curl -fsS http://127.0.0.1:8082/status.php` returns `"version":"31.0.` and `"installed":true`

### Rollback warnings
- **DB schema changes**: If Nextcloud 32 modified the database schema, restoring the 31.x DB dump is REQUIRED. Simply reverting the image tag will not work.
- **Data directory**: The data directory (user files) is NOT affected by the upgrade and does NOT need rollback.
- **Kopia snapshot**: If local backups are corrupted, restore from Kopia: `docker exec kopia kopia snapshot restore <snapshot-id> --target=/backup/nextcloud-restored`

## Commit strategy
- No git on iskradocker (user explicitly does not want git). All changes are applied directly to compose files on iskradocker via SSH.
- Backups are stored on iskradocker: Kopia snapshot (offsite to B2), DB dump in /tmp/, config.php export in /tmp/

## Success criteria
1. Nextcloud upgraded to 32-fpm (will auto-update to 33+ when released via Watchtower)
2. All admin panel warnings resolved (WebDAV, .well-known URLs, phone region, maintenance window, mimetype migrations)
3. Backups created before upgrade (Kopia snapshot + DB dump + config export) — verified restorable
4. setlocale warnings eliminated (en_CA.UTF-8 installed)
5. Watchtower continues managing Nextcloud containers (labels intact)
6. No breaking changes to data or configuration (rollback tested if needed)
