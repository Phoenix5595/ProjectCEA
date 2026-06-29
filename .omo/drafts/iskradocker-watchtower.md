---
slug: iskradocker-watchtower
status: awaiting-approval
intent: clear
pending-action: user approves, then $start-work
approach: Use existing Watchtower (remove --cleanup, add labels to NC/JF/immich containers). Immich gated on pgvecto→VectorChord migration. No scripts, no timers, no git.
---

# Draft: iskradocker-watchtower

## Components (topology ledger)
C1 | Watchtower reconfig (remove --cleanup) | active | .omo/plans/iskradocker-watchtower.md T1
C2 | Nextcloud + Jellyfin labels | active | T1
C3 | Immich DB migration (pgvecto→VectorChord) | active (manual-gated) | T2
C4 | Immich labels (post-migration) | active | T3
C5 | AGENTS.md docs | active | T4

## Open assumptions (announced defaults)
- No email notifications | Watchtower logs to docker logs only | user wants simple, "easily manage in the future" | reversible (can add later)
- Keep hourly check interval (--interval 3600) | existing behavior, simple | user didn't request a specific window | reversible
- *arr stack NOT labeled | user only mentioned "nextcloud, jellyfin, and immich" | scope boundary | reversible (can add labels later)
- Watchtower config is UNCHANGED — keep `--cleanup` | user said "these projects should not have broken stable releases" + rollback is just pulling old version from Docker Hub | reversible
- No git on iskradocker | user explicitly removed it ("there is no git for this server") | user decision | N/A

## Findings (cited - path:lines)
- proxy.yml: Watchtower command = `--interval 3600 --label-enable --cleanup` (verified via SSH)
- nextcloud.yml: 5 services, nextcloud-app/cron share `nextcloud:31-fpm`, DB is postgres:16-alpine (verified)
- media.yml: 7 services, jellyfin uses network_mode: host + /dev/dri (verified)
- photos.yml: 4 services, immich-postgres uses pgvecto-rs:pg14-v0.2.0 (verified)
- Runbook exists at iskradocker: /home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md (verified, 8 steps)
- nextcloud-app/cron currently on older image (tag moved to newer); Watchtower will update on first check after labels added

## Decisions (with rationale)
- Use Watchtower not custom script: user explicitly said "using the script seems overtly complicated" and "i think using watchtower is the best solution"
- Remove --cleanup: user explicitly said "why use --cleanup if its an issue? just dont use it"
- No git: user explicitly said "there is no git for this server fyi, i dont know what the fuck you are doing with git"
- No notifications/timer/script: user said "the script and everything isnt something ill be able to easily manage in the future"

## Scope IN
- Watchtower reconfig (remove --cleanup)
- Labels on: nextcloud-app, nextcloud-web, nextcloud-redis, nextcloud-cron, jellyfin, immich-server, immich-machine-learning, immich-redis
- Immich DB migration (pgvecto → VectorChord) — manual, runbook exists
- AGENTS.md docs

## Scope OUT (Must NOT have)
- Custom update script / systemd timer
- Git on iskradocker
- Email/SMTP notifications
- Labels on DB containers (nextcloud-db, immich-postgres)
- Labels on cliproxyapi (unchanged) or *arr stack
- docker image prune -a (destroys rollback images)

## Open questions
None — user made all key decisions explicitly.

## Approval gate
status: awaiting-approval
Plan written to .omo/plans/iskradocker-watchtower.md. 4 todos, 2 waves. T2 (immich migration) is manual-gated and marked [-~].
