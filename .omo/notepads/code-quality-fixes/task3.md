# Task 3: Create cluster topology validation script

**Date:** 2026-06-02  
**Script:** `Infrastructure/scripts/validate_cluster_topology.py`

## Outcome

Created a standalone CI validation script that checks parity between:
- `Infrastructure/shared/cluster_topology.py` (Python — canonical registry)
- `Infrastructure/frontend/src/config/clusterTopology.ts` (TypeScript — frontend mirror)

## Verification

```
$ python Infrastructure/scripts/validate_cluster_topology.py
✓ Cluster topology parity check PASSED
  Rooms compared: 4
    Flower Room: dev='main', subs=('front', 'back'), url_slugs=('front', 'back')
    Lab: dev='main', subs=(), url_slugs=('main',)
    Outside: dev='main', subs=(), url_slugs=('main',)
    Veg Room: dev='main', subs=(), url_slugs=('main',)
$ echo $?
0
```

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Python parser uses `ast` module | Robust against formatting/comment changes; handles `_RoomTopology(...)` call syntax |
| TS parser uses regex + brace counting | TS is not Python-parseable; file is small and well-structured; regex is sufficient |
| Compare 4 fields: rooms, device clusters, sensor sub-clusters, URL slugs | Covers all contract dimensions from AGENTS.md Cluster Topology Contract |
| Exit 0 on match, 1 on mismatch | Standard CI convention; can be wired into `.pre-commit-config.yaml` or CI pipeline |
| No dependencies beyond stdlib | Zero friction to run in any Python 3.9+ environment |

## Notable Implementation Detail

The Python `_TOPOLOGY` uses an annotated assignment (`_TOPOLOGY: Final[...] = {...}`), which generates an `ast.AnnAssign` node — not `ast.Assign`. The script handles both.

## Next Steps

- Wire into CI pipeline (GitHub Actions / pre-commit)
- Consider adding to `pre-commit-config.yaml` as a local hook
