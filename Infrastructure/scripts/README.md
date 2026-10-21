# Infrastructure Scripts

Operational and verification scripts for ProjectCEA services.

## Approved local commands

```bash
python3 scripts/validate_cluster_topology.py
bash scripts/tests/test-reset-device-registry.sh
bash scripts/tests/test-deploy-candidate.sh
```

Do not extend these to contact production endpoints, databases, Redis, or hardware without explicit owner authorization.
