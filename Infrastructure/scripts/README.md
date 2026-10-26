# Infrastructure Scripts

Operational and verification scripts for ProjectCEA services.

## Approved local commands

```bash
python3 scripts/validate_cluster_topology.py
bash scripts/tests/test-reset-device-registry.sh
bash scripts/tests/test-deploy-candidate.sh
```

Do not extend these to contact production endpoints, databases, Redis, or hardware without explicit owner authorization.

## One-time data scripts

- `clear_soil_sensors_2026-10.sql` — removes the two pre-DFR0845 RS-485 soil identities (registry 4/5, devices 3/6, sensors 14–17 and 30–37, their address history and placements) after the R9 cutover. Guards abort and roll back unless the rows match exactly and hold no measurements; a second run aborts. Owner-authorised for a single run on `sj_cea` right after the Plan 3 deploy; proven on disposable databases by `database/tests/test-soil-placement.sh`.
