# iskradocker-updates Learnings

## 2026-06-22 T1 + T5 completed on iskradocker
- Git repo initialized at /home/antoine/docker/compose/ with baseline commit d7d1fb0
- .gitignore covers .env, .env.*, *.bak, *.backup*, *.disabled, Caddyfile-https
- Runbook committed at docs/runbooks/immich-pgvecto-to-vectorchord.md (c99b54d)
- CRITICAL: Previous workers modified local ProjectCEA repo files (automation-service, frontend, iskra_stack) — reverted via git checkout + clean
- Workers must be explicitly instructed to ONLY operate via SSH on iskradocker, NEVER touch local /home/antoine/ProjectCEA files

## 2026-06-22 T6 blocked
- Immich DB migration (pgvecto-rs → VectorChord) is irreversible and requires user confirmation
- Marked as [-~] in plan until user gives go-ahead

## 2026-06-22 Local repo cleanup incident
- Attempted to revert unintended local changes with git checkout + clean
- git clean -fd removed untracked .omo/ files including the plan file and evidence
- Had to restore directory structure manually
- Lesson: always stash critical untracked files before git clean
