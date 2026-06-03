# Plan: Fix can-processor Watchdog Timeouts

**Created:** 2026-01-19
**Priority:** P0 - Critical (causing system crashes)
**Estimated Time:** 2-3 hours
**Status:** Ready for implementation

---

## Problem Statement

The `can-processor.service` experiences repeated watchdog timeouts (30s limit), causing service restarts and contributing to system instability. Crash logs show:
- Jan 17: 8 watchdog timeouts between 19:12-19:51
- Jan 18: 2 watchdog timeouts at 18:18, 18:32
- Jan 19: 12+ timeouts between 08:46-09:28 (crash storm)

## Root Cause Analysis

### Primary Issue: Synchronous DB Writes Block Main Loop

The async batching infrastructure **already exists** but **is not being used**:

```python
# writer.py line 512 - CURRENT (blocking):
result["db"] = self.write_to_db(decoded, raw_data, sensors, timestamp)

# writer.py line 304 - EXISTING but unused:
def queue_db_write(self, decoded, raw_data, sensors, timestamp):
    # This queues to _db_queue for async processing
    # But it's NEVER CALLED from write()
```

### Secondary Issues

1. **No connection timeout** on `psycopg2.connect()` (writer.py:98)
2. **No query timeout** on `SELECT 1` health check (writer.py:202)
3. **Health check before every write** compounds blocking (writer.py:347)
4. **7 bare except clauses** swallow errors silently

---

## Implementation Plan

### Phase 1: Critical Fix - Enable Async DB Writes (P0)

#### Task 1.1: Modify write() to use async queue

**File:** `Infrastructure/can-processor-service/app/writer.py`

**Current code (line 506-517):**
```python
def write(self, msg, decoded, raw_data, sensors, timestamp, timestamp_ms):
    result = {"stream": False, "db": False, "redis": False}
    result["stream"] = self.write_to_stream(msg, decoded)
    result["db"] = self.write_to_db(decoded, raw_data, sensors, timestamp)  # BLOCKING!
    result["redis"] = self.write_to_redis_state(sensors, timestamp_ms)
    return result
```

**Change to:**
```python
def write(self, msg, decoded, raw_data, sensors, timestamp, timestamp_ms):
    result = {"stream": False, "db": False, "redis": False}
    result["stream"] = self.write_to_stream(msg, decoded)
    result["db"] = self.queue_db_write(decoded, raw_data, sensors, timestamp)  # NON-BLOCKING
    result["redis"] = self.write_to_redis_state(sensors, timestamp_ms)
    return result
```

**Verification:**
- Run `lsp_diagnostics` on writer.py
- Check service starts: `sudo systemctl restart can-processor && sleep 5 && systemctl status can-processor`
- Monitor logs: `journalctl -u can-processor -f` for 2 minutes
- Verify DB writes working: `psql -U cea_user -d cea_sensors -c "SELECT COUNT(*) FROM measurement WHERE time > NOW() - INTERVAL '1 minute';"`

#### Task 1.2: Add connection timeout to psycopg2

**File:** `Infrastructure/can-processor-service/app/writer.py`

**Current code (line 91-98):**
```python
db_config_optimized = self.db_config.copy()
db_config_optimized["keepalives"] = 1
db_config_optimized["keepalives_idle"] = 30
db_config_optimized["keepalives_interval"] = 10
db_config_optimized["keepalives_count"] = 3
self.db_conn = psycopg2.connect(**db_config_optimized)
```

**Add after keepalives_count (before connect):**
```python
db_config_optimized["connect_timeout"] = 5  # 5 second connection timeout
```

#### Task 1.3: Add statement timeout for queries

**File:** `Infrastructure/can-processor-service/app/writer.py`

**In connect_db() after line 99 (`self.db_conn = psycopg2.connect(...)`):**
```python
# Set statement timeout to prevent long-running queries
cursor = self.db_conn.cursor()
cursor.execute("SET statement_timeout = '5000'")  # 5 second query timeout
cursor.close()
```

**Also in _flush_batch() after getting cursor (line 148):**
```python
cursor = self.db_conn.cursor()
cursor.execute("SET statement_timeout = '5000'")  # Ensure timeout on batch thread
```

---

### Phase 2: Error Handling Fixes (P1)

#### Task 2.1: Fix bare except in writer.py close() method

**File:** `Infrastructure/can-processor-service/app/writer.py`

**Lines 529, 540, 547, 554 - Replace bare excepts with logged exceptions:**

```python
# Line 529 - queue emptying (change from):
except:
    break
# To:
except Exception as e:
    logger.debug(f"Queue empty or error during drain: {e}")
    break

# Lines 540, 547, 554 - resource cleanup (change from):
except Exception:
    pass
# To:
except Exception as e:
    logger.debug(f"Error during cleanup: {e}")
```

#### Task 2.2: Fix bare except in writer.py _check_db_connection()

**File:** `Infrastructure/can-processor-service/app/writer.py`

**Line 211 - Replace:**
```python
except Exception:
    pass
```
**With:**
```python
except Exception as e:
    logger.debug(f"Error closing stale connection: {e}")
```

#### Task 2.3: Fix bare except in can_reader.py close()

**File:** `Infrastructure/can-processor-service/app/can_reader.py`

**Line 109 - Replace:**
```python
except Exception:
    pass
```
**With:**
```python
except Exception as e:
    logger.debug(f"Error shutting down CAN bus: {e}")
```

---

### Phase 3: Monitoring & Hardening (P2)

#### Task 3.1: Add periodic stats logging

**File:** `Infrastructure/can-processor-service/app/main.py`

**In the main loop (around line 310), add stats logging every 60 seconds:**

```python
# Add at module level (after line 46):
last_stats_log = 0.0
STATS_LOG_INTERVAL = 60  # seconds

# In the main loop, after watchdog ping (around line 311):
if now - last_stats_log >= STATS_LOG_INTERVAL:
    stats = data_writer.get_stats()
    logger.info(f"DB batch stats: queued={stats['queued']}, flushed={stats['flushed']}, "
                f"dropped={stats['dropped']}, pending={stats['pending']}")
    last_stats_log = now
```

#### Task 3.2: Add queue depth warning

**File:** `Infrastructure/can-processor-service/app/writer.py`

**In queue_db_write() method (around line 309):**

```python
def queue_db_write(self, decoded, raw_data, sensors, timestamp):
    if not self.db_enabled:
        return False
    item = DBWriteItem(decoded=decoded, raw_data=raw_data, sensors=sensors, timestamp=timestamp)
    try:
        self._db_queue.put_nowait(item)
        self._queued_count += 1
        # Warn if queue is getting full (>80% capacity)
        queue_size = self._db_queue.qsize()
        if queue_size > 8000 and queue_size % 1000 == 0:  # Log every 1000 items above 8000
            logger.warning(f"DB write queue depth high: {queue_size}/10000")
        return True
    except queue.Full:
        self._dropped_count += 1
        logger.error("DB write queue full, dropping measurement")
        return False
```

---

## Test Plan

### Unit Tests (if test infrastructure exists)

1. **Test async queue write:**
   - Mock DB connection
   - Call `write()` method
   - Verify `queue_db_write()` is called (not `write_to_db()`)
   - Verify queue receives item

2. **Test connection timeout:**
   - Mock slow/unreachable DB
   - Verify connection fails within 5 seconds
   - Verify appropriate error logged

3. **Test statement timeout:**
   - Mock slow query
   - Verify query fails within 5 seconds

### Integration Tests (manual)

1. **Verify service stability:**
   ```bash
   # Restart and monitor for 10 minutes
   sudo systemctl restart can-processor
   journalctl -u can-processor -f
   # Should see no watchdog timeouts
   ```

2. **Verify DB writes working:**
   ```bash
   # Check measurement count increases
   psql -U cea_user -d cea_sensors -c "SELECT COUNT(*) FROM measurement WHERE time > NOW() - INTERVAL '5 minutes';"
   # Wait 1 minute
   psql -U cea_user -d cea_sensors -c "SELECT COUNT(*) FROM measurement WHERE time > NOW() - INTERVAL '5 minutes';"
   # Count should increase
   ```

3. **Verify Redis writes working:**
   ```bash
   redis-cli GET sensor:dry_bulb_b
   # Should return current temperature value
   ```

4. **Stress test (optional):**
   ```bash
   # Simulate DB slowdown by running vacuum
   psql -U cea_user -d cea_sensors -c "VACUUM ANALYZE measurement;"
   # Service should NOT timeout during vacuum
   ```

---

## Success Criteria

- [ ] No watchdog timeouts for 24 hours
- [ ] DB writes continue working (verify via count query)
- [ ] Redis state updates working (verify via GET)
- [ ] `lsp_diagnostics` clean on all modified files
- [ ] All bare except clauses replaced with logged exceptions
- [ ] Stats logging visible in journalctl

---

## Rollback Plan

If issues occur after deployment:

```bash
# Immediate rollback
cd /home/antoine/ProjectCEA
git checkout Infrastructure/can-processor-service/app/writer.py
git checkout Infrastructure/can-processor-service/app/can_reader.py
git checkout Infrastructure/can-processor-service/app/main.py
sudo systemctl restart can-processor
```

Or use system rollback:
```bash
./rollback.sh
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `Infrastructure/can-processor-service/app/writer.py` | Use async queue, add timeouts, fix bare excepts, add queue warning |
| `Infrastructure/can-processor-service/app/can_reader.py` | Fix bare except in close() |
| `Infrastructure/can-processor-service/app/main.py` | Add periodic stats logging |

---

## Dependencies

- None - all changes are internal to can-processor-service
- No new packages required
- No database schema changes

---

## Post-Implementation

1. Monitor service for 24 hours
2. Check Grafana dashboards for data gaps
3. If stable, consider adding Prometheus metrics endpoint (future enhancement)
4. Update AGENTS.md to note the fix

---

## Related Issues

- SSH spam from `iskra` (100.100.196.43) - separate issue, not addressed in this plan
- Firefox slowness - was caused by PackageKit running kernel update (transient)
