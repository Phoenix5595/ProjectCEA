# iskradocker-tailscale-https - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** Nextcloud, Jellyfin, and Immich become reachable over HTTPS at your `*.ts.net` tailnet name (e.g. `https://iskradocker.tail7a351e.ts.net`), with real Let's Encrypt certificates that auto-renew. Your browser will stop showing "Not secure" and Nextcloud's "insecure URLs" / HSTS warnings will clear automatically.

**Why this approach:** Tailscale has built-in HTTPS provisioning for your tailnet — it terminates TLS at the edge and forwards to your existing HTTP services. No reverse proxy to install, no external DNS, no port forwarding, no renewal cron. Solo tailnet user = perfect fit.

**What it will NOT do:** open any ports to the public internet; install Caddy/nginx at the TLS edge (Tailscale is the edge); touch CLIProxyAPI dashboard's existing HTTPS on :7443; disable Watchtower; require any cron or systemd timer.
**Note on HTTP fallback:** once Nextcloud gets `overwriteprotocol=https`, direct HTTP access (e.g. `http://iskradocker:8082`) will receive a 301 redirect to the HTTPS tailnet URL — this is expected behaviour and clears the admin warning. HTTP as a pure fallback path no longer works for Nextcloud after this change; keep this plan if you ever revert `overwriteprotocol=https`.

**Effort:** Quick — 5 todos, 3 waves. Each `tailscale serve` is one command; the bulk is one Nextcloud config block.
**Risk:** Low — fully reversible (`tailscale serve reset` clears all rules in <1s); HTTP fallback preserved for Jellyfin/Immich; all changes are additive (no destructive ops).
**Decisions to sanity-check:** (1) Service→port mapping: Nextcloud=443, Jellyfin=8443, Immich=9443. (2) nextcloud-nginx.conf changes `fastcgi_param HTTPS off` to `on` unconditionally (edge is always HTTPS via Tailscale).
**Prerequisite (user action):** HTTPS Certificates must be enabled at https://login.tailscale.com/admin/dns → "HTTPS Certificates" toggle ON. T1's cert test will confirm this; if it fails with "tailnet does not have HTTPS enabled", the worker marks itself blocked (~) and surfaces it to you — only you can toggle it.

Your next move: approve, or run a high-accuracy review. Full execution detail follows below.

---

> TL;DR (machine): Quick effort, Low risk. 5 todos, 3 waves. Tailscale serve HTTPS for Nextcloud/Jellyfin/Immich (no new proxy, no public ports). Nextcloud occ config + nginx HTTPS=on. Certs auto-renew via Tailscale daemon. HTTP fallback preserved.

## Scope
### Must have
- HTTPS endpoints for Nextcloud, Jellyfin, Immich via `tailscale serve` (real Let's Encrypt certs on ts.net domain)
- Nextcloud config updated to serve HTTPS (overwriteprotocol, overwrite.cli.url, trusted_domains, nginx HTTPS=on)
- AGENTS.md updated to document new HTTPS access
- No ports opened to public internet; all access stays inside tailnet

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **MUST NOT** open any ports to the WAN / public internet
- **MUST NOT** install a separate reverse proxy (Caddy/nginx) for TLS termination — Tailscale is the edge
- **MUST NOT** touch CLIProxyAPI dashboard's existing Caddyfile-https on :7443 (leave as-is)
- **MUST NOT** add a Tailscale-side HTTP→HTTPS redirect (direct HTTP stays working for Jellyfin/Immich; Nextcloud will issue its own 301 after overwriteprotocol=https — that's expected and documented)
- **MUST NOT** modify files in /home/antoine/ProjectCEA/ (atomic, no auto-fix)
- **MUST NOT** disable Watchtower or remove Watchtower labels
- **MUST NOT** recycle nextcloud-db/redis/postgres containers
- **MUST NOT** configure external DNS or Dynamic DNS providers
- **MUST NOT** require cron jobs / systemd timers for cert renewal (Tailscale daemon handles both)
- **MUST NOT** use git on iskradocker (no git installed; edit files via SSH directly)

## Verification strategy
> Zero human intervention - all verification is agent-executed via SSH probes, curl HTTPS, occ setupchecks, and bash test loops.
- Test decision: tests-after (configure → then curl/occ verify → assert)
- Evidence: .omo/evidence/task-<N>-iskradocker-tailscale-https.txt

## Execution strategy
### Parallel execution waves
> 5-8 todos per wave. One-time setup can't parallelize; the three service setups fully parallelize.

Wave 1 (T1): One-time Tailscale operator setup + verify cert capability
Wave 2 (T2, T3, T4): Three services in parallel (Nextcloud, Jellyfin, Immich)
Wave 3 (T5): Update AGENTS.md docs
Wave 4 (F1-F4): Final verification wave

### Dependency matrix

| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 (operator+cert verify) | — | T2, T3, T4 | — |
| T2 (Nextcloud HTTPS) | T1 | F1, F3, F4 | T3, T4 |
| T3 (Jellyfin HTTPS) | T1 | F1, F3, F4 | T2, T4 |
| T4 (Immich HTTPS) | T1 | F1, F3, F4 | T2, T3 |
| T5 (AGENTS.md) | T2, T3, T4 | F1 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. Set Tailscale operator + verify HTTPS cert provisioning capability
  What to do / Must NOT do:
    (a) Set the current user as Tailscale operator (allows non-sudo serve management going forward):
        `echo 'Lenin1917' | sudo -S tailscale set --operator=antoine`
    (b) Verify HTTPS cert issuance works by requesting a cert for the machine's tailnet FQDN:
        `tailscale cert iskradocker.tail7a351e.ts.net --cert-file=/tmp/ts-test.crt --key-file=/tmp/ts-test.key`
    (c) Verify cert files exist and are non-empty:
        `ls -la /tmp/ts-test.crt /tmp/ts-test.key`
    (d) Inspect cert to confirm Let's Encrypt issuer:
        `openssl x509 -in /tmp/ts-test.crt -noout -issuer`
    (e) Clean up test certs:
        `rm /tmp/ts-test.crt /tmp/ts-test.key`
  Must NOT: install new packages; open any ports; modify tailnet policy (admin console only — if certs fail, that means HTTPS isn't enabled in tailnet policy, which only the USER can toggle from https://login.tailscale.com/admin/dns — if so, MARK THIS TASK `- [~]` and SURFACE the blocker to the user; do NOT try to enable it via CLI)
  Parallelization: Wave 1 | Blocked by: none | Blocks: T2, T3, T4
  References (executor has NO interview context):
    - SSH: antoine@iskradocker (sudo password: Lenin1917)
    - Tailnet domain: tail7a351e.ts.net
    - Machine FQDN: iskradocker.tail7a351e.ts.net
    - Tailscale version: 1.96.4 (HTTPS supported since v1.34)
    - MagicDNS enabled, tailnet name verified via `tailscale status --json`
    - Existing Caddyfile-https on iskradocker uses TS certs via `tailscale cert` — proves capability exists in this tailnet already (see /home/antoine/docker/compose/Caddyfile-https)
  Acceptance criteria (agent-executable):
    - `tailscale serve status 2>&1 | head -1` exits 0 (no permission errors)
    - `ls -la /tmp/ts-test.crt /tmp/ts-test.key` both >0 bytes
    - `openssl x509 -in /tmp/ts-test.crt -noout -issuer` contains "Let's Encrypt"
  QA scenarios (name the exact tool + invocation):
    - happy: cert files generated, issuer=Let's Encrypt, serve commands work without sudo
    - failure: "tailnet does not have HTTPS enabled" → MARK `- [~]`, write evidence with the exact error, and STOP — the rest of the plan is blocked until user enables HTTPS in admin dashboard
    - Evidence: .omo/evidence/task-1-iskradocker-tailscale-https.txt
  Commit: N (no git on iskradocker)

- [x] 2. Configure Nextcloud HTTPS (tailscale serve + occ config + nginx HTTPS on)
  What to do / Must NOT do:
    (a) Start Tailscale HTTPS reverse proxy pointing at the existing nginx HTTP port:
        `tailscale serve --bg --https=443 http://localhost:8082`
    (a-bis) Warm-up / cert-provision step — Tailscale lazily provisions the Let's Encrypt cert on the FIRST HTTPS request (blocks ~5-30s). Trigger it explicitly so the verification step below doesn't time out:
        `curl -fsS --max-time 60 https://iskradocker.tail7a351e.ts.net/status.php | grep '"installed":true'`
    (b) Verify HTTPS responds after warm-up:
        `curl -fsS https://iskradocker.tail7a351e.ts.net/status.php | grep '"installed":true'`
    (c) Add the tailnet FQDN to Nextcloud trusted_domains. First VERIFY the next available index, then use it:
        `docker exec --user www-data nextcloud-app php occ config:system:get trusted_domains` to list current entries (indices 0-4 are expected taken; if index 5 is already in use, use the next free integer).
        Set: `docker exec --user www-data nextcloud-app php occ config:system:set trusted_domains 5 --value="iskradocker.tail7a351e.ts.net"`
    (d) Set overwrite.cli.url to the HTTPS URL used by cron/WebDAV:
        `docker exec --user www-data nextcloud-app php occ config:system:set overwrite.cli.url --value="https://iskradocker.tail7a351e.ts.net"`
    (e) Set overwriteprotocol to https (makes generated URL links use https://, clears the "insecure URLs" warning):
        `docker exec --user www-data nextcloud-app php occ config:system:set overwriteprotocol --value=https`
    (f) Update nextcloud-nginx.conf so PHP-FPM sees HTTPS=on (currently hardcoded off):
        Edit `/home/antoine/docker/compose/nextcloud-nginx.conf` — change the line `fastcgi_param HTTPS off;` to `fastcgi_param HTTPS on;`
        (Reason: Tailscale terminates TLS at the edge and forwards to nginx over HTTP; nginx tells PHP whether HTTPS was used. Setting on matches the user-facing reality.)
    (g) Recreate nextcloud-web to pick up the nginx config change:
        `cd /home/antoine/docker/compose && docker compose -f nextcloud.yml up -d nextcloud-web`
    (h) Verify Nextcloud admin warnings cleared:
        `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "insecure URLs"` should be 0
        `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "Strict-Transport-Security"` should be 0
  Must NOT: change trusted_domains indices 0-4; use https in nextcloud-nginx.conf listen directives (nginx stays HTTP); recreate nextcloud-db/redis; disable Watchtower labels; break existing http://iskradocker:8082 access (must still work as a fallback)
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: F1, F3, F4 | Can parallelize with: T3, T4
  References (executor has NO interview context):
    - SSH: antoine@iskradocker (sudo password: Lenin1917)
    - Current Nextcloud version: 34.0.0.12
    - Current trusted_domains: 0=localhost, 1=192.168.1.77, 2=iskradocker, 3=100.123.38.1, 4=nextcloud-web
    - Current overwrite.cli.url: http://nextcloud-web (deliberately internal Docker name — being changed to the HTTPS ts.net URL)
    - Current overwriteprotocol: http
    - nextcloud-nginx.conf path: /home/antoine/docker/compose/nextcloud-nginx.conf
    - The line to change is approximately line 90, inside the `location ~ \.php(?:$|/)` block
    - Nextcloud-app container does NOT have Tailscale DNS — but to confirm this doesn't break WebDAV self-check, run setupchecks after the change (T2 step h); if WebDAV self-check fails because the container can't resolve iskradocker.tail7a351e.ts.net, fall back to keeping overwrite.cli.url=http://nextcloud-web (internal Docker name resolves inside the container network) AND set overwritehost=iskradocker.tail7a351e.ts.net (this overrides only the URL shown to users, not the internal cron path). Document whichever approach worked in the evidence file.
    - HSTS header (`Strict-Transport-Security`) is set by Tailscale's TLS terminator, so Nextcloud's check should clear once overwriteprotocol=https.
    - After overwriteprotocol=https, direct HTTP access (http://iskradocker:8082) will receive a 301 redirect to the HTTPS tailnet URL — this is expected and clears the admin panel warning. Do NOT treat the redirect as a failure; treat it as confirmation that overwriteprotocol is active.
  Acceptance criteria (agent-executable):
    - `tailscale serve status` shows a rule for port 443 → http://localhost:8082
    - `curl -fsS https://iskradocker.tail7a351e.ts.net/status.php | grep '"installed":true'` exits 0
    - `docker exec --user www-data nextcloud-app php occ config:system:get overwriteprotocol` returns `https`
    - `docker exec --user www-data nextcloud-app php occ config:system:get trusted_domains 5` returns `iskradocker.tail7a351e.ts.net`
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "insecure URLs"` == 0
    - `docker exec --user www-data nextcloud-app php occ setupchecks 2>&1 | grep -c "Strict-Transport-Security"` == 0
    - Direct HTTP still works as fallback: `curl -fsSI http://iskradocker:8082/status.php | head -1` returns 200/302
  QA scenarios:
    - happy: HTTPS responds, occ reports https config, both admin warnings cleared, HTTP fallback intact
    - failure path 1: WebDAV self-check fails after overwrite.cli.url change → use the overwritehost fallback described above, re-run setupchecks, document
    - failure path 2: nginx config syntax error → `docker compose -f nextcloud.yml logs nextcloud-web --tail 20` shows the issue; revert the change and proceed with only occ config (still clears warnings because overwriteprotocol=https is what URL generation reads)
    - Evidence: .omo/evidence/task-2-iskradocker-tailscale-https.txt
  Commit: N (no git on iskradocker)

- [x] 3. Configure Jellyfin HTTPS via Tailscale serve
  What to do / Must NOT do:
    (a) Start Tailscale HTTPS reverse proxy for Jellyfin's host-networked port 8096:
        `tailscale serve --bg --https=8443 http://localhost:8096`
    (a-bis) Warm-up / cert-provision step — Tailscale lazily provisions the cert on first HTTPS request (blocks ~5-30s). Trigger it explicitly:
        `curl -fsSI --max-time 60 https://iskradocker.tail7a351e.ts.net:8443/web/ 2>&1 | head -5`
    (b) Verify HTTPS responds after warm-up:
        `curl -fsSI https://iskradocker.tail7a351e.ts.net:8443/web/ 2>&1 | head -5` — expect 200 or 302 (login redirect)
    (c) Verify Jellyfin's known-network config doesn't reject the tailnet subnet (100.64.0.0/10 is CGNAT range):
        Check Jellyfin network config: `ls -la /srv/jellyfin/config/network/ 2>/dev/null`
        If a `network.xml` exists with a restricted `<LocalAddress>` or `<KnownProxies>` list, ensure `100.64.0.0/10` is permitted; if absent, do nothing (default allows all local subnets)
  Must NOT: change Jellyfin's httpPort/httpsPort settings (Tailscale terminates TLS before Jellyfin sees it, so Jellyfin stays HTTP); disable host networking; add a bind address that excludes localhost; disable Watchtower label
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: F1, F3, F4 | Can parallelize with: T2, T4
  References (executor has NO interview context):
    - SSH: antoine@iskradocker (sudo password: Lenin1917)
    - Jellyfin uses `network_mode: host` (listens on host's 0.0.0.0:8096), so `localhost:8096` from the host is the correct backend
    - Compose file: /home/antoine/docker/compose/media.yml
    - Jellyfin config: /srv/jellyfin/config/
    - Watchtower-managed (nextcloud.yml/media.yml labels stay intact)
    - The tailnet IP range is CGNAT (100.64.0.0/10); Jellyfin's localhost classification usually accepts this as LAN by default, but if HTTPS returns 403/401, the known-networks config is the first thing to check
  Acceptance criteria (agent-executable):
    - `tailscale serve status` shows a rule for port 8443 → http://localhost:8096
    - `curl -fsSI https://iskradocker.tail7a351e.ts.net:8443/web/ | head -1` returns 200 or 302
    - `curl -fsS https://iskradocker.tail7a351e.ts.net:8443/System/Info/Public | head -20` returns JSON with `"ServerName"` (Jellyfin's public info endpoint)
  QA scenarios:
    - happy: HTTPS responds, Jellyfin returns 200/302 to /web/, public system info accessible
    - failure: 403 "Forbidden" → check network config; add `100.64.0.0/10` to KnownProxies/LAN; restart Jellyfin
    - Evidence: .omo/evidence/task-3-iskradocker-tailscale-https.txt
  Commit: N

- [x] 4. Configure Immich HTTPS via Tailscale serve
  What to do / Must NOT do:
    (a) Start Tailscale HTTPS reverse proxy for Immich's port 2283:
        `tailscale serve --bg --https=9443 http://localhost:2283`
    (a-bis) Warm-up / cert-provision step — Tailscale lazily provisions the cert on first HTTPS request (blocks ~5-30s). Trigger it explicitly:
        `curl -fsSI --max-time 60 https://iskradocker.tail7a351e.ts.net:9443/ 2>&1 | head -5`
    (b) Verify HTTPS responds after warm-up:
        `curl -fsSI https://iskradocker.tail7a351e.ts.net:9443/ 2>&1 | head -5` — expect 200 or 302
    (c) Verify Immich's "/server-info" API responds on HTTPS:
        `curl -fsS https://iskradocker.tail7a351e.ts.net:9443/server-info | head -50` — expect JSON
  Must NOT: change Immich's `IMMICH_SERVER_URL` env (the app server stays on HTTP; only the Tailscale edge serves HTTPS); recreate immich-postgres or immich-redis; disable Watchtower labels
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: F1, F3, F4 | Can parallelize with: T2, T3
  References (executor has NO interview context):
    - SSH: antoine@iskradocker (sudo password: Lenin1917)
    - Compose file: /home/antoine/docker/compose/photos.yml
    - Immich-server container exposes port 2283 (0.0.0.0:2283 → 2283/tcp), so localhost:2283 is the correct backend
    - Immich web is at root (`/`); API is at /api/* or /server-info/style etc. depending on version — /server-info reliably returns JSON for health check
    - Watchtower-managed; TLS termination happens at Tailscale edge, container itself is unchanged
  Acceptance criteria (agent-executable):
    - `tailscale serve status` shows a rule for port 9443 → http://localhost:2283
    - `curl -fsSI https://iskradocker.tail7a351e.ts.net:9443/ | head -1` returns 200 or 302
    - `curl -fsSL https://iskradocker.tail7a351e.ts.net:9443/server-info` exits 0 (JSON returned, may be 401 for some endpoints — that's healthy, just not 404/500)
  QA scenarios:
    - happy: HTTPS responds 200/302, Immich publicly accessible, API reachable
    - failure: 502 Bad Gateway from Tailscale serve → container is down/unreachable; check `docker logs immich_server --tail 30` and `docker ps | grep immich_server`
    - Evidence: .omo/evidence/task-4-iskradocker-tailscale-https.txt
  Commit: N

- [x] 5. Update AGENTS.md with HTTPS access details
  What to do / Must NOT do:
    (a) Update `/home/antoine/docker/AGENTS.md` to add a section documenting the new Tailscale HTTPS endpoints for iskradocker services:
        - Nextcloud: https://iskradocker.tail7a351e.ts.net
        - Jellyfin: https://iskradocker.tail7a351e.ts.net:8443
        - Immich: https://iskradocker.tail7a351e.ts.net:9443
        - (mention CLIProxyAPI dashboard already on https://iskradocker.tail7a351e.ts.net:7443 via existing Caddyfile-https)
    (b) Document the cert lifecycle (auto-provisioned by Tailscale, auto-renewed by daemon — no cron, no systemd timers)
    (c) Document the rollback procedure (run `tailscale serve reset` to clear all serve rules; revert Nextcloud occ config to overwriteproto=http, overwrite.cli.url=http://nextcloud-web)
    (d) Update `/home/antoine/docker/compose/AGENTS.md` with a similar HTTPS summary
  Must NOT: install new packages; create new docs files (edit existing only); duplicate full Tailscale runbook content
  Parallelization: Wave 3 | Blocked by: T2, T3, T4 | Blocks: F1
  References (executor has NO interview context):
    - SSH: antoine@iskradocker
    - These AGENTS.md files already have Nextcloud 34 + Watchtower sections from a previous plan (add new HTTPS context as an additional subsection)
    - Tailnet domain: tail7a351e.ts.net
  Acceptance criteria (agent-executable):
    - `grep -qi "tailscale serve" /home/antoine/docker/AGENTS.md` exits 0
    - `grep -qi "https://iskradocker.tail7a351e.ts.net" /home/antoine/docker/AGENTS.md` exits 0
    - `grep -qi "cert.*auto\|auto.*cert\|automatic" /home/antoine/docker/AGENTS.md` exits 0
  QA scenarios:
    - happy: docs include exact HTTPS URLs and mention auto-renewal
    - failure: file missing → check SSH access; create the section under existing structure
    - Evidence: .omo/evidence/task-5-iskradocker-tailscale-https.txt
  Commit: N

- [x] 6. Restore Portainer + add to Watchtower (post-scope-violation remediation)
  What to do / Must NOT do:
    (a) Restore Portainer container from `infra.yml` after T4 worker wrongly removed it
    (b) Change host port from 9443→9444 to resolve conflict with Immich Tailscale serve on 9443
    (c) Add Watchtower auto-update label: `com.centurylinklabs.watchtower.enable=true`
    (d) Update AGENTS.md with Portainer restoration details and scope violation log
    (e) Verify Portainer responds on port 9000 and has Watchtower label
  Must NOT: remove any other containers; change Portainer's internal port (9443); disable Watchtower
  Parallelization: Post-wave | Blocked by: T4 scope violation | Blocks: —
  Acceptance criteria:
    - `docker ps --filter name=portainer --format '{{.Status}}'` shows "Up"
    - `curl -fsSI http://localhost:9000/api/status | head -1` returns 200
    - `docker inspect portainer --format='{{json .Config.Labels}}' | grep watchtower.enable` exits 0
    - AGENTS.md contains Portainer restoration details

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — verify: tailscale serve shows 3 rules (443→8082, 8443→8096, 9443→2283); Nextcloud occ overwriteprotocol=https; trusted_domains index 5 = ts.net FQDN; nextcloud-nginx.conf has `fastcgi_param HTTPS on;`; AGENTS.md mentions tailscale serve + auto cert; CLIProxyAPI dashboard untouched (Caddyfile-https unchanged on :7443)
- [x] F2. Code quality review — verify: no leftover test cert files in /tmp; no dangling serve rules; nginx config syntactically valid (`nginx -t` inside container); no broken trusted_domains indices
- [x] F3. Real manual QA — verify with curl:
  - `curl -fsS https://iskradocker.tail7a351e.ts.net/status.php | grep '"installed":true'` exits 0
  - `curl -fsSI https://iskradocker.tail7a351e.ts.net:8443/web/ | head -1` returns 200 or 302
  - `curl -fsSI https://iskradocker.tail7a351e.ts.net:9443/ | head -1` returns 200 or 302
  - Nextcloud setupchecks shows 0 for "insecure URLs" AND "Strict-Transport-Security" warnings
  - Direct HTTP on Nextcloud now returns 301 (redirect to HTTPS — this confirms overwriteprotocol=https is active, NOT a failure)
  - Direct HTTP on Jellyfin and Immich still works as a fallback: `curl -fsSI http://iskradocker:8096/web/ | head -1` returns 200; `curl -fsSI http://iskradocker:2283/ | head -1` returns 200/302
- [x] F4. Scope fidelity — verify: no ports opened to WAN (check `sudo iptables -L INPUT -n | grep -E "443|8443|9443"` shows no public-facing rules); no new Docker containers added; no new systemd timers or cron jobs created; no changes to CLIProxyAPI dashboard's Caddyfile; no changes to Watchtower labels; nextcloud-db/redis/postgres containers not recreated

## Commit strategy
- No git on iskradocker — all changes applied directly via SSH. `tailscale serve --bg` configs persist in the Tailscale daemon state (survives reboot). Nextcloud occ configs persist in `config.php`. nginx config persists in the mounted `nextcloud-nginx.conf` file.

## Success criteria
1. HTTPS works for Nextcloud, Jellyfin, Immich at their respective ts.net URLs (no browser cert warnings)
2. Nextcloud admin panel no longer shows "insecure URLs" or "Strict-Transport-Security" warnings
3. Certs are Let's Encrypt, auto-renewed by the Tailscale daemon (no cron, no timers)
4. No ports opened to public internet; all HTTPS terminates inside the tailnet
5. Existing direct HTTP access on local ports still works as fallback
6. CLIProxyAPI dashboard and its existing Caddyfile-https configuration untouched
7. AGENTS.md updated with the new HTTPS access pattern and rollback procedure
