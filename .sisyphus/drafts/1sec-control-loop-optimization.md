# Draft: 1-Second Control Loop Optimization

## Requirements (confirmed)
- **Target**: All control loop calculations must complete within 1 second (max)
- **Current state**: 1-5 second control loop (configurable, 2s default)
- **Constraint**: Deterministic, never exceed 1 second
- **Trade-off accepted**: User willing to drop local Grafana if needed

## Architecture Understanding

### Current Setup (from research)
- **Mothernode (Raspberry Pi 5)**: Runs all control services natively (systemd)
  - automation-service (control loop)
  - can-processor (CAN bus ingestion)
  - soil-sensor-service (RS485 Modbus)
  - weather-service (external API)
  - cea-backend (API + WebSocket)
  - PostgreSQL/TimescaleDB (primary)
  - Redis (live state)
  - Grafana (local - CANDIDATE FOR REMOVAL)

- **Iskra (separate machine)**: Docker stack for analytics
  - TimescaleDB streaming replica (read-only)
  - Redis (synced via redis_sync from replica DB)
  - Grafana (reads PostgreSQL + Redis)
  
### Key Insight
Iskra already exists for offloading analytics! If mothernode Grafana is removed:
- Analytics queries don't hit mothernode PostgreSQL
- No Grafana rendering load on Pi 5
- Database can focus on writes from ingestion + reads from control loop

## Research Findings (COMPLETED)

### 1. Control Loop Timing Analysis ✅
- **Current tick**: 3 seconds (configurable 1-5s in config)
- **Best case execution**: ~40ms
- **Worst case execution**: ~570ms ← ALREADY UNDER 1 SECOND!
- **Primary bottleneck**: DFR0971 I2C dimming (50-290ms per operation with delays/retries)
- **Secondary**: MCP23017 relays (5-20ms), Sensor reads (5-50ms, 30s cache), Control logic (10-100ms), PID calcs (5-20ms)
- **Built-in perf monitor**: `/api/status` endpoint
- **Key files**: `background_tasks.py` (loop), `control_engine.py` (orchestration), `dfr0971.py` (bottleneck)

### 2. Database/Redis Performance ✅
- **Redis operations**: <1ms (control loop uses Redis exclusively)
- **TimescaleDB**: Well-optimized with hypertables, 1-day chunks, 90-day compression (70-90% savings)
- **Continuous aggregates**: Hourly/daily pre-computed
- **Connection pooling**: Backend=10, Automation dual pools (state=20, stream=10)
- **Opportunities**: Enable commented materialized view for latest values, MGET batching, longer TTL for setpoints

### 3. Grafana Impact Assessment ✅
- **Dual setup**: Mothernode (native systemd) + Iskra (Docker stack)
- **Control loop impact**: LOW - control uses Redis, Grafana queries PostgreSQL aggregates separately
- **Iskra architecture**: TimescaleDB streaming replica + Redis (synced every 10s) + Grafana
- **Conclusion**: Removing mothernode Grafana possible but NOT high-impact for control loop

### 4. Sensor Ingestion Latency ✅
- **CAN Processor**: <10ms processing, immediate Redis, async DB batching (100ms/50 msgs)
- **Soil Sensor**: 5s polling, ~100-500ms per sensor (Modbus RTU)
- **Weather**: 15min polling, 1-5s processing
- **All Redis writes**: <1ms
- **Conclusion**: No bottlenecks - all SLAs met

### 5. System Resources/Hardware ⏳ (waiting)
- Pending: I2C bus performance analysis, service resource footprints

## KEY INSIGHT 🎯

**The system is ALREADY capable of 1-second loops!**

Current worst case (~570ms) is well under 1 second. The question is:
1. What edge cases could push it over?
2. How do we guarantee determinism?
3. What safety margins do we need?

## Optimization Opportunities Identified

### High Impact
1. **DFR0971 I2C optimization** - Primary bottleneck (50-290ms)
   - Reduce retry delays?
   - Parallel I2C operations on different buses?
   - Pre-calculate dimming values?

2. **Parallel hardware operations**
   - MCP23017 (bus 0) and DFR0971 (bus 1) are on different I2C buses
   - Can execute simultaneously with asyncio

### Medium Impact
3. **Redis MGET batching** - Read multiple keys in single round-trip
4. **Materialized view for latest values** - Already defined but commented out
5. **Longer TTL for stable data** - Setpoints don't change often

### Low Impact (given findings)
6. **Remove mothernode Grafana** - Control loop doesn't query it anyway
   - Still worth doing for resource cleanup
   - Reduces PostgreSQL query load from dashboard refreshes

## Open Questions (Remaining)
- What's the Pi 5 CPU/memory headroom during peak control loop?
- Are there GC pauses or other Python runtime concerns?
- What happens under system load (multiple zones, many devices)?

## Scope Boundaries
- INCLUDE: All optimizations to achieve 1-second control loop
- INCLUDE: Removal of local Grafana if beneficial
- INCLUDE: Database/Redis tuning
- INCLUDE: Service architecture changes
- EXCLUDE: Hardware upgrades (staying on Pi 5)
- EXCLUDE: ESP32 firmware changes (unless necessary)
- EXCLUDE: Functionality reduction (must maintain all control features)
