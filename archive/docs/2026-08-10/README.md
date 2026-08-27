# Documentation Archive — 2026-08-10

This directory holds pre-consolidation, superseded, duplicate, and legacy Markdown. These files are preserved for historical reference only and are not current instructions.

## Moved files

| Original path | Archive path | Reason | Canonical replacement / Historical rationale |
|---|---|---|---|
| `.debug-journal.md` | `archive/docs/2026-08-10/.debug-journal.md` | historical-only | Temporary debug journal for the monitoring performance timeout investigation; not a permanent runbook. |
| `QA-dfr-relay-channel-fixes.md` | `archive/docs/2026-08-10/QA-dfr-relay-channel-fixes.md` | historical-only | One-off QA plan for the dfr-relay-channel-fixes deploy; superseded by the device registry and relay snapshot guidance. |
| `Infrastructure/database-replica/README.md` | `archive/docs/2026-08-10/Infrastructure/database-replica/README.md` | superseded | `Infrastructure/iskra_stack/README.md` |
| `Infrastructure/database/SETPOINTS_TABLE_EXPLANATION.md` | `archive/docs/2026-08-10/Infrastructure/database/SETPOINTS_TABLE_EXPLANATION.md` | duplicate | `Infrastructure/database/REQUIREMENTS.md` |
| `Infrastructure/database/SETPOINTS_RECOMMENDATION.md` | `archive/docs/2026-08-10/Infrastructure/database/SETPOINTS_RECOMMENDATION.md` | duplicate | `Infrastructure/database/REQUIREMENTS.md` |
| `Sensor_Nodes/ESP32/fullV5/README.md` | `archive/docs/2026-08-10/Sensor_Nodes/ESP32/fullV5/README.md` | legacy | `Sensor_Nodes/ESP32/fullV6/README.md` |
| `Sensor_Nodes/ESP32/fullV4/README.md` | `archive/docs/2026-08-10/Sensor_Nodes/ESP32/fullV4/README.md` | legacy | `Sensor_Nodes/ESP32/fullV6/README.md` |
| `Infrastructure/scripts/README-onewire.md` | rewritten to `Infrastructure/onewire-worker-service/README.md` (git mv) | superseded | `Infrastructure/onewire-worker-service/README.md` |
| `Infrastructure/frontend/grafana/README.md` | `archive/docs/2026-08-10/Infrastructure/frontend/grafana/README.md` | superseded | `Infrastructure/iskra_stack/README.md` |
| `Infrastructure/frontend/grafana/REQUIREMENTS.md` | `archive/docs/2026-08-10/Infrastructure/frontend/grafana/REQUIREMENTS.md` | superseded | `Infrastructure/frontend/REQUIREMENTS.md` |
| `Infrastructure/frontend/grafana/SETPOINTS_IN_GRAFANA.md` | `archive/docs/2026-08-10/Infrastructure/frontend/grafana/SETPOINTS_IN_GRAFANA.md` | duplicate | `Infrastructure/database/REQUIREMENTS.md` |
| `Infrastructure/frontend/grafana/alerting/README.md` | `archive/docs/2026-08-10/Infrastructure/frontend/grafana/alerting/README.md` | superseded | `Infrastructure/iskra_stack/README.md` |
| `Infrastructure/frontend/grafana/AGENTS.md` | `archive/docs/2026-08-10/Infrastructure/frontend/grafana/AGENTS.md` | superseded | `Infrastructure/iskra_stack/AGENTS.md` |

## Deferred moves

These files stay in their original locations until a parallel rewrite extracts their usable content.

| Original path | Planned archive path | Blocking todo | Canonical replacement destination |
|---|---|---|---|

## Removed files

These files were deleted per owner decision and are not archived.

| Original path | Status | Reason |
|---|---|---|
| `ARCHITECTURE_SCHEMATIC.md` | removed | removed per owner decision |
| `RASPBERRY_PI_POWER_TRACKING.md` | removed | removed per owner decision |

## Notes

- Existing deployment snapshots in `archive/ARCHITECTURE_2026-07-12.md` and `archive/ARCHITECTURE_SCHEMATIC_2026-07-12.md` were preserved byte-identical.
- The machine-readable inventory for this archive is `archive-manifest.json`.
