# ProjectCEA - Agent Guidelines

Read .sisyphus/ files for full context:
- .sisyphus/PROJECT_CONTEXT.md - Technical architecture
- .sisyphus/USER_PREFERENCES.md - User constraints
- .sisyphus/plans/optimization_master_plan.md - Implementation roadmap

## Critical Rules

1. 1/sec sampling NON-NEGOTIABLE
2. 100ms max DB batch, live data instant via Redis
3. VPD is master controller
4. Full TDD for new code
5. No bare excepts
6. Rollback in under 30 seconds

## Locked Decisions

PID: Self-tuning, UI shows K values + reset button
Leaf Temp: Manual day/night delta, interpolates
Data: 1yr full resolution, indefinite aggregates
Safety: Software-only for now
CO2: ASC on, design for FRC when enrichment added

## Current Phase: 1 (Reliability)

1. Fix device_type bug
2. Fix bare excepts
3. Document and verify rollback
4. Add watchdog
5. Implement 100ms DB batching

## Rollback

sudo /opt/projectcea/rollback.sh [release_name]

## Reminders

- MQTT: When adding Lab/Water Management nodes
- IR Camera: Replace manual leaf temp delta
- CO2 FRC: If enrichment added, disable ASC

## CRITICAL LESSON (2026-01-15)

### NEVER MAKE CHANGES WITHOUT UNDERSTANDING THE WHOLE SYSTEM

**What went wrong:**
1. Made changes to device_controller.py (relay sync) without understanding how the system works
2. Modified Grafana datasources without checking existing configuration
3. Reset passwords that didn't need resetting
4. Created duplicate datasources
5. Did not use proper deployment process (deploy.sh/rollback.sh)
6. Made assumptions instead of reading documentation first

**Before ANY change:**
1. READ .sisyphus/PROJECT_CONTEXT.md and requirements only
2. READ AGENTS.md
3. Check git history to understand what exists
4. Understand the deployment process (deploy.sh, rollback.sh)
5. ASK if unsure - do not assume
6. Test in isolation before deploying

**Deployment Process:**
- Development: /home/antoine/ProjectCEA/
- Production: /opt/projectcea/current/ (symlink)
- Deploy: ./deploy.sh (NOT manual copying)
- Rollback: ./rollback.sh (fast, use it if anything breaks)

**If something is working, DO NOT TOUCH IT unless explicitly asked.**
