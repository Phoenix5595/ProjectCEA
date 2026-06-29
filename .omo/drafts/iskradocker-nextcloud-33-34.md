# iskradocker-nextcloud-33-34 - Draft Notes

## Status
status: awaiting-approval

## Exploration Summary

### Nextcloud Versions Available
- `nextcloud:33-fpm` ✅ Available on Docker Hub
- `nextcloud:34-fpm` ✅ Available on Docker Hub
- Current: 32.0.11.1 (just upgraded)

### Pasted Issues Analysis

| Issue | Category | Fixable? | Approach |
|-------|----------|----------|----------|
| **WebDAV endpoint** | Connectivity | ✅ Yes | Set `overwrite.cli.url` to internal nginx (`http://nextcloud-web`) |
| **HTTPS access** | TLS | ❌ No | Tailscale-only, no Caddy. Expected behavior. Documented in AGENTS.md. |
| **.well-known/webfinger** | Routing | ⚠️ Partial | nginx config already has `location ^~ /.well-known` — may need `webfinger` specifically |
| **HTTP headers/heartbeat** | Headers | ❌ No | `Security headers` check requires HTTPS endpoint. HTTP-only limitation. |
| **Second factor** | Security | ✅ Yes | Install `twofactor_totp` or `twofactor_nextcloud_notification` app |
| **Missing DB indices** | Database | ✅ Yes | Run `occ db:add-missing-indices` |
| **AppAPI deploy daemon** | Apps | ⚠️ Partial | Can suppress by disabling `app_api` app or configuring dummy daemon |
| **Errors in log** | Logs | ✅ Yes | Check actual errors, clear if benign post-upgrade |
| **Email test** | Config | ⚠️ Partial | User previously said skip. Could suppress warning with basic config or leave as-is. |

### Key Decisions (CLEAR intent, ask minimal questions)

1. **Major version upgrades (32→33→34)**: User explicitly wants both. Include sequentially with pause gates.
2. **HTTPS**: Expected limitations. Won't fix (no Caddy on iskradocker). Document in issues-to-accept list.
3. **Email**: User previously said skip. Will leave as-is unless explicitly asked otherwise.
4. **Heartbeat/security headers**: HTTP-only limitation. Won't fix.
5. **2FA**: Install `twofactor_totp` app (fixes "no provider" warning). User can enable in UI if desired.
6. **AppAPI**: Disable the `app_api` app to suppress warning (simplest fix).

### Upgrade Strategy
- Wave 1: Backups + Upgrade 32→33 + immediate verification
- Wave 2: Fix all post-33 issues (WebDAV, DB indices, 2FA, logs)
- Wave 3: Upgrade 33→34 + verification
- Wave 4: Fix any new post-34 issues
- Wave 5: Final verification

### Risk Assessment
- Two major version upgrades in sequence = higher risk
- Backups must be created before EACH upgrade
- Must verify 33 is stable before going to 34
- Issues-to-accept (HTTPS, heartbeat) clearly documented

### Rollback
- Same as previous plan: DB dump + config backup → revert image tags → recreate

## Issues to Accept (won't fix)
These are architectural limitations of the iskradocker setup:

1. **HTTPS access**: Tailscale provides transport encryption. No need for TLS at the container level. Nextcloud admin warning is cosmetic.
2. **HTTP headers/heartbeat**: Nextcloud's security header check queries a `/heartbeat` endpoint. This check skips on HTTP-only setups. The nginx config already serves standard security headers (X-Frame-Options, etc.). Cosmetic warning.
3. **Email**: User previously said skip. Will remain as an admin panel reminder.
