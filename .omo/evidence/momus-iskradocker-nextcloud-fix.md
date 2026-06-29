# Momus Review: iskradocker-nextcloud-fix Plan

**Reviewer:** Momus (High-Accuracy Review)  
**Date:** 2026-06-22  
**Plan:** `/home/antoine/ProjectCEA/.omo/plans/iskradocker-nextcloud-fix.md`  
**Draft:** `/home/antoine/ProjectCEA/.omo/drafts/iskradocker-nextcloud-fix.md`  
**Verdict:** REJECT — 4 critical issues, 5 high issues, 4 medium issues. Plan requires revision before execution.

---

## Executive Summary

The plan captures the user's intent correctly and has good structural bones (7 todos, 3 waves, acceptance criteria, QA scenarios). However, it contains **one critical technical error that would cause nginx to fail to start**, several procedural gaps that could cause the upgrade to fail or be unrecoverable, and contradictions in the verification strategy. The plan must be revised before execution.

---

## A. Plan Completeness

### Score: PARTIAL — Missing critical procedural steps

**Todos structure:** All 7 todos (T1-T7) have the required sections: What to do / Must NOT do, Parallelization, References, Acceptance criteria, QA scenarios, Evidence path, and Commit flag. This is good.

**Acceptance criteria:** All acceptance criteria are agent-executable (grep, docker exec, ls, curl). No issues here.

**QA scenarios:** All todos have happy + failure paths. No issues here.

**Evidence paths:** All todos specify `.omo/evidence/task-<N>-iskradocker-nextcloud-fix.{txt,md}`. No issues here.

**Critical gaps in completeness:**

1. **Missing maintenance mode step (CRITICAL):** Nextcloud major upgrades should put the instance in maintenance mode before stopping containers. The official Nextcloud docs state: "You can put your Nextcloud server into maintenance mode before performing upgrades." This prevents partial state corruption if the upgrade is interrupted. The plan jumps straight from T3 (nginx config) to T4 (stop containers) without enabling maintenance mode.

2. **Missing explicit upgrade step (CRITICAL):** The plan assumes that recreating the container with the new image will automatically run the upgrade. While the official Nextcloud Docker image does auto-detect version mismatches and run upgrades on startup, this is an implicit assumption that should be explicit. The plan should either: (a) document that the Docker entrypoint handles this, or (b) include an explicit `occ upgrade` step after container startup. If the auto-upgrade fails silently, the plan has no recovery path.

3. **Missing post-upgrade verification (HIGH):** After a major upgrade, Nextcloud recommends waiting for background migrations to finish. The plan doesn't mention checking `occ status` or running the cron container to complete background jobs.

4. **Rollback procedure missing from plan (HIGH):** The draft has a rollback procedure ("If Nextcloud 32 breaks: docker compose down, change tags back, docker compose up"), but the actual plan file does not include this. The plan only says "Backups run first (T1), so you can rollback if anything goes wrong" without specifying HOW. For a Momus-reviewed plan, the rollback must be explicit and agent-executable.

5. **T3 dependency matrix error (CRITICAL):** T3's dependency matrix says "Blocks: T3" — this is a copy-paste error. It should say "Blocks: T4" (since T4 depends on T2 and T3).

---

## B. Risk Assessment

### Score: ADEQUATE but incomplete

**Risk level appropriateness:** "Medium" risk is appropriate for a Nextcloud major upgrade (31→32). Major version upgrades can have breaking changes, schema migrations, and app compatibility issues.

**Backups assessment:** The 3-backup strategy (Kopia snapshot + DB dump + config.php export) is sufficient. Kopia provides offsite backup to B2, DB dump provides a local restorable copy, and config.php export captures runtime configuration. Good.

**Rollback plan assessment:** INADEQUATE. The plan lacks an explicit rollback procedure. The draft mentions rolling back image tags, but the plan does not. For a major upgrade, the plan must include:
- Exact rollback commands (agent-executable)
- When to trigger rollback (what failure conditions)
- DB restore procedure if schema migrations occurred
- The fact that downgrading Nextcloud DB schema is NOT officially supported — if 32 changes the DB schema, rolling back to 31 requires restoring the DB dump

**Missing failure modes:**

1. **App compatibility (HIGH):** Nextcloud 32 may disable incompatible apps. The plan doesn't mention checking disabled apps or verifying critical apps work post-upgrade.

2. **Background migrations (MEDIUM):** Nextcloud runs background migrations after major upgrades. The plan doesn't verify these complete.

3. **Cron container health (MEDIUM):** The cron container runs background jobs. If the upgrade breaks cron, background migrations stall.

4. **Disk space (MEDIUM):** Major upgrades can temporarily increase disk usage. No pre-check for available disk space.

5. **Trusted domains mismatch (MEDIUM):** The draft identifies that trusted_domains might not include the access URL, but the plan only verifies existing domains without adding the Tailscale access URL if missing.

---

## C. Technical Accuracy

### Score: POOR — Contains a critical syntax error

**1. `nextcloud:32-fpm` tag (CORRECT):** Nextcloud 32 is the current stable major version (released 2025). The `32-fpm` tag tracks the latest 32.x patch release. This is correct and appropriate.

**2. nginx .well-known rewrites (CRITICAL ERROR):**

The plan's T3 includes this location block:
```nginx
location /.well-known/acme-challenge {
    try_uri /.well-known/acme-challenge;
}
```

**`try_uri` is NOT a valid nginx directive.** The correct directive is `try_files`. Per the official Nextcloud nginx documentation (https://docs.nextcloud.com/server/latest/admin_manual/installation/nginx.html), the correct configuration is:

```nginx
location ^~ /.well-known {
    location = /.well-known/carddav { return 301 /remote.php/dav/; }
    location = /.well-known/caldav  { return 301 /remote.php/dav/; }
    location /.well-known/acme-challenge    { try_files $uri $uri/ =404; }
    location /.well-known/pki-validation    { try_files $uri $uri/ =404; }
    return 301 /index.php$request_uri;
}
```

**Issues with the plan's nginx config:**
- `try_uri` will cause nginx to fail to reload/start with "unknown directive" error
- Missing the outer `location ^~ /.well-known` block that prevents regex location conflicts
- Individual `location = /.well-known/nodeinfo` and `location = /.well-known/webfinger` blocks are redundant — the catch-all `return 301 /index.php$request_uri` handles all other `.well-known` URLs
- The plan's approach of scattering individual location blocks may conflict with existing regex locations in the nginx config

**3. occ commands (CORRECT):**
- `docker exec --user www-data nextcloud-app php occ config:system:set default_phone_region --value="CA"` — correct syntax
- `docker exec --user www-data nextcloud-app php occ config:system:set maintenance_window_start --type=integer --value=2` — correct syntax
- `docker exec --user www-data nextcloud-app php occ maintenance:repair --include-expensive` — correct syntax

**4. Locale installation (CORRECT):**
- `echo "en_CA.UTF-8 UTF-8" | sudo tee -a /etc/locale.gen` — correct
- `sudo locale-gen` — correct
- `locale -a | grep -i en_ca` — correct verification

**5. T4 version check timing (HIGH):** T4 step (e) says to verify version by grepping config.php immediately after `sleep 30`. However, the official Nextcloud Docker image runs the upgrade process on container startup when it detects a version mismatch. The version string in config.php may not update until the upgrade completes, which could take 1-5 minutes depending on installation size. Checking after only 30 seconds may show the old version (31.x) and falsely fail the acceptance criteria. The plan should wait for the upgrade to complete (check logs or run `occ status`).

**6. T4 health check (MEDIUM):** T4 step (d) checks `docker inspect nextcloud-app --format '{{.State.Health.Status}}'`, but the plan's own references note "app itself may not have healthcheck". If no healthcheck is defined, this command returns an empty string or fails. The acceptance criteria should use a curl probe to `status.php` instead.

---

## D. Scope Boundaries

### Score: GOOD — Adequately constrained

**Must NOT have section:** The guardrails are comprehensive and appropriate:
- No DB image changes (postgres:16-alpine stays)
- No redis image changes
- No nginx base image changes (only config)
- No Watchtower label removal
- No email/SMTP configuration
- No HTTPS/Caddy enablement
- No git usage on iskradocker
- No proceeding without backups

**Potential scope drift risks:**

1. **T3 nginx config (MEDIUM):** If the nginx config edit goes wrong, the executor might be tempted to rewrite the entire nginx config file. The "Must NOT: break existing location blocks; remove existing config" constraint helps, but there's no explicit instruction to make a backup of the nginx config before editing. This should be added to T1 (backups should include nginx config).

2. **T5 trusted_domains (MEDIUM):** The draft mentions that trusted_domains might need the access URL added, but the plan only says "Verify trusted_domains includes access URL (should already have...)". If the access URL is missing, the plan has no instruction to add it, which could leave Nextcloud inaccessible. However, this is arguably out of scope since the user didn't explicitly ask for it.

---

## E. Verification Wave

### Score: CONTRADICTORY — Strategy conflicts with F3

**F1-F4 structure:** The final verification wave has 4 checks covering plan compliance, artifact cleanup, manual QA, and scope fidelity. This is a reasonable structure.

**Critical contradiction:**

The plan's Verification Strategy states: "Zero human intervention - all verification is agent-executed."

But F3 says: "Real manual QA — verify: Nextcloud admin panel shows 0 critical errors; .well-known URLs return 301; version is 32.0.x; WebDAV test passes"

The term "manual QA" contradicts "zero human intervention." F3 should be reworded to "Automated functional QA" with agent-executable commands:
- `curl -fsS http://127.0.0.1:8082/status.php | grep -q '"version":"32'`
- `curl -I http://127.0.0.1:8082/.well-known/carddav | grep -q "301"`
- `curl -I http://127.0.0.1:8082/.well-known/caldav | grep -q "301"`
- `curl -fsS http://127.0.0.1:8082/remote.php/dav | head -1` (WebDAV probe)

**F2 incompleteness (MEDIUM):** F2 says "No leftover artifacts — verify: no backup files left in /tmp/ (or documented)". The plan creates DB dumps and config exports in /tmp/ but never specifies whether to keep or delete them. For a production system, backup files in /tmp/ should either be moved to a persistent location or explicitly deleted after successful verification. The plan should specify.

**F3 admin panel check (HIGH):** "Nextcloud admin panel shows 0 critical errors" is not agent-executable. The admin panel requires authentication. The plan should use `occ` commands to check for warnings instead, e.g.:
- `docker exec --user www-data nextcloud-app php occ setupchecks` (if available in NC 32)
- Or check the `config.php` for known warning flags

---

## F. User Intent Alignment

### Score: GOOD — Intent correctly captured

**User requirements alignment:**
- ✅ Nextcloud 32 upgrade (trusts stable releases, wants 33+ when available)
- ✅ Direct Tailscale access on port 8082, no Caddy/HTTPS
- ✅ Phone region: CA (Canada)
- ✅ Email: skip
- ✅ Maintenance window: 2 AM
- ✅ setlocale fix: install en_CA.UTF-8

**Decisions correctly captured:**
- TL;DR accurately states: "Upgrade to 32-fpm now — trusts Nextcloud's stable releases"
- TL;DR accurately states: "en_CA locale install — optional quality-of-life fix"
- Must NOT have section correctly captures: "MUST NOT configure email/SMTP (skipped per user request)"
- Must NOT have section correctly captures: "MUST NOT enable HTTPS (direct Tailscale access, no Caddy termination)"

**Discrepancy between draft and plan:**
The draft mentions "Trusted domains don't include access URL | Add mothernode:8080 or Tailscale IP to trusted_domains" as a required fix, but the plan only verifies existing trusted_domains without adding anything. This is a minor discrepancy — the plan treats it as "verify it already works" while the draft identified it as a potential issue. Not a critical mismatch since the user didn't explicitly ask for this.

---

## Summary of Issues

### Critical (Must Fix Before Approval)

| # | Issue | Location | Fix Required |
|---|-------|----------|--------------|
| C1 | `try_uri` is not a valid nginx directive — will cause nginx to fail to start | T3, nginx config | Change to `try_files $uri $uri/ =404;` and restructure to use official `location ^~ /.well-known` block pattern |
| C2 | Missing maintenance mode before upgrade | Between T3 and T4 | Add step: `docker exec --user www-data nextcloud-app php occ maintenance:mode --on` before stopping containers |
| C3 | Missing explicit upgrade handling | T4 | Either document that Docker entrypoint auto-runs upgrade, or add explicit `occ upgrade` step with retry logic |
| C4 | T3 dependency matrix says "Blocks: T3" instead of "Blocks: T4" | T3 metadata | Fix to "Blocks: T4" |

### High (Should Fix Before Approval)

| # | Issue | Location | Fix Required |
|---|-------|----------|--------------|
| H1 | Rollback procedure missing from plan | Plan file | Add explicit rollback todo or section with agent-executable commands, including DB restore warning |
| H2 | T4 version check timing — 30 seconds may be too early | T4 step (e) | Change to poll `occ status` or check container logs for "Starting upgrade..." / "Update successful" instead of fixed sleep |
| H3 | T4 health check may fail if container has no healthcheck | T4 step (d) | Replace `docker inspect ... Health.Status` with curl probe to `status.php` |
| H4 | F3 says "manual QA" but verification strategy says "zero human intervention" | F3 | Reword F3 to "Automated functional QA" with agent-executable curl commands |
| H5 | F3 admin panel check is not agent-executable | F3 | Replace with `occ` commands or curl probes that don't require authentication |

### Medium (Should Fix Before Approval)

| # | Issue | Location | Fix Required |
|---|-------|----------|--------------|
| M1 | nginx config backup not included in T1 | T1 | Add backup of `/home/antoine/docker/compose/nextcloud-nginx.conf` to T1 |
| M2 | Missing background migration verification | After T6 | Add step to run cron container or check `occ status` for pending migrations |
| M3 | F2 doesn't specify whether to keep or delete /tmp/ backups | F2 | Specify: move to persistent location or delete after successful verification |
| M4 | Missing app compatibility check | T4 or T6 | Add `occ app:list` before and after to identify disabled apps |

---

## Verdict

**REJECT**

The plan has good structural foundations and correctly captures user intent, but it contains a **critical nginx syntax error (`try_uri`) that would cause the web server to fail to start**, making Nextcloud inaccessible. Additionally, the missing maintenance mode step, missing explicit upgrade handling, missing rollback procedure, and contradictions in the verification strategy create unacceptable risk for a production Nextcloud instance.

**Required fixes for approval:**
1. Fix the nginx `try_uri` syntax error and restructure to match official Nextcloud nginx config pattern
2. Add maintenance mode step before container stop
3. Add explicit upgrade handling (document auto-upgrade or add `occ upgrade` step)
4. Fix T3 dependency matrix (Blocks: T4)
5. Add explicit rollback procedure to the plan
6. Fix T4 version check to poll for completion instead of fixed 30-second sleep
7. Replace T4 health check with curl probe
8. Reword F3 to be agent-executable (remove "manual QA", add curl commands)
9. Add nginx config backup to T1

After these fixes, the plan should be re-reviewed before execution.
