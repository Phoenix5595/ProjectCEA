# Grafana Resource Assessment

**Date:** 2026-02-06
**System:** Raspberry Pi 5 (mothernode)

## Current Configuration

- **Grafana Status:** Running as native systemd service
- **Uptime:** 6 days (since Jan 30)
- **Plugins:** redis-datasource

## Resource Measurements

| Metric | Value | Notes |
|--------|-------|-------|
| CPU % | 0.7% | 1h 15min total over 6 days |
| Memory | 143 MB (1.7%) | Includes redis-datasource plugin |
| Processes | 2 | Main grafana + redis-datasource |

## Impact Analysis

### Control Loop Impact: **NEGLIGIBLE**

The control loop uses **Redis** for sensor data (<1ms operations), not PostgreSQL.
Grafana queries PostgreSQL aggregates asynchronously and does not compete for the same resources.

### Resource Competition: **MINIMAL**

- Grafana's 0.7% CPU is insignificant on a 4-core Pi 5
- 143MB memory is acceptable (Pi 5 has 8GB RAM typically)
- No I/O contention observed (Grafana reads from DB, control writes to Redis)

## Recommendation

**KEEP** - Removing Grafana would save ~143MB RAM but has **no measurable impact on control loop timing**.

### Rationale:
1. Control loop already achieves <600ms worst case
2. Grafana and control loop use different data paths (Redis vs PostgreSQL)
3. Iskra exists for offloading heavy analytics if needed
4. Local Grafana provides immediate visibility during debugging

### If RAM becomes constrained in future:
- Stop local Grafana: `sudo systemctl stop grafana-server`
- Use Iskra for all visualization
- Estimated savings: ~150MB RAM, ~1% CPU
