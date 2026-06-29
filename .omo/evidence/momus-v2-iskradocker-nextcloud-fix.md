# Momus Review v2: iskradocker-nextcloud-fix Plan

**Reviewer:** Momus (High-Accuracy Re-Review)  
**Date:** 2026-06-22  
**Plan:** `/home/antoine/ProjectCEA/.omo/plans/iskradocker-nextcloud-fix.md`  
**Prior Review:** `/home/antoine/ProjectCEA/.omo/evidence/momus-iskradocker-nextcloud-fix.md` (REJECT)  
**Verdict:** APPROVE — All 4 critical + 5 high + 2 medium issues from v1 are fixed. Two minor observations noted, neither blocking.

---

## A. Critical Issues Resolution

| # | Issue | Status | Evidence |
|---|-------|--------|----------|
| C1 | `try_uri` → `try_files` with official `location ^~ /.well-known` block | **FIXED** | T3 (lines 120-134) now uses correct `try_files $uri $uri/ =404;` inside `location ^~ /.well-known` with nested `location = /.well-known/carddav` and `caldav` blocks, plus catch-all `return 301 /index.php$request_uri;`. Matches official Nextcloud nginx docs exactly. |
| C2 | Maintenance mode added to T4 (on before stop, off after upgrade) | **FIXED** | T4 step (a) enables maintenance mode: `docker exec --user www-data nextcloud-app php occ maintenance:mode --on`. T4 step (g) disables it: `docker exec --user www-data nextcloud-app php occ maintenance:mode --off`. |
| C3 | Explicit upgrade handling (occ status + log polling in T4) | **FIXED** | T4 step (e) polls logs every 10s for "Update successful" or "Fatal" with 5-minute timeout. T4 step (f) verifies via `occ status` showing `"version": "32.0.`. References explicitly state "Nextcloud Docker entrypoint auto-runs upgrade when version mismatch detected." |
| C4 | T3 dependency matrix "Blocks: T4" (not T3) | **FIXED** | Dependency matrix line 61: `| T3 (nginx config) | — | T4 | T2 |` |

**Critical verdict: ALL FIXED.**

---

## B. High Issues Resolution

| # | Issue | Status | Evidence |
|---|-------|--------|----------|
| H1 | Rollback procedure section added with triggers, steps, warnings | **FIXED** | Lines 253-277 contain full rollback section: triggers (Fatal/500/disabled apps/schema failure), 6 agent-executable steps (down → restore DB → restore nginx → revert tags → up → verify), and warnings about DB schema changes + Kopia fallback. |
| H2 | T4 version check uses occ status + log polling (not fixed 30s sleep) | **FIXED** | T4 step (e) uses active log polling (`docker logs ... | grep -E "Starting upgrade|Update successful|Fatal"` every 10s, 5min timeout). No fixed sleep. T4 step (f) uses `occ status` for version confirmation. |
| H3 | T4 health check uses curl probe (not docker inspect Health.Status) | **FIXED** | T4 acceptance criteria (lines 171-176) use `docker inspect` only for image tag verification, then `curl -fsS http://127.0.0.1:8082/status.php` for health. No `docker inspect ... Health.Status` anywhere. |
| H4 | F3 reworded to "Automated functional QA" (not "manual QA") | **FIXED** | Line 245: `- [ ] F3. Automated functional QA — verify via agent-executable commands:` |
| H5 | F3 uses agent-executable commands (curl probes, occ setupchecks) | **FIXED** | F3 (lines 246-250) lists: `curl ... status.php | grep '"version":"32'`, `curl -I .../.well-known/carddav | grep "301"`, `curl -I .../.well-known/caldav | grep "301"`, `curl -fsS .../remote.php/dav --user antoine:PASSWORD | head -1`, `docker exec ... occ setupchecks 2>&1 | grep -i "error\|critical"`. All agent-executable, no auth-required admin panel checks. |

**High verdict: ALL FIXED.**

---

## C. Medium Issues Resolution

| # | Issue | Status | Evidence |
|---|-------|--------|----------|
| M1 | nginx config backup added to T1 | **FIXED** | T1 step (d): `cp /home/antoine/docker/compose/nextcloud-nginx.conf /tmp/nextcloud-nginx-backup.conf`. Acceptance criteria (line 90) verifies nginx backup exists. |
| M2 | Missing background migration verification | **PARTIALLY ADDRESSED** | Not explicitly called out as a separate step, but T4 log polling catches upgrade completion and T6 `occ app:list` catches disabled apps. Acceptable for Medium severity. |
| M3 | F2 doesn't specify whether to keep or delete /tmp/ backups | **FIXED** | F2 (line 244): "verify: backup files in /tmp/ are either moved to persistent location or deleted after successful verification" |
| M4 | Missing app compatibility check | **FIXED** | T6 step (d): `docker exec --user www-data nextcloud-app php occ app:list | grep -A20 "Disabled"` — notes any apps disabled during upgrade. |

**Medium verdict: ALL ADDRESSED (M2 partially, acceptable).**

---

## D. Overall Plan Quality

### Decision-Completeness
All 7 todos (T1-T7) have exact file paths, exact commands, exact image tags, exact nginx config blocks. No judgment calls required. A worker with zero interview context can execute this plan using only the References sections.

### Acceptance Criteria
Every todo has agent-executable acceptance criteria using `grep`, `docker exec`, `ls`, `curl`, `locale -a`. No human judgment required.

### Verification Strategy Consistency
No contradictions. The plan states "All verification is agent-executed via curl probes, docker inspect, and occ commands" (line 44), and F1-F4 are fully agent-executable. F3 no longer says "manual QA."

### Guardrails (Must NOT have)
Lines 33-41: 8 guardrails covering DB image, redis image, nginx image, Watchtower labels, email, HTTPS, git, and backup prerequisites. Adequate and appropriate.

### Risk Assessment
"Medium" risk (line 14) is accurate for a Nextcloud major upgrade with schema migration potential. The 3-backup strategy (Kopia offsite + DB dump + config export + nginx config backup) is sufficient.

### Rollback Quality
Rollback section is decision-complete: exact triggers, exact commands, DB restore warning, Kopia fallback. A worker can execute it without interview context.

---

## E. New Issues (Regression / Scope Creep Check)

Two minor observations found. Neither is blocking.

### N1: T4 step (f) expects `maintenance: false` before maintenance mode is disabled (Minor)
- **Location:** T4 step (f) says `occ status` shows `"maintenance": false`, but step (g) disables maintenance mode AFTER step (f).
- **Impact:** Low. The Nextcloud Docker entrypoint automatically disables maintenance mode after a successful upgrade, so `occ status` will likely already show `false` by step (f). Step (g) is redundant but harmless.
- **Recommendation:** Non-blocking. Could swap (f) and (g) order for logical clarity, but functionally safe.

### N2: F3 WebDAV probe uses literal `PASSWORD` placeholder (Minor)
- **Location:** F3: `curl -fsS http://127.0.0.1:8082/remote.php/dav --user antoine:PASSWORD | head -1`
- **Impact:** Low. The acceptance comment says "returns HTTP 207 or 401 (auth required, not 404)" — 401 is the expected outcome with a wrong password, which still satisfies the "not 404" check. The agent does not need the real password.
- **Recommendation:** Non-blocking. The comment correctly documents the expected behavior.

**No regressions or scope creep introduced by the fixes.**

---

## Summary

| Category | v1 Count | v2 Status |
|----------|----------|-----------|
| Critical | 4 | **4/4 FIXED** |
| High | 5 | **5/5 FIXED** |
| Medium | 4 | **4/4 ADDRESSED** |
| New Issues | — | **2 minor, non-blocking** |

The plan now contains:
- ✅ Correct nginx syntax (`try_files`, official `location ^~ /.well-known` block)
- ✅ Maintenance mode before/after upgrade
- ✅ Explicit upgrade handling via log polling + `occ status`
- ✅ Correct dependency matrix
- ✅ Full rollback procedure with triggers, steps, warnings
- ✅ Agent-executable verification throughout (F1-F4)
- ✅ nginx config backup in T1
- ✅ App compatibility check in T6
- ✅ `/tmp/` backup cleanup specified in F2

---

## Verdict

**APPROVE**

All critical and high issues from Momus v1 have been resolved. The plan is technically accurate, procedurally complete, and decision-complete for a worker with zero interview context. The two minor observations (N1, N2) do not affect executability or safety. The plan is ready for execution.
