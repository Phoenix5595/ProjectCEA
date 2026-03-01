# Comprehensive Architecture Refactor Plan

## TL;DR

> **Quick Summary**: Full refactor of automation-service architecture covering Redis key standardization, TTL strategy, Redis Streams event bus, and observability. Builds on existing EventBus and StateManager. Goal: reduce DB load by 90%+, improve performance, enable horizontal scaling.
>
> **Deliverables**:
> - Unified Redis key namespace (`cea:*` prefix) with migration strategy
> - Categorized TTL strategy integrated with StateManager
> - Redis Streams for cross-service events (NOT in-memory)
> - Cache-aside pattern for DB reads (integrated with StateManager)
> - Prometheus metrics for Redis and events
>
> **Estimated Effort**: XL (1-2 weeks)
> **Execution**: Phased (5 phases, sequential within each)

---

## Context

### Current Issues Identified

1. **Redis Key Chaos**: Each module creates keys differently - no consistent naming
2. **TTL Inconsistency**: Ranges from 5s to infinite - unpredictable behavior
3. **Event Bus In-Memory Only**: Uses asyncio.Queue, won't work across containers
4. **DB Overload**: Every schedule read hits database directly
5. **No Observability**: Can't see Redis hit/miss rates, queue depths

### What Already Exists (DO NOT REIMPLEMENT)

| Component | Location | Status | Implication for This Plan |
|-----------|----------|--------|---------------------------|
| **EventBus** | `app/events/__init__.py` | In-memory only | Phase 3: Add Redis Streams, keep in-memory as fallback |
| **StateManager** | `app/state/__init__.py` | Dual-write (memory + Redis) | Phase 2: Extend, don't replace - integrate with cache layer |
| **Redis Client** | `app/redis.py` | Stream writing | Phase 1: Extend, add schema validation |

### Goals

1. **Performance**: Reduce DB load by 90%+ with Redis caching
2. **Maintainability**: Clear key naming, consistent TTLs, documented patterns
3. **Observability**: Metrics for debugging and monitoring
4. **Horizontal Scaling**: Redis Streams for cross-service events

---

## Architecture After Refactor

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TARGET ARCHITECTURE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                      │
│  │    API     │────►│   Redis    │────►│ Control    │                      │
│  │            │     │   Cache    │     │   Loop     │                      │
│  └─────────────┘     └─────────────┘     └─────────────┘                      │
│         │                 │                 ▲                                 │
│         │                 │                 │                                 │
│         ▼                 ▼                 │                                 │
│  ┌───────────────────────────────────────────┐                             │
│  │         Database (TimescaleDB)           │                             │
│  │    - Historical data only                 │                             │
│  │    - Reads cached via Redis              │                             │
│  └───────────────────────────────────────────┘                             │
│                                                                              │
│  ┌───────────────────────────────────────────┐                             │
│  │      Event Bus (Redis Streams)            │                             │
│  │    - Cross-service config events          │                             │
│  │    - Consumer groups for reliability     │                             │
│  │    - In-memory fallback (existing)        │                             │
│  └───────────────────────────────────────────┘                             │
│                                                                              │
│  ┌───────────────────────────────────────────┐                             │
│  │      StateManager (Extended)               │                             │
│  │    - In-memory TTL cache                  │                             │
│  │    - Redis dual-write                     │                             │
│  │    - Schema validation                    │                             │
│  └───────────────────────────────────────────┘                             │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Work Objectives

### Core Objectives

1. **Standardize Redis Keys** - All keys use `cea:` prefix with migration
2. **Define TTL Strategy** - Categorized by data criticality, integrated with StateManager
3. **Add Redis Streams Events** - Replace in-memory EventBus for cross-service
4. **Implement Cache-Aside** - Redis-first for DB reads (integrate with StateManager)
5. **Add Metrics** - Prometheus for Redis operations and events

### Guardrails (From Metis Review)

- **DO NOT replace StateManager** - Extend it with schema validation
- **DO NOT remove in-memory EventBus** - Add Redis Streams alongside it as primary
- **Migration must be reversible** - Rollback script required before key migration
- **Single source of truth**: StateManager for runtime values, Redis for cross-service

---

## TTL Strategy

| Category      | Data Type              | TTL      | Storage           | Rationale                         |
| ------------ | --------------------- | -------- | ----------------- | -------------------------------- |
| **Critical** | mode, failsafe        | None     | Redis (permanent) | Must survive restart              |
| **Runtime**  | setpoints, ramps     | 60s      | StateManager      | Refreshes frequently              |
| **Transient**| sensor values        | 10s      | Redis (direct)    | Always fresh                     |
| **Cached**   | schedules, PID       | 300s     | StateManager      | Cache-aside, refresh on write    |

---

## Redis Key Schema (NEW)

```
# Current patterns (to migrate)
sensor:*           → cea:sensor:*
setpoint:*         → cea:setpoint:*
schedule:*         → cea:schedule:*
ramp:*             → cea:ramp:*
alarm:*            → cea:alarm:*
mode:*             → cea:mode:*
pid:*              → cea:pid:*
heartbeat:*        → cea:heartbeat:*

# New standardized patterns
cea:sensor:{location}/{cluster}/{sensor_type}  → Current sensor value
cea:setpoint:{location}:{cluster}:{device}    → Target values
cea:schedule:{location}:{cluster}             → Active schedules
cea:ramp:{location}:{cluster}:{device}        → Active ramp state
cea:mode:{location}:{cluster}                 → Current mode (CRITICAL - no TTL)
cea:alarm:{location}:{cluster}:{alarm_type}   → Active alarms
cea:heartbeat:{service_name}                  → Service liveness
cea:pid:{device_type}                          → PID parameters
cea:config:{config_type}:{id}                 → Configuration snapshots
```

---

## Implementation Tasks

### Phase 1: Foundation (Days 1-2)

- [x] 1.1 Create Redis key schema constants
  - File: `app/redis/schema.py`
  - Define all key patterns with `cea:` prefix
  - Include migration mapping (old → new)

- [x] 1.2 Define TTL constants
  - File: `app/redis/ttl.py`
  - Categorize: CRITICAL, RUNTIME, TRANSIENT, CACHED
  - Integrate with `app/state/__init__.py`

- [x] 1.3 Add schema validation to Redis client
  - Validate key format before write
  - Enforce TTL category on write

- [x] 1.4 Create migration script (DRY RUN FIRST)
  - Scan existing keys
  - Map to new schema
  - Support rollback

### Phase 2: Cache Layer (Days 3-5)

> **Integration Note**: StateManager (`app/state/__init__.py`) already implements dual-write and in-memory cache. This phase EXTENDS it, not replaces it.

- [x] 2.1 Extend StateManager with schema validation
  - Add key format validation
  - Add TTL category enforcement

- [x] 2.2 Implement cache-aside for schedules
  - Modify `repositories/schedules.py`
  - Check StateManager first, DB on miss
  - Invalidate on write via event

- [x] 2.3 Implement cache-aside for setpoints
  - Modify `repositories/setpoints.py`
  - Same pattern as schedules

- [x] 2.4 Implement cache-aside for PID params
  - Modify `repositories/pid.py`
  - Same pattern

### Phase 3: Event Bus Redis Streams (Days 6-8)

> **Critical**: Existing EventBus uses asyncio.Queue (in-memory). This phase ADDS Redis Streams while keeping in-memory as fallback.

- [x] 3.1 Create Redis Streams event module
  - File: `app/events/redis_streams.py`
  - Stream: `cea:events:config`
  - Consumer group: `cea:events:group`
  - Publish with persistence

- [x] 3.2 Update EventBus to dual-publish
  - Modify `app/events/__init__.py`
  - Publish to both in-memory Queue AND Redis Stream
  - In-memory as fallback if Redis fails

- [x] 3.3 Add Redis event consumer
  - File: `app/events/consumer.py`
  - Consumer group for reliability
  - Acknowledge after processing
  - Fallback: re-publish to in-memory for local handlers

- [x] 3.4 Add backend service consumer
  - Backend subscribes to config events
  - Push to WebSocket on events

### Phase 4: Observability (Days 9-10)

- [x] 4.1 Add Redis metrics
  - Hit/miss rates (via StateManager)
  - Keyspace usage
  - Operation latency

- [x] 4.2 Add event metrics
  - Events published/consumed (both in-memory and Redis)
  - Stream queue depths
  - Processing latency

- [ ] 4.3 Add Grafana dashboard
  - Redis health panels
  - Event bus throughput panels

### Phase 5: Cleanup (Days 11-14)

- [ ] 5.1 Migrate existing keys (with rollback)
  - Dry-run first
  - Monitor for issues
  - Rollback script ready

- [ ] 5.2 Remove old key patterns
  - After successful migration
  - Delete deprecated keys

- [x] 5.3 Update documentation
  - Redis schema docs
  - TTL policy docs
  - Event flow docs
  - Migration runbook

- [ ] 5.4 Load testing
  - Verify cache hit rates
  - Verify event latency
  - Benchmark performance

---

## Verification Strategy

### Test Decision

- **Infrastructure exists**: YES (pytest, existing tests)
- **Automated tests**: Tests-after (add tests for new functionality)
- **Framework**: pytest

### Agent-Executed QA Scenarios (MANDATORY)

> All verification is executed by the agent using tools. No human intervention.

**Scenario: Redis key schema migration**
```
Scenario: Keys migrate from old format to cea: prefix
  Tool: Bash (redis-cli)
  Preconditions: Redis running, old keys exist
  Steps:
    1. SCAN for keys matching "sensor:*" pattern
    2. Run migration script in dry-run mode
    3. Verify no data loss in dry-run output
    4. Run migration for test subset
    5. Verify old key still exists, new key created
    6. Verify data integrity (values match)
    7. Rollback test subset
    8. Verify old key restored
  Expected Result: Migration reversible, no data loss
  Evidence: redis-cli output captured
```

**Scenario: Event bus publishes to Redis Streams**
```
Scenario: Config change publishes to Redis Stream
  Tool: Bash (redis-cli + Python script)
  Preconditions: Automation service running, Redis Streams configured
  Steps:
    1. Trigger config change via API (ramp times)
    2. XREAD from cea:events:config stream
    3. Verify event appears in stream within 1s
    4. Verify event payload contains correct data
    5. Verify consumer group processes event
  Expected Result: Event visible in Redis Stream
  Evidence: XREAD output captured
```

**Scenario: Cache-aside reduces DB queries**
```
Scenario: Schedule reads use cache, DB query only on miss
  Tool: Bash (psql + redis-cli)
  Preconditions: Database and Redis running
  Steps:
    1. Clear schedule cache
    2. Enable query logging on DB
    3. Call schedule API 3 times
    4. Count DB queries (expect 1 - first call only)
    5. Verify Redis has schedule cached
  Expected Result: Only 1 DB query for 3 API calls
  Evidence: Query log output
```

**Scenario: Cross-service event propagation**
```
Scenario: Backend receives config event and pushes to WebSocket
  Tool: Bash (curl for API) + WebSocket client
  Preconditions: Backend running, WebSocket connected
  Steps:
    1. Connect WebSocket client to backend
    2. POST config change to automation API
    3. Wait for WebSocket message (timeout: 5s)
    4. Verify WebSocket receives event
    5. Verify event data matches config change
  Expected Result: Event propagates to backend via Redis Stream
  Evidence: WebSocket message captured
```

---

## Risk Mitigation

| Risk                  | Mitigation                           |
| --------------------- | ------------------------------------ |
| Cache invalidation    | Write-through on all updates via event bus |
| TTL misalignment     | Schema validation at write          |
| Event loss            | Consumer groups with acknowledgment, in-memory fallback |
| Memory growth        | Maxmemory policy + TTL enforcement   |
| Key migration loss   | Dry-run first, rollback script ready |
| StateManager conflict| Extend, don't replace - integrate cache layer |

---

## Commit Strategy

| Phase | Message | Files |
|-------|---------|-------|
| 1 | `refactor: add Redis schema constants and TTL` | app/redis/schema.py, app/redis/ttl.py |
| 2 | `perf: integrate cache-aside with StateManager` | app/state/__init__.py, app/repositories/*.py |
| 3 | `feat: add Redis Streams for cross-service events` | app/events/redis_streams.py, app/events/consumer.py |
| 4 | `feat: add Redis and event observability metrics` | app/metrics/ |
| 5 | `refactor: migrate keys and complete cleanup` | Migration scripts, docs |

---

## Success Criteria

- [ ] All Redis keys use `cea:` prefix
- [ ] TTLs categorized and enforced via StateManager
- [ ] Schedule reads cached (>90% hit rate)
- [ ] Events publish to Redis Streams (cross-service)
- [ ] Consumer group processes events reliably
- [ ] Prometheus metrics for Redis operations
- [ ] Key migration reversible (dry-run + rollback)
- [ ] Documentation complete

---

## Decisions Needed

1. **Source of Truth**: StateManager is runtime source. Redis is cross-service cache. Is this the correct model?
2. **Event Bus Semantics**: Keep in-memory as fallback, Redis Streams as primary. Acceptable?
3. **Migration Timing**: Run key migration in Phase 5. Start with dry-run. Acceptable?
4. **Scope**: Is this plan TOO large? Consider splitting into smaller plans.

---

## Integration with Existing Code

### Files NOT to Modify (Already Working)

| File | Reason |
|------|--------|
| `app/events/__init__.py` | EventBus works - extend, don't replace |
| `app/state/__init__.py` | StateManager works - extend with schema |
| `app/redis.py` | Basic client works - add validation layer |

### Files to Create

| File | Purpose |
|------|---------|
| `app/redis/schema.py` | Key pattern constants |
| `app/redis/ttl.py` | TTL category constants |
| `app/events/redis_streams.py` | Redis Streams publisher |
| `app/events/consumer.py` | Redis event consumer |
| `app/metrics/redis.py` | Redis metrics |
| `app/metrics/events.py` | Event metrics |

### Files to Modify

| File | Changes |
|------|---------|
| `app/state/__init__.py` | Add schema validation |
| `app/repositories/schedules.py` | Cache-aside integration |
| `app/repositories/setpoints.py` | Cache-aside integration |
| `app/repositories/pid.py` | Cache-aside integration |
| `app/events/__init__.py` | Dual-publish to Redis Streams |
