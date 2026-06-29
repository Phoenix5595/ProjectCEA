---
slug: iskradocker-tailscale-https
status: drafting
intent: clear
pending-action: write .omo/plans/iskradocker-tailscale-https.md
approach: <fill: the approach you intend to plan>
---

# Draft: iskradocker-tailscale-https

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->

## Findings (cited - path:lines)

## Decisions (with rationale)

## Scope IN

## Scope OUT (Must NOT have)

## Open questions

## Approval gate
status: drafting
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
# Draft — iskradocker-tailscale-https

## Intent routing
- CLEAR. User said: "make it https" / "no, for iskra just use tailscale" / "do it, write" / "continue writing the plan". Sole user-facing outcome: HTTPS for the iskradocker services they access via Tailscale. No public ports, no port forwarding.

## Facts gathered (Phase 1 - explore)
- Tailscale 1.96.4 on iskradocker (>> 1.34 HTTPS minimum) — HTTPS supported
- Tailnet domain: `tail7a351e.ts.net`; MagicDNS enabled
- Machine FQDN: `iskradocker.tail7a351e.ts.net`
- No existing `tailscale serve` config
- `tailscale serve` requires sudo (or one-time `tailscale set --operator=antoine`)
- SSH access: antoine@iskradocker, sudo password: Lenin1917
- Services currently HTTP on local ports:
  - Nextcloud: localhost:8082 (via nextcloud-web nginx → nextcloud-app:9000 PHP-FPM)
  - Jellyfin: localhost:8096 (host networking, no published ports)
  - Immich: localhost:2283
  - CLIProxyAPI dashboard: localhost:8085 (already has Caddyfile-https on :7443 using TS certs — LEAVE ALONE)
- Nextcloud current config (from occ):
  - overwriteprotocol = http (NEEDS → https)
  - overwrite.cli.url = http://nextcloud-web (NEEDS → https://iskradocker.tail7a351e.ts.net)
  - trusted_domains indices 0-4 filled (localhost, 192.168.1.77, iskradocker, 100.123.38.1, nextcloud-web)
  - Need to add index 5 = iskradocker.tail7a351e.ts.net
- nextcloud-nginx.conf line 90: `fastcgi_param HTTPS off;` (hardcoded OFF — PHP won't see HTTPS=on even though Tailscale terminates TLS). Decision: change to a map that respects X-Forwarded-Proto, OR default to `on` since the edge is always HTTPS via Tailscale.
- Watchtower labeled nextcloud-app/cron/web, jellyfin, immich-server/ML
- No git on iskradocker; everything via SSH direct edit

## Defaults adopted
1. Services covered: Nextcloud, Jellyfin, Immich. CLIProxyAPI dashboard already HTTPS — leave as-is.
2. Port assignments: Nextcloud=443 (primary), Jellyfin=8443, Immich=9443.
3. One-time: `sudo tailscale set --operator=antoine` allows non-sudo serve management.
4. nextcloud-nginx.conf: change `fastcgi_param HTTPS off;` → `fastcgi_param HTTPS on;` (edge is always HTTPS via Tailscale; simplest, matches reality).
5. HTTP stays accessible on local ports for backward compat (no HTTP→HTTPS redirect lock; Tailscale serve adds HTTPS alongside existing HTTP).

## Gate
- status: awaiting-approval → APPROVED ("do it, write")
- pending action: write .omo/plans/iskradocker-tailscale-https.md

## Phase 3 - Plan generation complete
- Scaffolded: yes (script-emitted)
- Metis gap analysis: complete; folded findings in silently (cert warm-up steps, trusted_domains index verify, container-DNS fallback, HTTP redirect note, prerequisite call-out)
- Todos appended: T1 (operator+cert verify), T2 (Nextcloud), T3 (Jellyfin), T4 (Immich), T5 (AGENTS.md)
- Final wave: F1-F4 (plan compliance, code quality, manual QA, scope fidelity)
- TL;DR filled LAST
- Self-review: every todo has references + agent-executable acceptance + happy/failure QA + evidence path + commit line. Dependency matrix consistent.

## Phase 4 - Delivery
- Plan written, Metis findings folded in.
- Awaiting user: start work now, or run the dual high-accuracy review first?
