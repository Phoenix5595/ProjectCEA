# Infrastructure Scripts

Operational and verification scripts for ProjectCEA services.

## Approved local commands

```bash
python3 scripts/validate_cluster_topology.py
bash scripts/tests/test-reset-device-registry.sh
bash scripts/tests/test-deploy-candidate.sh
```

Do not extend these to contact production endpoints, databases, Redis, or hardware without explicit owner authorization.

## Guarded duplicate-key purge runbook (`redis_duplicate_key_purge.py`)

Purpose: remove legacy-namespace Redis keys left behind by the monitoring/Redis canonicalization plan (Tasks 18–24) after parity has been verified in production. The tool is **read-only by default**; it never runs unattended.

### Eligible legacy families

Discovered via fixed globs only — anything else is refused as UNKNOWN:

| Family | Legacy glob | Canonical counterpart |
|---|---|---|
| sensor last-good | `sensor:*:*:last_good` | `cea:sensor:global:{cluster}:{name}_last_good` |
| setpoint fields / rate-limit | `setpoint:*` | `cea:setpoint:{loc}:{clu}:{field}[:last_write]` |
| effective setpoints (climate + per-light) | `effective_setpoint:*` | `cea:effective_setpoint:...` |
| light state | `light:{loc}:{clu}:{dev}` | `cea:light:...` |
| automation device state (+`:ts`) | `automation:*` | `cea:automation:...` |
| ramp active / persisted | `ramp:*`, `ramp_persist:*` | `cea:ramp...` |
| mode | `mode:{loc}:{clu}` | `cea:mode:...` |
| alarm | `alarm:{loc}:{clu}:{name}` | `cea:alarm:...` |
| heartbeat | `heartbeat:{service}` | `cea:heartbeat:global:default:{service}` |
| PID parameter caches (all shapes incl. `all`) | `pid:parameters:*` | `cea:pid:...` |

### Never touched (active / control-critical)

- `failsafe:*` — T18 KEEP verdict; intentionally legacy until exhaustive consumer proof.
- `pid:autotune:*` — still-active legacy-shape namespace.
- `sensor:raw`, `stream:control` — active streams.
- `schedules:*` — active schedule caches.
- `automation:degraded` — distinct self-consistent health pair.
- Every `cea:*` key.

### Procedure

1. **Dry-run (read-only, safe):**
   ```bash
   python3 Infrastructure/scripts/redis_duplicate_key_purge.py --redis-url "$REDIS_URL"
   ```
   Prints scan summary, exclusions, per-key status, and the exact `DEL` plan.
   Exit codes: `0` clean · `2` value mismatch · `3` unknown pattern refused · `4` blocked (missing canonical twin) · `5` misuse.

2. **Investigate any nonzero exit.** Mismatches mean the two namespaces disagree — do not delete. Unknown patterns must be classified before proceeding. Blocked keys would lose data.

3. **Deletion requires an owner-authorized production operation plus BOTH flags:**
   ```bash
   python3 Infrastructure/scripts/redis_duplicate_key_purge.py \
     --redis-url "$REDIS_URL" \
     --confirm PURGE-LEGACY-DUPLICATE-KEYS \
     --owner-approval "<ticket/approval reference>"
   ```
   Deletion re-runs discovery first and refuses unless every candidate is parity-clean; then deletes each legacy key individually (canonical keys are never deleted).

4. **Post-purge:** re-run the dry-run; expect zero eligible keys and no unknowns.

### Local behavior proof

Fixture-based scenarios (fake Redis, no network) cover clean parity, value-mismatch refusal, unknown-pattern refusal, missing-twin blocking, and deletion gating. See `.omo/evidence/botanical-color-spectrum-monitoring-recovery/T24/purge-dry-run.txt`.
