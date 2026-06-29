# iskradocker-watchtower Learnings

## 2026-06-22 T1 completed
- Added Watchtower labels to nextcloud-app, nextcloud-web, nextcloud-redis, nextcloud-cron, jellyfin
- nextcloud-db intentionally NOT labeled (manual-only)
- *arr stack intentionally NOT labeled (out of scope)
- Watchtower config unchanged: --interval 3600 --label-enable --cleanup
- Health probes pass for both Nextcloud and Jellyfin
- Evidence: .omo/evidence/task-1-iskradocker-watchtower.txt

## 2026-06-22 T2 blocked
- Immich DB migration (pgvecto → VectorChord) requires user confirmation
- Runbook exists at iskradocker: /home/antoine/docker/compose/docs/runbooks/immich-pgvecto-to-vectorchord.md
- Waiting for user go-ahead before proceeding
