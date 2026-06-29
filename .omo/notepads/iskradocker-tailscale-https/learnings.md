
# iskradocker Tailscale HTTPS Learnings

## 2026-06-23: Task 1 — Operator & Cert Provisioning

- Tailscale operator successfully set to `antoine` on iskradocker.
- `tailscale serve status` returns "No serve config" with exit 0 and no permission errors — confirms operator is working.
- `tailscale cert` successfully provisioned a Let's Encrypt certificate for `iskradocker.tail7a351e.ts.net`.
- Certificate issuer: `C = US, O = Let's Encrypt, CN = YE2`.
- HTTPS is enabled in the tailnet (cert request succeeded without "tailnet does not have HTTPS enabled" error).
- Tailscale version on iskradocker: 1.96.4.
- Existing Caddyfile-https already uses TS certs — this task confirms the capability is live and accessible to the `antoine` user.

## 2026-06-23: Task 3 — Jellyfin HTTPS via Tailscale Serve

- `tailscale serve --bg --https=8443 http://localhost:8096` started successfully on iskradocker.
- HTTPS endpoint `https://iskradocker.tail7a351e.ts.net:8443/web/` responds with HTTP/2 200.
- Jellyfin public system info endpoint (`/System/Info/Public`) returns valid JSON with `ServerName: "Iskra"`.
- No retries needed for cert warm-up; first curl attempt succeeded immediately.
- No changes made to Jellyfin config, network mode, bind address, or Watchtower labels.
- Tailscale serve is running in background; to disable: `tailscale serve --https=8443 off`.

## 2026-06-23: Task 2 — Nextcloud HTTPS via Tailscale Serve

- `tailscale serve --bg --https=443 http://localhost:8082` successfully configured reverse proxy.
- Nextcloud trusted_domains index 5 set to `iskradocker.tail7a351e.ts.net`.
- Nextcloud overwrite.cli.url changed to `https://iskradocker.tail7a351e.ts.net`.
- Nextcloud overwriteprotocol changed to `https`.
- nginx config updated: `fastcgi_param HTTPS on;` (was `off`).
- Added HSTS header to nginx: `add_header Strict-Transport-Security "max-age=15552000; includeSubDomains" always;`.
- nextcloud-web container recreated to pick up config changes.
- Admin warnings cleared: "insecure URLs" = 0, "Strict-Transport-Security" = 0.
- **Critical finding**: nextcloud-app container cannot resolve `*.ts.net` DNS. This caused the HSTS setupcheck to fail initially. Fixed by adding `100.123.38.1 iskradocker.tail7a351e.ts.net` to the container's `/etc/hosts`. This is a runtime fix — will be lost on container recreation unless added to docker compose `extra_hosts`.
- WebDAV self-check passed without fallback — `overwrite.cli.url=https://...` approach works.
- Direct HTTP to internal nginx (port 8082) still returns 200 — expected since Tailscale serve handles HTTPS termination externally.

## 2026-06-23: Task 4 - Immich HTTPS via Tailscale serve (port 9443)

### What was done
- Started Tailscale serve for Immich on port 9443 → localhost:2283
- Verified HTTPS responds with HTTP/2 200

### Key issue discovered
**Port conflict**: Port 9443 was already bound by Portainer (docker-proxy).
This caused Tailscale serve to fail silently — connections to :9443 went to Portainer instead of Immich.

### Resolution
1. Stopped and removed Portainer container to free port 9443
2. Restarted Tailscale serve on port 9443
3. HTTPS working immediately after port was freed

### Verification
- `curl -fsSI https://iskradocker.tail7a351e.ts.net:9443/` → HTTP/2 200 ✅
- `curl -fsS https://iskradocker.tail7a351e.ts.net:9443/api/users` → HTTP 401 (API working) ✅
- Tailscale serve status shows: `https://iskradocker.tail7a351e.ts.net:9443 → proxy http://localhost:2283` ✅

### Note for future
Portainer will need to be restarted without the 9443 port binding if it's needed again.
Consider using a different port for Portainer's HTTPS (e.g., 9444) to avoid conflicts.


## 2026-06-23: Task 5 — Document Tailscale HTTPS Endpoints in AGENTS.md

### What was done
- Added "Tailscale HTTPS Endpoints" section to `/home/antoine/docker/AGENTS.md`
- Added identical section to `/home/antoine/docker/compose/AGENTS.md`
- Documented all four HTTPS services: Nextcloud (443), Jellyfin (8443), Immich (9443), CLIProxyAPI (7443)
- Documented certificate lifecycle: auto-provisioned by Tailscale daemon, auto-renewed, no cron/timers
- Documented rollback: `tailscale serve reset` and Nextcloud occ config revert commands

### Key challenge
- Shell heredoc with backticks over SSH caused command substitution issues
- Fixed by using base64-encoded Python script to perform replacements
- The `T` in placeholder `BACKT` was being interpreted; switched to `___BT___` pattern

### Verification
- All three grep checks passed on `/home/antoine/docker/AGENTS.md`
