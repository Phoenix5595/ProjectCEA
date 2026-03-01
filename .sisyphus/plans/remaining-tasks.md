# Remaining Implementation Tasks - Consolidated Plan

## TL;DR

> **Quick Summary**: Implement remaining items from multiple plans - backend event consumer, Grafana panels, Redis key cleanup, load testing.
> 
> **Deliverables**:
> - Backend Redis Streams consumer for config events → WebSocket
> - Grafana Redis health panels and event metrics
> - Old Redis key cleanup script
> - Load testing verification
> 
> **Estimated Effort**: Medium-Large
> **Execution**: Sequential (dependencies)

---

## Context

### Background
Analysis of 14 plan files revealed most tasks are already complete. This plan addresses the remaining truly incomplete items.

### Metis Review Findings

**CRITICAL GAP**: Backend event consumer does NOT exist
- automation-service consumer pushes to its own in-memory bus, NOT to backend WebSocket
- Need to create NEW backend consumer

**Verified State**:
- Redis datasource exists in Grafana (UID: bf9yw6nuqt81sa)
- Redis keys migrated to cea:* prefix
- StateManager with cache-aside working

### What Was Already Done
- Event bus system (automation-service)
- StateManager with cache-aside
- Redis key migration (cea:* prefix)
- Mode switch synchronization
- Schedule caching

### What's Remaining

| Item | Source Plan | Effort | Status |
|------|-------------|--------|--------|
| Backend service consumer | comprehensive-refactor | Large | **NOT IMPLEMENTED** - Need to create |
| Grafana Redis panels | comprehensive-refactor | Medium | Not implemented |
| Old key cleanup | comprehensive-refactor | Medium | Not implemented |
| Load testing | comprehensive-refactor | Medium | Not implemented |
| Mode switch verification | mode-switch-performance | Medium | Not verified |
| Grafana verification | grafana-optimization-v3 | Medium | Not verified |

---

## Work Objectives

### Core Objectives

1. **Backend Event Consumer** - Backend subscribes to Redis Streams, pushes to WebSocket
2. **Grafana Panels** - Redis health + event metrics panels
3. **Key Cleanup** - Remove old non-cea:* keys after migration verified
4. **Load Testing** - Verify cache hit rates, event latency
5. **Verification** - Test mode switch, Grafana panels

---

## Implementation Tasks

### Task 1: Backend Redis Streams Consumer (CRITICAL)

**What to do**:
- Create `Infrastructure/backend/app/events/redis_consumer.py` (NEW - does NOT exist)
- Subscribe to Redis Stream `cea:events:config` 
- Parse config events and broadcast to WebSocket clients
- Use consumer group for reliability

**Key Files**:
- CREATE: `Infrastructure/backend/app/events/__init__.py`
- CREATE: `Infrastructure/backend/app/events/redis_consumer.py`
- REFERENCE: `automation-service/app/events/consumer.py` (pattern to follow)
- REFERENCE: `backend/app/websocket.py` (WebSocket broadcast)

**Guardrails**:
- MUST NOT modify automation-service event publishing
- MUST use Redis Streams (cea:events:config), NOT in-memory

**Estimated**: Large

---

### Task 2: Grafana Redis Health Panels

**What to do**:
- Add Redis panels to existing dashboards
- Panels: keyspace hits/misses, memory, latency, event throughput
- Use Redis data source plugin or expose metrics via API

**References**:
- `frontend/grafana/dashboards/flower_sector/flower_sector.json`
- `frontend/grafana/dashboards/veg_sector/veg_sector.json`

**Estimated**: Medium

---

### Task 3: Old Key Cleanup Script

**What to do**:
- Create cleanup script to remove old-pattern keys (sensor:*, setpoint:*, etc.)
- Run after verifying migration success
- Backup before delete

**References**:
- `automation-service/scripts/redis_migrate.py` - migration pattern

**Estimated**: Medium

---

### Task 4: Load Testing Verification

**What to do**:
- Run cache hit rate test
- Verify event latency < 100ms
- Test mode switch propagation

**Verification Commands**:
```bash
# Cache hit rate
redis-cli INFO stats | grep keyspace

# Event timing
curl -X POST .../api/mode/set && check logs

# API latency
curl -w "%{time_total}" .../api/setpoints/...
```

**Estimated**: Medium

---

### Task 5: Mode Switch Verification

**What to do**:
- Trigger mode change via API
- Verify scheduler updates immediately (< 2s)
- Verify Redis state reflects new mode

**Estimated**: Small

---

### Task 6: Grafana Verification

**What to do**:
- Verify Redis plugin installed
- Verify panels render correctly
- Check query performance

**Estimated**: Small

---

## Success Criteria

- [ ] Backend consumer running and pushing events to WebSocket
- [ ] Grafana shows Redis health panels
- [ ] Old keys cleaned up from Redis
- [ ] Cache hit rate > 90%
- [ ] Mode switch reflects < 2s
- [ ] All verification tests pass

---

## Archive Action (After Completion)

Move completed plans to archive:
```bash
mkdir -p .sisyphus/archive
mv .sisyphus/plans/fix-logging-shadow.md .sisyphus/archive/
mv .sisyphus/plans/config-event-bus-system.md .sisyphus/archive/
mv .sisyphus/plans/redis-asyncio-hybrid-architecture.md .sisyphus/archive/
mv .sisyphus/plans/event-bus-consolidation.md .sisyphus/archive/
mv .sisyphus/plans/mode-switch-architecture-fix.md .sisyphus/archive/
mv .sisyphus/plans/mode-switch-performance.md .sisyphus/archive/
```
