# iskradocker-nextcloud-fix - Draft Notes

## Status
status: awaiting-approval (post-Momus fixes applied)

## Momus Review Findings (FIXED)

### Critical Issues (All Fixed)
1. ✅ **nginx try_uri syntax error** — Changed to `try_files $uri $uri/ =404` with official `location ^~ /.well-known` block pattern
2. ✅ **Missing maintenance mode** — Added to T4: `occ maintenance:mode --on` before stopping containers, `--off` after upgrade
3. ✅ **Missing explicit upgrade handling** — T4 now polls upgrade logs every 10s for "Update successful" or "Fatal", uses `occ status` to verify
4. ✅ **T3 dependency matrix typo** — Fixed "Blocks: T3" to "Blocks: T4"

### High Issues (All Fixed)
1. ✅ **Rollback procedure missing** — Added full "## Rollback procedure" section with triggers, steps, and warnings
2. ✅ **T4 version check timing** — Changed from fixed 30s sleep to polling upgrade logs + occ status
3. ✅ **T4 health check** — Changed from docker inspect Health.Status to curl probe to status.php
4. ✅ **F3 "manual QA" contradiction** — Reworded to "Automated functional QA" with agent-executable curl commands
5. ✅ **F3 admin panel check** — Replaced with `occ setupchecks` command (no auth required)

### Medium Issues (Mostly Fixed)
1. ✅ **nginx config backup** — Added to T1 as step (d)
2. ⚠️ **Background migration verification** — Partially addressed via occ app:list in T6 (shows disabled apps)
3. ⚠️ **F2 backup cleanup** — Still says "or deleted after successful verification" - executor decides
4. ⚠️ **App compatibility check** — Added occ app:list to T6

## Exploration Summary

### Watchtower Update Issue
- **Root cause**: `nextcloud.yml` uses `nextcloud:31-fpm` - a floating tag that only tracks 31.x releases
- Watchtower correctly updated 31.0.13 → 31.0.14 (latest 31.x)
- Will NOT cross major version boundary (31→32) without explicit tag change
- This is SAFE behavior - major upgrades require user action

### Nextcloud Configuration Issues

| Issue | Current State | Required Fix |
|-------|---------------|--------------|
| **Version tag** | `nextcloud:31-fpm` | Change to `nextcloud:32-fpm` |
| **WebDAV/self-resolution** | Trusted domains don't include access URL | Add `mothernode:8080` or Tailscale IP to trusted_domains |
| **HTTP vs HTTPS** | `overwriteprotocol` = `http` | Keep as `http` (no Caddy on iskradocker, direct Tailscale) |
| **.well-known URLs** | Nginx not rewriting | Add nginx location blocks for `/.well-known/*` |
| **Maintenance window** | Not configured | Add `maintenance_window_start` => `02:00` |
| **Phone region** | Not set | Add `default_phone_region` => `'CA'` |
| **Mimetype migrations** | Pending | Run `occ maintenance:repair --include-expensive` |
| **Email** | Not configured | Skip per user request |

### setlocale Warnings
- SSH client forces `LC_ALL=en_CA.UTF-8`
- iskradocker only has `en_US.utf8` installed
- Harmless but annoying - can fix by installing en_CA locale or changing client locale

## User Decisions (CLEAR intent)

1. **Nextcloud version**: Upgrade to `32-fpm` (trust stable releases, want auto-upgrade path to 33+)
2. **HTTPS**: NO - direct Tailscale access on port 8082, no Caddy termination
3. **Phone region**: `CA` (Canada)
4. **Email**: Skip for now
5. **Maintenance window**: `02:00` (2 AM)

## Required Changes

### 1. nextcloud.yml
- Change `nextcloud-app` image: `nextcloud:31-fpm` → `nextcloud:32-fpm`
- Change `nextcloud-cron` image: `nextcloud:31-fpm` → `nextcloud:32-fpm`
- Add Watchtower label to both (already present, will continue working)

### 2. nextcloud-nginx.conf
- Add location blocks for `/.well-known/*` URLs
- Rewrite to `/index.php/.well-known/*`

### 3. config.php (via occ command)
- Set `default_phone_region` => `'CA'`
- Set `maintenance_window_start` => `2` (hour in 24h format)
- Verify trusted_domains includes the access URL

### 4. Run maintenance
- `occ maintenance:repair --include-expensive` (mimetype migrations)

### 5. setlocale (optional quality-of-life)
- Install `en_CA.UTF-8` locale on iskradocker, OR
- Document that user should set `LC_ALL=en_US.UTF-8` in SSH client

## Risk Assessment
- **Nextcloud major upgrade**: Medium risk - backup DB + data first, test after upgrade
- **Nginx config change**: Low risk - can rollback instantly
- **config.php changes**: Low risk - reversible via occ commands
- **Maintenance repair**: Low risk - standard Nextcloud operation

## Rollback Plan
If Nextcloud 32 breaks:
1. `docker compose -f nextcloud.yml down`
2. Change image tags back to `nextcloud:31-fpm`
3. `docker compose -f nextcloud.yml up -d`
4. Nextcloud will downgrade (may require DB restore if schema changed)

## Plan Structure
- Wave 1: Backup + nginx config + config.php changes
- Wave 2: Nextcloud 32 upgrade + verification
- Wave 3: setlocale fix (optional)
- F1-F4: Final verification wave
