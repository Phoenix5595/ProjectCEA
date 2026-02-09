# AGENTS.md Audit Report

**Generated:** 2026-02-06 | **Session:** ses_3cc070d20fferTHhQ5TiWpt9Gd

## Summary

| File | Complexity Score | Status | Priority |
|------|-----------------|--------|----------|
| Infrastructure/AGENTS.md | 93 | needs-update | HIGH |
| Infrastructure/automation-service/AGENTS.md | 74 | accurate | LOW |
| Infrastructure/backend/AGENTS.md | 30 | accurate | LOW |
| Infrastructure/frontend/AGENTS.md | 68 | accurate | LOW |
| Infrastructure/database/AGENTS.md | 47 | needs-update | MEDIUM |
| Infrastructure/can-processor-service/AGENTS.md | 32 | accurate | LOW |
| Sensor_Nodes/AGENTS.md | 7 | accurate | LOW |

**Action Required:** 2 files need updates

---

## Detailed Findings

### Infrastructure/AGENTS.md
- **Complexity Score**: 93
- **Status**: needs-update
- **Hierarchy Compliance**: YES (root file)
- **Issues Found**:
  - [ ] Missing `iskra_stack/` service documentation
  - [ ] Missing `onewire-worker-service/` documentation
  - [ ] Missing `database-replica/` documentation
  - [ ] Missing `sensor-query-service/` documentation
  - [ ] Missing `scripts/` directory documentation
  - [ ] Structure diagram outdated (shows 6 services, now 10+)
- **Recommended Changes**: Add new services to structure diagram and service list

### Infrastructure/automation-service/AGENTS.md
- **Complexity Score**: 74
- **Status**: accurate
- **Hierarchy Compliance**: YES (references control/AGENTS.md for deep dive)
- **Issues Found**: None
- **Notes**: Well-structured, mentions config_schema.py, container.py, hardware I2C patterns

### Infrastructure/backend/AGENTS.md
- **Complexity Score**: 30
- **Status**: accurate
- **Hierarchy Compliance**: YES
- **Issues Found**: None
- **Notes**: 60 lines, query strategy documented, anti-patterns listed

### Infrastructure/frontend/AGENTS.md
- **Complexity Score**: 68
- **Status**: accurate
- **Hierarchy Compliance**: YES (references grafana/ subdirectory docs)
- **Issues Found**: None
- **Notes**: Key components with line counts documented

### Infrastructure/database/AGENTS.md
- **Complexity Score**: 47
- **Status**: needs-update
- **Hierarchy Compliance**: YES
- **Issues Found**:
  - [ ] Missing `notes` table documentation (just added for dashboard notes persistence)
- **Recommended Changes**: Add notes table to schema documentation

### Infrastructure/can-processor-service/AGENTS.md
- **Complexity Score**: 32
- **Status**: accurate
- **Hierarchy Compliance**: YES
- **Issues Found**: None
- **Notes**: Documents async batching, node mapping, MQTT future reminder

### Sensor_Nodes/AGENTS.md
- **Complexity Score**: 7 (below threshold but kept)
- **Status**: accurate
- **Hierarchy Compliance**: YES
- **Issues Found**: None
- **Notes**: Low score but contains valuable node ID mapping and CAN protocol info

---

## Update Plan

### HIGH Priority (Infrastructure/AGENTS.md)
1. Add new services to structure diagram
2. Document iskra_stack, onewire-worker-service, database-replica, sensor-query-service
3. Add scripts/ directory documentation

### MEDIUM Priority (Infrastructure/database/AGENTS.md)
1. Add notes table to schema section
