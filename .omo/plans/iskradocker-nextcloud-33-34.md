# iskradocker-nextcloud-33-34 - Work Plan

## TL;DR (For humans)

**What you'll get:** Nextcloud upgraded from version 32 to 33, then to 34. All fixable admin warnings resolved (WebDAV, 2FA, DB indices, AppAPI, logs). Three warnings remain intentionally (HTTPS, heartbeat headers, email) — these are architectural limitations of the Tailscale-only setup and are documented.

**Why this approach:** Two major version upgrades in sequence with a pause gate after 33 so you can verify stability before jumping to 34. The fixable issues are solved via `occ` commands (set internal URL, install 2FA app, add DB indices, suppress unused AppAPI). The three remaining warnings (HTTPS, heartbeat, email) are architectural — they require Caddy/HTTPS or SMTP credentials that you intentionally don't use.

**What it will NOT do:** enable HTTPS (Tailscale encrypts the tunnel, TLS at container level is unnecessary); configure email (you said skip before); fix the heartbeat security header check (requires HTTPS endpoint to verify); downgrade to 31 if something breaks (you have backups + rollback procedure).

**Effort:** Medium — 11 todos, 5 waves. Each major upgrade takes ~5-10 minutes with verification.
**Risk:** High — two major version upgrades back-to-back. Backups before each upgrade and pause gate after 33 mitigate risk.
**Decisions to sanity-check:** (1) Pause after 33 — you'll be asked "Want to go to 34?" before T9 runs. (2) three accepted warnings documented in AGENTS.md. (3) 2FA app installed but not enforced — you configure your authenticator in the UI if you want it.

Your next move: approve the plan, then `$start-work`. Wave 1 (backups) runs immediately. Wave 2 (32→33) follows. Then you'll be asked before 33→34.

---

> TL;DR (machine): Medium effort, High risk. 11 todos, 5 waves. Upgrade 32→33→34 with pause gate. Fix WebDAV, 2FA, DB indices, AppAPI, logs. Three accepted warnings (HTTPS, heartbeat, email). Backups before each upgrade.

## Scope
### Must have
- Upgrade Nextcloud 32→33 with full backup + verification
- Pause gate with user confirmation before 33→34
- Upgrade Nextcloud 33→34 with full backup + verification
- Fix WebDAV self-connectivity (set internal URL)
- Install twofactor_totp 2FA provider (suppress "no provider" warning)
- Add missing DB indices (`occ db:add-missing-indices`)
- Suppress AppAPI deploy daemon warning (disable unused app_api app)
- Check and clear post-upgrade logs
- Update AGENTS.md with current version and accepted warnings
- Only accepted warnings remain: HTTPS, heartbeat headers, email

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **MUST NOT** enable HTTPS or TLS (Tailscale-only, no Caddy)
- **MUST NOT** configure SMTP/email (user explicitly said skip)
- **MUST NOT** configure a real AppAPI deploy daemon (unused feature)
- **MUST NOT** change nextcloud-db/redis/web images (manual-only)
- **MUST NOT** enforce 2FA for user accounts (provide app only, user enables in UI)
- **MUST NOT** skip backup before any upgrade
- **MUST NOT** proceed to 34 without user confirmation after 33
- **MUST NOT** use git on iskradocker

## Verification strategy
> All verification is agent-executed via occ setupchecks, curl probes, and docker inspect.
- Test decision: tests-after (occ setupchecks + curl health probes)
- Evidence: .omo/evidence/task-<N>-iskradocker-nextcloud-33-34.{txt,md}

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

Wave 1 (T1): Backups before 33 upgrade
Wave 2 (T2): Upgrade 32→33
Wave 3 (T3-T7): Fix all post-33 issues (WebDAV, 2FA, DB indices, AppAPI, logs)
Wave 4 (T9): Upgrade 33→34 (only if T8 user confirmation)
Wave 5 (T10-T11): Fix post-34 issues + update AGENTS.md docs

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 (backups) | — | T2 | — |
| T2 (upgrade 32→33) | T1 | T3-T7 | — |
| T3 (WebDAV) | T2 | F3 | T4-T7 |
| T4 (2FA) | T2 | F3 | T3,T5-T7 |
| T5 (DB indices) | T2 | F3 | T3-T4,T6-T7 |
| T6 (AppAPI) | T2 | F3 | T3-T5,T7 |
| T7 (logs) | T2 | F3 | T3-T6 |
| T8 (pause/user conf) | T3-T7 | T9 | — |
| T9 (upgrade 33→34) | T8 | T10-T11 | — |
| T10 (post-34 fixes) | T9 | F1-F4 | T11 |
| T11 (AGENTS.md) | T10 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. Backup Nextcloud before 33 upgrade
  What to do / Must NOT do:
    (a) Create Kopia snapshot: `docker exec kopia kopia snapshot create /backup/nextcloud`
    (b) Dump database: `docker exec nextcloud-db pg_dump -U oc_antoine5 nextcloud > /tmp/nextcloud-db-backup-33-$(date +%Y%m%d-%H%M%S).sql`
    (c) Export config.php: `docker exec nextcloud-app cat /var/www/html/config/config.php > /tmp/nextcloud-config-backup-33.php`
    (d) Export app list: `docker exec --user www-data nextcloud-app php occ app:list > /tmp/nextcloud-apps-32.txt`
    (e) Verify all 4 backups exist and are non-empty
  Must NOT: skip any backup; proceed without verification
  Parallelization: Wave 1 | Blocked by: none | Blocks: T2
  References (executor has NO interview context):
    - All work on iskradocker via SSH (host: `iskradocker`, user: `antoine`, sudo password: `Lenin1917`)
    - NEVER modify files in `/home/antoine/ProjectCEA/`
    - DB user: `oc_antoine5`, DB name: `nextcloud`
    - /tmp/ backups expected: DB dump >1MB, config ~1KB, apps list ~1KB
  Acceptance criteria (agent-executable):
    - Kopia snapshot list shows new entry for `/backup/nextcloud`
    - `ls -lh /tmp/nextcloud-*-33-*` shows 3 files (DB, config, apps)
    - All files non-zero size
  QA scenarios: happy: all backups created. failure: Kopia not running → check docker; DB dump fails → pg_isready check. Evidence: .omo/evidence/task-1-iskradocker-nextcloud-33-34.txt
  Commit: N (no git on iskradocker)

- [x] 2. Upgrade Nextcloud 32→33
  What to do / Must NOT do:
    (a) Edit `/home/antoine/docker/compose/nextcloud.yml`: change `nextcloud:32-fpm` → `nextcloud:33-fpm` (both app + cron)
    (b) Enable maintenance mode: `docker exec --user www-data nextcloud-app php occ maintenance:mode --on`
    (c) Pull new images: `cd /home/antoine/docker/compose && docker compose -f nextcloud.yml pull nextcloud-app nextcloud-cron`
    (d) Stop containers: `docker compose -f nextcloud.yml stop nextcloud-app nextcloud-cron nextcloud-web`
    (e) Recreate: `docker compose -f nextcloud.yml up -d nextcloud-app nextcloud-cron nextcloud-web`
    (f) Poll logs for upgrade completion: every 10s `docker logs nextcloud-app --tail 20 | grep -E "Starting upgrade|Update successful|Fatal"` until "Update successful" or 5min timeout
    (g) Verify: `docker exec --user www-data nextcloud-app php occ status` shows `"version": "33.0.` and `"maintenance": false`
    (h) Disable maintenance mode: `docker exec --user www-data nextcloud-app php occ maintenance:mode --off`
    (i) Verify HTTP: `curl -fsS http://127.0.0.1:8082/status.php | grep '"installed":true'` exits 0
  Must NOT: recreate nextcloud-db/redis; skip maintenance mode; proceed if "Fatal" in logs
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: T3, T4, T5, T6, T7, T8
  References (executor has NO interview context):
    - Nextcloud Docker entrypoint auto-detects version mismatch and runs upgrade on first boot
  Acceptance criteria (agent-executable):
    - `docker inspect nextcloud-app --format '{{.Config.Image}}'` == `nextcloud:33-fpm`
    - `docker inspect nextcloud-cron --format '{{.Config.Image}}'` == `nextcloud:33-fpm`
    - `docker exec --user www-data nextcloud-app php occ status | grep '"version"'` contains `33.0.`
    - Upgrade logs contain "Update successful" (not "Fatal")
  QA scenarios: happy: 33 confirmed, no errors. failure: Fatal in logs → rollback (T1 backups → revert image tags). Evidence: .omo/evidence/task-2-iskradocker-nextcloud-33-34.txt (logs + occ status)
  Commit: N

- [x] 3. Fix WebDAV self-connectivity
  What to do / Must NOT do:
    (a) Add `nextcloud-web` to trusted_domains:
        `docker exec --user www-data nextcloud-app php occ config:system:set trusted_domains 4 --value="nextcloud-web"`
    (b) Set overwrite.cli.url to internal address:
        `docker exec --user www-data nextcloud-app php occ config:system:set overwrite.cli.url --value="http://nextcloud-web"`
    (c) Verify: `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -A3 "WebDAV"` must NOT show "Your web server is not yet properly set up"
  Must NOT: break external access; change overwriteprotocol to https
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: F3
  References (executor has NO interview context):
    - Root cause: container can't reach itself at external IP 192.168.1.77:8082
    - nginx container `nextcloud-web` proxies to `nextcloud-app` on port 9000
  Acceptance criteria (agent-executable):
    - `docker exec nextcloud-app cat /var/www/html/config/config.php | grep "nextcloud-web"` exists
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "Your web server is not yet properly set up"` == 0
  QA scenarios: happy: setupchecks no longer shows WebDAV error. failure: still broken → try `http://localhost` instead. Evidence: .omo/evidence/task-3-iskradocker-nextcloud-33-34.txt
  Commit: N

- [~] 4. Install 2FA provider
  What to do / Must NOT do:
    (a) Check if `twofactor_totp` app is available:
        `docker exec --user www-data nextcloud-app phpocc app:install twofactor_totp`
    (b) If already installed and disabled: `docker exec --user www-data nextcloud-app php occ app:enable twofactor_totp`
    (c) Verify: `docker exec --user www-data nextcloud-app php occ app:list | grep twofactor_totp` shows "Enabled"
    (d) Confirm warning gone: `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "Second factor"` == 0
  Must NOT: require user to configure authenticator (UI step); install untrusted apps
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: F3
  References (executor has NO interview context):
    - Nextcloud bundles `twofactor_totp` as a trusted security app
    - Enabling the app removes the "no provider" warning
    - User can later set up TOTP in Personal Settings → Security
  Acceptance criteria (agent-executable):
    - `docker exec --user www-data nextcloud-app php occ app:list | grep twofactor_totp` contains "Enabled"
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep "Second factor"` returns empty
  QA scenarios: happy: app enabled, warning gone. failure: app not found in app store → check NC 33 compat. Evidence: .omo/evidence/task-4-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 5. Add missing database indices
  What to do / Must NOT do:
    (a) Run: `docker exec --user www-data nextcloud-app php occ db:add-missing-indices`
    (b) Monitor for completion (may take 30-120 seconds on large tables)
    (c) Verify: `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "missing optional indices"` == 0
  Must NOT: skip if warning persists (may need to run twice on very large instances); run during peak usage
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: F3
  References (executor has NO interview context):
    - Missing indices: "properties_name_path_user" (table "properties"), "calobjects_by_uid_index" (table "calendarobjects"), "activity_object_user" (table "activity")
    - These are OPTIONAL indices — adding them improves query speed but is not blocking
  Acceptance criteria (agent-executable):
    - Command exits 0
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep "missing optional indices"` returns empty
  QA scenarios: happy: indices added, warning gone. failure: timeout on large table → retry with `--no-interaction`. Evidence: .omo/evidence/task-5-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 6. Suppress AppAPI deploy daemon warning
  What to do / Must NOT do:
    (a) Check if `app_api` app is enabled: `docker exec --user www-data nextcloud-app php occ app:list | grep app_api`
    (b) If warning persists, disable app_api (simplest fix): `docker exec --user www-data nextcloud-app php occ app:disable app_api`
    (c) Verify: `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "AppAPI deploy daemon"` == 0
  Must NOT: configure a real Ex-App deploy daemon (complex, not needed)
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: F3
  References (executor has NO interview context):
    - AppAPI is for installing "External Apps" (Ex-Apps) — not used in this setup
    - Disabling the app suppresses the warning
  Acceptance criteria (agent-executable):
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep "AppAPI deploy daemon"` returns empty
  QA scenarios: happy: warning suppressed. failure: app_api not listed → check NC 33 bundled apps. Evidence: .omo/evidence/task-6-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 7. Check and clear logs
  What to do / Must NOT do:
    (a) Check recent logs: `docker exec --user www-data nextcloud-app php occ log:tail 50`
    (b) If logs contain only benign post-upgrade entries (version mismatch, reindex), clear old logs:
        `docker exec --user www-data nextcloud-app php occ log:manage --level 2` (sets to warning level)
    (c) If real errors present (not upgrade-related), capture and escalate to user
    (d) Verify admin panel shows 0 errors:
        `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "error"` == 0 (excluding accepted issues)
  Must NOT: clear logs without reviewing them; hide genuinely critical errors
  Parallelization: Wave 3 | Blocked by: T2 | Blocks: F3
  References (executor has NO interview context):
    - Log level 2 = warnings; level 3 = errors
    - Post-upgrade logs often contain benign "starting upgrade" / "new version" entries
    - User reported "1 warning in the logs since June 15, 2026"
  Acceptance criteria (agent-executable):
    - `docker exec --user www-data nextcloud-app php occ log:tail 5` shows no critical errors
    - Admin panel log warning cleared (if was benign)
  QA scenarios: happy: logs clean. failure: actual errors → capture for user review. Evidence: .omo/evidence/task-7-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 8. Pause — verify 33 is stable before 34
  What to do / Must NOT do:
    (a) Run full verification: `curl -fsS http://127.0.0.1:8082/status.php` → `"installed":true`
    (b) Check all post-33 fixes applied: `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep "✗"` (should show only accepted issues: HTTPS, heartbeat, email)
    (c) Confirm 2FA app installed, DB indices added, WebDAV fixed
    (d) Wait for USER CONFIRMATION before proceeding to Nextcloud 34 upgrade
  Must NOT: skip user confirmation; upgrade to 34 before 33 is verified stable
  Parallelization: Wave 3 | Blocked by: T3-T7 | Blocks: T9
  References (executor has NO interview context):
    - This is a deliberate pause point — the user must confirm they want to proceed to v34
    - If 33 has issues, fix them before proceeding
  Acceptance criteria (agent-executable):
    - `curl -fsS http://127.0.0.1:8082/status.php` exits 0
    - All fixable setupchecks resolved (only HTTPS/heartbeat/email remain)
    - PAUSE: Worker must ask user "Nextcloud 33 is ready. Proceed to 34?"
  QA scenarios: happy: user confirms. failure: user says no → stop after 33, do not mark T9-T10. Evidence: .omo/evidence/task-8-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 9. Upgrade Nextcloud 33→34
  What to do / Must NOT do:
    (a) Re-use T1 backups (or create new ones if more than 24h old):
        `docker exec nextcloud-db pg_dump -U oc_antoine5 nextcloud > /tmp/nextcloud-db-backup-34-$(date +%Y%m%d-%H%M%S).sql`
    (b) Edit `/home/antoine/docker/compose/nextcloud.yml`: change `nextcloud:33-fpm` → `nextcloud:34-fpm` (app + cron)
    (c) Enable maintenance mode, pull images, stop, recreate (same pattern as T2)
    (d) Poll logs for "Update successful"
    (e) Verify: `docker exec --user www-data nextcloud-app php occ status` shows `"version": "34.0.`
    (f) Disable maintenance mode, verify HTTP response
  Must NOT: recreate DB/redis; skip backups; proceed without verification
  Parallelization: Wave 4 | Blocked by: T8 (user conf) | Blocks: T10, T11
  References (executor has NO interview context):
    - Same upgrade pattern as T2 (32→33)
    - NC 34 may have different migration paths — monitor logs closely
  Acceptance criteria (agent-executable):
    - `docker inspect nextcloud-app --format '{{.Config.Image}}'` == `nextcloud:34-fpm`
    - `docker exec --user www-data nextcloud-app php occ status | grep '"version"'` contains `34.0.`
    - `curl -fsS http://127.0.0.1:8082/status.php | grep '"installed":true'` exits 0
  QA scenarios: happy: 34 confirmed. failure: upgrade errors → rollback using T9a DB dump. Evidence: .omo/evidence/task-9-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 10. Fix post-34 issues
  What to do / Must NOT do:
    (a) Re-run all post-33 fixes (T3-T7) to verify they still apply on v34:
        - WebDAV self-connectivity (T3)
        - 2FA app (T4) — may need reinstall if v34 removes it
        - DB indices (T5) — may have new v34 indices
        - AppAPI (T6) — may need re-suppression
        - Logs (T7) — check for new v34 entries
    (b) Capture any new v34-specific warnings
    (c) Re-verify `occ setupchecks` output and catalog remaining warnings
  Must NOT: skip any fix that reappeared after upgrade
  Parallelization: Wave 5 | Blocked by: T9 | Blocks: F1-F4
  References (executor has NO interview context):
    - Each major version may change app availability and bundled warnings
    - Some fixes from T3-T7 may need to be re-applied
  Acceptance criteria (agent-executable):
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep "✗"` only shows accepted issues (HTTPS, heartbeat, email)
    - All previously fixed issues remain fixed on v34
  QA scenarios: happy: all fixable issues resolved on 34. failure: new issue appears → document and address. Evidence: .omo/evidence/task-10-iskradocker-nextcloud-33-34.txt
  Commit: N

- [x] 11. Update AGENTS.md with Nextcloud version tracking
  What to do / Must NOT do:
    (a) Update `/home/antoine/docker/AGENTS.md`: add line documenting Nextcloud is on v34 and auto-updates via Watchtower
    (b) Update `/home/antoine/docker/compose/AGENTS.md`: add Nextcloud version info and link to rollback procedure
    (c) List accepted warnings (HTTPS, heartbeat, email) with explanation of why they persist
    (d) Verify: `grep -qi "nextcloud.*34" /home/antoine/docker/AGENTS.md` exits 0
  Must NOT: duplicate runbook content; expand beyond version tracking docs
  Parallelization: Wave 5 | Blocked by: T10 | Blocks: F1-F4
  References (executor has NO interview context):
    - AGENTS.md already has Watchtower auto-update section from previous plan
    - Add Nextcloud-specific subsection
    - Document accepted warnings as intentional, not bugs
  Acceptance criteria (agent-executable):
    - `grep -qi "nextcloud.*34" /home/antoine/docker/AGENTS.md` exits 0
    - `grep -qi "accepted warning\|intentional" /home/antoine/docker/AGENTS.md` exits 0
  QA scenarios: happy: docs updated. failure: grep fails. Evidence: .omo/evidence/task-11-iskradocker-nextcloud-33-34.txt
  Commit: N

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — verify: image tags are 33-fpm then 34-fpm; backups before each upgrade; WebDAV fixed; 2FA app enabled; DB indices added; AppAPI suppressed; logs clean; AGENTS.md updated
- [x] F2. No leftover artifacts — verify: no failed Nextcloud containers; no excessive /tmp/ files; maintenance mode disabled
- [x] F3. Automated functional QA — verify:
  - Version: `curl -fsS http://127.0.0.1:8082/status.php | grep '"version":"3[34]'` exits 0
  - .well-known/carddav: 301 redirect
  - .well-known/caldav: 301 redirect
  - WebDAV: `curl -fsS http://127.0.0.1:8082/remote.php/dav | head -1` returns 207 or 401 (not 404)
  - Setupchecks: Only accepted issues remain (HTTPS, heartbeat headers, email)
  - DB health: `docker exec --user www-data nextcloud-app php occ db:convert-filecache-bigint --dry-run` exits 0 (or confirms no issue)
- [x] F4. Scope fidelity — verify: only Nextcloud app+cron images changed; DB/redis/web images unchanged; Watchtower labels intact; no custom scripts/timers added; no email/SMTP configured; HTTPS not enabled

## Rollback procedure
> If any upgrade (32→33 or 33→34) fails, execute immediately.

### Triggers
- Upgrade logs contain "Fatal" or "failed"
- `occ status` shows maintenance:true for >10min after upgrade
- Nextcloud returns 500/503 errors
- Critical apps disabled

### Steps
1. `docker compose -f nextcloud.yml down`
2. Restore DB: `docker exec -i nextcloud-db psql -U oc_antoine5 nextcloud < /tmp/nextcloud-db-backup-NN.sql`
3. Revert image tags in nextcloud.yml to previous version
4. `docker compose -f nextcloud.yml up -d`
5. Verify: `curl -fsS http://127.0.0.1:8082/status.php` returns correct version

### Warnings
- If DB schema changed, restore from dump is REQUIRED
- Data directory (user files) is NEVER affected by upgrades
- If local dump fails, use Kopia snapshot restore

## Commit strategy
- No git on iskradocker. Changes applied directly via SSH.

## Success criteria
1. Nextcloud upgraded from 32→33→34 (each with backups + verification)
2. WebDAV self-connectivity resolved (occ setupchecks passes)
3. 2FA provider app installed and enabled (warning suppressed)
4. Missing DB indices added (warning suppressed)
5. AppAPI deploy daemon warning suppressed
6. Logs cleared of upgrade-related warnings
7. Only accepted warnings remain (HTTPS, heartbeat headers, email — documented in AGENTS.md)
8. Watchtower continues managing Nextcloud containers
9. No breaking changes to data or configuration
10. AGENTS.md documents current version and accepted warnings
