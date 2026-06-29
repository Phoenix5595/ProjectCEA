# iskradocker-nextcloud-33-34 Learnings

## 2026-06-22 - Wave 1-2 Complete
- T1: Backups created successfully (Kopia snapshot, DB dump 4.1MB, config, apps list)
- T2: Nextcloud upgraded 32→33.0.5.1 successfully
- Current version: 33.0.5.1, maintenance: false
- Next: T3-T7 (post-33 fixes) in parallel

## 2026-06-22 - T3: WebDAV Self-Connectivity Fix
- Applied: `trusted_domains 4 = nextcloud-web`, `overwrite.cli.url = http://nextcloud-web`
- Verification: setupchecks shows ✓ WebDAV endpoint, 0 warnings
- External access preserved (Tailscale 8082 unchanged)
- Evidence saved to .omo/evidence/task-3-iskradocker-nextcloud-33-34.txt

- SSH: antoine@iskradocker, sudo password: Lenin1917
- DB: oc_antoine5/nextcloud
- WebDAV fix: set overwrite.cli.url to http://nextcloud-web + trusted_domains
- 2FA: twofactor_totp app (bundled, trusted)
- AppAPI: disable app_api app (unused Ex-Apps feature)
- DB indices: occ db:add-missing-indices
- Logs: set level to warning (2) if benign
- Accepted warnings (intentional): HTTPS, heartbeat headers, email

## 2026-06-22 - T5: Missing DB Indices Added
- Command: `docker exec --user www-data nextcloud-app php occ db:add-missing-indices`
- Result: Completed successfully (idempotent, no output)
- Verification: `setupchecks | grep -c "missing optional indices"` == 0
- Evidence saved to .omo/evidence/task-5-iskradocker-nextcloud-33-34.txt

## 2026-06-22 - T6: AppAPI Deploy Daemon Warning
- Checked: app_api app is installed (v33.0.0) but already disabled
- Disable command returned: "No such app enabled: app_api"
- Verification: setupchecks grep count for "AppAPI deploy daemon" = 0
- No action needed — warning already suppressed from previous v32 fix
- Evidence saved to .omo/evidence/task-6-iskradocker-nextcloud-33-34.txt

## 2026-06-22 - T7: Post-Upgrade Logs Checked and Cleared
- Checked: `docker exec --user www-data nextcloud-app php occ log:tail 50`
- Findings: ALL entries benign (no Error/Fatal/Critical)
  * Info: updater — "Update successful", "Reset log level to Warning(2)"
  * Debug: serverDI — deprecated alias (non-critical)
  * Warning: no app in context — updater backup folder missing (expected)
  * Warning: cron — QueryNotFoundException for ProvidersAICleanUpJob (missing after app update, non-critical)
  * Warning: core — memory usage 721MB/1GB (within limits)
- Action: `occ log:manage --level 2` (Warning) — clears old Info/Debug from tail view
- Verification: `occ log:tail 5` shows no critical errors; `setupchecks` shows 0 genuine errors
- Evidence saved to .omo/evidence/task-7-iskradocker-nextcloud-33-34.txt


## 2026-06-22 - T9: Nextcloud Upgraded 33.0.5.1 → 34.0.0
- DB backup: /tmp/nextcloud-db-backup-34-20260622-192422.sql (3.1MB)
- Image tags changed: nextcloud:33-fpm → nextcloud:34-fpm (app + cron)
- Maintenance mode on → pull → stop → recreate → poll logs → verify → maintenance mode off
- Upgrade completed in ~40 seconds ("Update successful" + 3/3 100%)
- Post-upgrade status:
  * version: 34.0.0.12
  * maintenance: false
  * needsDbUpgrade: false
  * HTTP status.php: responding correctly on 8082
- No Fatal errors detected during upgrade
- Evidence saved to .omo/evidence/task-9-iskradocker-nextcloud-33-34.txt

## 2026-06-22 - T10: Post-33 Fixes Re-verified on v34.0.0.12
- WebDAV (T3): `setupchecks | grep -c "Your web server is not yet properly set up"` = 0 — still OK
- 2FA (T4): twofactor_totp app present (16.0.0) but NOT enabled — user skipped, no action taken
- DB indices (T5): `occ db:add-missing-indices` idempotent (no output), missing indices = 0 — still OK
- AppAPI (T6): app_api 34.0.0 installed but disabled, `setupchecks | grep -c "AppAPI deploy daemon"` = 0 — still OK
- Logs (T7): `occ log:tail 20` showed only 1 benign warning (updater backup folder), `occ log:manage --level 2` applied
- Full setupchecks: 0 ✗ (errors), 2 ⚠ (warnings), 7 ℹ (info)
- NEW v34-specific warning: "Mimetype migrations available" — optional, non-critical. Can be fixed with `occ maintenance:repair --include-expensive` if desired.
- Accepted warnings (intentional): HTTPS access, HTTP headers (HSTS), email, brute-force throttle, forwarded-for headers, server ID, 2FA not enforced
- Evidence saved to .omo/evidence/task-10-iskradocker-nextcloud-33-34.txt

## 2026-06-22 - T11: AGENTS.md Updated for Nextcloud 34.0.0
- Files modified on iskradocker:
  * `/home/antoine/docker/AGENTS.md` — added "Nextcloud 34.0.0 — Version & Accepted Warnings" section
  * `/home/antoine/docker/compose/AGENTS.md` — added "Nextcloud 34.0.0" section
- Content documented:
  * Current version: 34.0.0
  * Auto-update via Watchtower (nextcloud-app, nextcloud-web, nextcloud-cron)
  * nextcloud-db excluded (manual only)
  * Rollback procedure: docker pull + edit compose + recreate
  * Accepted warnings (intentional): HTTPS, heartbeat headers, email, 2FA
- Verification: all grep checks pass (nextcloud.*34, accepted warning/intentional in both files)
- Evidence saved to .omo/evidence/task-11-iskradocker-nextcloud-33-34.txt
