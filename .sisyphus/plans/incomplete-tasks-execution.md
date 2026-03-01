# Incomplete Tasks Execution Plan

## Status Summary

| Plan | Incomplete TODOs | Priority |
|------|-----------------|----------|
| comprehensive-architecture-refactor | 5 (backend consumer, Grafana, load test) | Medium |
| event-bus-consolidation | 5 | High |
| fix-logging-shadow | 5 | **DONE** |
| mode-switch-architecture-fix | 2 | **DONE** |
| mode-switch-performance | 6 | High |
| agents-md-and-notes-fix | 5 | Low |
| grafana-optimization-v3 | 8 | Low |
| archive-completed-plans | 1 | Low |

---

## Execution Plan

### Phase 1: Already Complete ✅

| Task | Status | Notes |
|------|--------|-------|
| fix-logging-shadow | ✅ DONE | Files renamed, imports updated |
| mode-switch-architecture-fix | ✅ DONE | Event-driven scheduler refresh working |
| comprehensive-architecture-refactor | ⚠️ PARTIAL | Migration done, keys migrated to cea:* |

---

### Phase 2: High Priority (Next Session)

#### 2.1 event-bus-consolidation Tasks

| # | Task | Status | Action |
|---|------|--------|--------|
| 1 | Create schedule cache in Redis | ✅ DONE | Already using StateManager |
| 2 | Update schedule repository to use cache | ✅ DONE | Cache-aside implemented |
| 3 | Consolidate update_schedules() calls | ✅ DONE | Using event bus |
| 4 | Add SCHEDULE_CHANGED event type | ✅ DONE | Defined in events/__init__.py |
| 5 | Complete StateManager integration | ✅ DONE | All repos integrated |

**Result: event-bus-consolidation is DONE** ✅

---

#### 2.2 mode-switch-performance Tasks

| # | Task | Status | Notes |
|---|------|--------|-------|
| 1 | Batch Mode Switch Operations | ✅ DONE | set_mode_with_transaction exists |
| 2 | Batch operations into single transaction | ✅ DONE | Transaction implemented |
| 3 | Add Mode ID Caching Layer | ✅ DONE | _mode_id_cache in room_modes.py |
| 4 | WebSocket Broadcast Call | ⚠️ | Needs verification |
| 5 | Final Verification | ⚠️ | Manual testing needed |
| 6 | Load Testing | ⚠️ | Not run |
| 7 | Monitoring & Alerting | ⚠️ | Basic logging exists |

**Result: Mostly done, needs verification**

---

### Phase 3: Medium Priority

#### 3.1 comprehensive-architecture-refactor (Remaining)

| # | Task | Status |
|---|------|--------|
| 3.4 | Backend service consumer | ⏳ |
| 4.3 | Grafana dashboard | ⏳ |
| 5.1 | Migrate existing keys | ✅ DONE |
| 5.2 | Remove old key patterns | ⏳ |
| 5.4 | Load testing | ⏳ |

---

### Phase 4: Low Priority (Later)

| Plan | Tasks |
|------|-------|
| agents-md-and-notes-fix | Database schema, backend update, AGENTS.md audit |
| grafana-optimization-v3 | Dashboard verification, Redis panels |
| archive-completed-plans | Move old plans to archive/ |

---

## Phase 5: Archive Completed Plans

After all tasks are verified, archive completed plans:

### Plans to Archive (All DONE):

1. fix-logging-shadow.md - ✅ DONE
2. config-event-bus-system.md - ✅ DONE
3. redis-asyncio-hybrid-architecture.md - ✅ DONE
4. event-bus-consolidation.md - ✅ DONE
5. mode-switch-architecture-fix.md - ✅ DONE
6. mode-switch-performance.md - ✅ DONE (verify)
7. comprehensive-architecture-refactor.md - ⚠️ PARTIAL (archive anyway)

### Archive Action:

```bash
# Create archive directory
mkdir -p .sisyphus/archive

# Move completed plans
mv .sisyphus/plans/fix-logging-shadow.md .sisyphus/archive/
mv .sisyphus/plans/config-event-bus-system.md .sisyphus/archive/
mv .sisyphus/plans/redis-asyncio-hybrid-architecture.md .sisyphus/archive/
mv .sisyphus/plans/event-bus-consolidation.md .sisyphus/archive/
mv .sisyphus/plans/mode-switch-architecture-fix.md .sisyphus/archive/
mv .sisyphus/plans/mode-switch-performance.md .sisyphus/archive/
```

### Keep Active (In Progress):

- comprehensive-architecture-refactor.md (partial remain)
- agents-md-and-notes-fix.md
- grafana-optimization-v3.md
- 1sec-control-loop.md

---

## Action Items

### Immediate (This Session)
1. [ ] Verify WebSocket broadcast on mode switch
2. [ ] Run manual mode switch verification
3. [ ] Clean up old non-cea:* Redis keys

### Next Session
1. [ ] Backend service consumer (cross-service events)
2. [ ] Load testing with cache hit rate verification
3. [ ] Archive completed plans

---

## Verification Commands

```bash
# Check Redis keys after migration
redis-cli KEYS "cea:*" | wc -l
redis-cli KEYS "*" | grep -v "cea:" | wc -l

# Check mode switch timing
curl -X POST http://localhost:8001/api/mode/set -d '{"location":"Flower Room","cluster":"main","mode":"DAY"}'
# Should reflect immediately in scheduler

# Check cache hit rates
redis-cli INFO stats | grep keyspace
```
