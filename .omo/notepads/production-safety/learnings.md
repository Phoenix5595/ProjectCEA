# Production Safety - Learnings

## 2026-07-12 - Plan Execution Start
- Plan: production-safety.md (716 lines, 11 todos, 6 waves)
- Boulder: production-safety-92d90761, status=active
- Wave 1 starting: T1 (alembic), T2 (DELETE guard), T3 (F3 ban) — all independent, parallel dispatch

## Key Conventions
- All acceptance criteria use `cea_sensors_test`, NEVER `cea_sensors` (production)
- Do NOT deploy between waves — only T11 deploys
- F3 permanently banned from production HTTP
- 10% hardcoded default for missing light_target_intensity rows
- mode_parameters.main_light_intensity/supplemental_light_intensity DEPRECATED
- Zero new Redis keys — all new caches are in-memory Python dicts
- Atomic reference swaps in all update_*() methods
- asyncio.Event startup gate prevents control loop from ticking before data loaded
- Mid-ramp target change recalculation MUST be preserved in T5 rewrite

## 2026-07-12 - T2 DELETE Guard Implementation
- Added `X-Confirm-Destructive: true` header requirement to `DELETE /api/devices/registry/{device_id}` in production only (`is_production()` gate)
- Files modified: `Infrastructure/automation-service/app/routes/devices_crud.py`, `Infrastructure/frontend/src/services/api.ts`
- Backend: `delete_registry_device()` now checks `request.headers.get("X-Confirm-Destructive") != "true"` and raises HTTP 403 in production
- Frontend: `deleteDevice()` and `deleteLight()` in `api.ts` both send the header unconditionally
- No UI confirm dialog added; no cascade behavior changed; dev/test mode unaffected

## 2026-07-12 - T3 Complete: Subagent QA Safety Section Added to AGENTS.md
- Added "Subagent QA Safety (Critical — Permanent Ban)" under NON-NEGOTIABLE SYSTEM RULES
- F3 permanently banned from production HTTP; replaced with static checks (ruff, pytest, tsc, build, vitest, grep)
- All subagents banned from DELETE/POST/PUT against production endpoints
- Exception: guard-verification probe with non-existent device ID (e.g., 999) allowed
- Production endpoints 8000/8001/8003: GET read-only only
- File modified: AGENTS.md (project root)
- Verification: grep confirms "Subagent QA Safety" and "PERMANENTLY BANNED" present
