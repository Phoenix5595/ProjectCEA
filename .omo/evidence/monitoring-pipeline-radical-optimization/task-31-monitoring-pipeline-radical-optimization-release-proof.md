# T31 — Release reconstruction and rollback readiness

## Reconstruction proof
- Deploy model: releases ship the committed working tree (owner-acknowledged mid-plan); byte identity between the deployed release and the live worktree is enforced continuously by the T3 allowlist verifier (127 release inputs, verify PASSED against `20260825-215608-b8e1099`).
- Worktree-vs-release source diff: **1 entry** = `automation-service/automation_config.restart_hash`, a runtime-generated restart sidecar written by the container after deploy. Not source; excluded from identity semantics.
- Frontend determinism: `npm run build` from the identical tree reproduces `dist/` **byte-for-byte** (0 diffs, content-hashed filenames included) against the deployed release.

## Sandbox
`test-deploy-candidate.sh` — **PASS** (15 scenarios): preflight gating, active-candidate rejection, finalize promotion, rollback restore of monitoring routes/assets, identity-artifact isolation from unrelated dirty files.

## Operator ledger
| Candidate | Release | Content | State |
|---|---|---|---|
| A | 20260825-103907-d5b2111 | backend point-budgeting, redis→503, CAGG grouping | finalized, superseded |
| B | 20260825-163148-7f8a968 | frontend budgets/orchestration/cancellation/alignment/chart-feed | finalized; **rollback target for C** |
| C | **20260825-215608-b8e1099** | publications end-to-end, tier escalation, fidelity budgets, domain fix | **CURRENT LAST-GOOD** |

- Schema migrations: **none** in this plan (existing CAGGs/columns queried only) — nothing to reverse.
- Recovery: `./rollback-deploy.sh` (restore), `./finalize-deploy.sh --confirm` (promote), T3 verifier (identity).
- No production rollback drill was performed; no active service/data modified by this task.
