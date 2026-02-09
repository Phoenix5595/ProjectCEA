# Plan: database.py Refactoring

**Created:** 2026-01-19
**Status:** Ready to Execute
**Estimated Effort:** 5-6 hours (3 focused days)
**Goal:** Reduce `database.py` from 2,236 lines to ~300 lines

---

## Summary

Refactor the `database.py` god object by:
1. Fixing duplicate code (bug)
2. Moving schema management to Alembic migrations
3. Moving batching logic to SetpointRepository
4. Slimming DatabaseManager to a pure delegation facade

## Decisions Made

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Facade vs Direct Access | **Keep facade** | No caller changes needed, simpler API |
| Schema Management | **Alembic** | Future-proof, version-controlled, zero runtime overhead |
| Batching Logic | **Move to SetpointRepository** | Cohesive with setpoint operations |
| Deployment | **One big PR** | Complete refactor, test thoroughly before merge |
| Tests | **Manual smoke test** | No existing test suite |

---

## Phase 1: Fix Bugs & Deduplicate

**Goal:** Remove duplicated code that shouldn't exist
**Lines Removed:** ~640
**Risk:** Low

### Tasks

- [ ] **1.1** Identify duplicate `_create_tables()` method (appears twice: lines ~331-811 and ~859-1370)
- [ ] **1.2** Remove the duplicate `_create_tables()` - keep the first, delete the second
- [ ] **1.3** Remove duplicate `update_light_schedule_target()` from database.py (already in ScheduleRepository)
- [ ] **1.4** Remove duplicate `update_light_schedule_times()` from database.py (already in ScheduleRepository)
- [ ] **1.5** Run LSP diagnostics to verify no broken references
- [ ] **1.6** Test service starts correctly

### Verification
```bash
sudo rsync -av /home/antoine/ProjectCEA/Infrastructure/automation-service/app/ \
  /opt/projectcea/current/Infrastructure/automation-service/app/
sudo systemctl restart automation-service
journalctl -u automation-service -f  # Watch for errors
```

---

## Phase 2: Alembic Migration Setup

**Goal:** Move all schema creation to versioned migrations
**Lines Removed:** ~800
**Risk:** Medium (schema changes require care)

### Tasks

- [ ] **2.1** Install alembic
  ```bash
  cd /home/antoine/ProjectCEA/Infrastructure/automation-service
  pip install alembic
  echo "alembic" >> requirements.txt
  ```

- [ ] **2.2** Initialize alembic structure
  ```bash
  cd /home/antoine/ProjectCEA/Infrastructure/automation-service
  alembic init alembic
  ```

- [ ] **2.3** Configure `alembic.ini`
  - Set `sqlalchemy.url` to use environment variable
  - Configure script location

- [ ] **2.4** Configure `alembic/env.py`
  - Set up async support for asyncpg
  - Configure connection string from environment

- [ ] **2.5** Create initial migration `001_baseline.py`
  - Extract ALL `CREATE TABLE` statements from `_create_tables()`
  - Extract ALL `CREATE INDEX` statements
  - Extract hypertable creation for TimescaleDB
  - This is a baseline - assumes current schema is correct

- [ ] **2.6** Create `alembic_version` table manually in production DB
  ```sql
  -- Mark current production as already at baseline
  CREATE TABLE IF NOT EXISTS alembic_version (
    version_num VARCHAR(32) NOT NULL PRIMARY KEY
  );
  INSERT INTO alembic_version (version_num) VALUES ('001_baseline');
  ```

- [ ] **2.7** Remove `_create_tables()` from database.py

- [ ] **2.8** Remove `_create_room_modes_tables()` from database.py

- [ ] **2.9** Remove `_migrate_tables()` from database.py (superseded by Alembic)

- [ ] **2.10** Update `initialize()` method to run Alembic
  ```python
  async def initialize(self):
      # Create connection pool
      self._pool = await self._get_pool()
      # Run migrations (sync, at startup only)
      from alembic.config import Config
      from alembic import command
      alembic_cfg = Config("alembic.ini")
      command.upgrade(alembic_cfg, "head")
      # Initialize repositories
      ...
  ```

- [ ] **2.11** Test on local/staging before production

### Verification
```bash
# Test Alembic works
cd /home/antoine/ProjectCEA/Infrastructure/automation-service
alembic current  # Should show 001_baseline
alembic upgrade head  # Should say "Already at head"

# Full service restart
sudo systemctl restart automation-service
```

### File Structure After Phase 2
```
Infrastructure/automation-service/
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_baseline.py
├── alembic.ini
├── app/
│   ├── database.py  (now ~1,400 lines)
│   └── ...
└── requirements.txt
```

---

## Phase 3: Move Batching to SetpointRepository

**Goal:** Move effective setpoint batching logic to repository
**Lines Removed:** ~130
**Risk:** Medium (batching affects data integrity)

### Tasks

- [ ] **3.1** Add batch buffer attributes to `SetpointRepository.__init__()`
  ```python
  def __init__(self, pool, redis_client):
      super().__init__(pool, redis_client)
      self._batch_buffer: List[Dict] = []
      self._last_batch_flush: float = time.time()
      self._batch_lock = asyncio.Lock()
      self._batch_interval: float = 10.0  # seconds
  ```

- [ ] **3.2** Move `flush_batch_buffer()` method to SetpointRepository

- [ ] **3.3** Update `log_effective_setpoints()` in SetpointRepository
  - Add batching logic (currently in database.py lines ~1552-1680)
  - Keep same 10-second batch interval
  - Add logging for debugging

- [ ] **3.4** Remove batch buffer attributes from DatabaseManager

- [ ] **3.5** Remove `flush_batch_buffer()` from DatabaseManager

- [ ] **3.6** Simplify DatabaseManager wrapper to just delegate
  ```python
  async def log_effective_setpoints(self, setpoints: List[Dict]) -> None:
      await self._setpoint_repo.log_effective_setpoints(setpoints)
  
  async def flush_batch_buffer(self) -> None:
      await self._setpoint_repo.flush_batch_buffer()
  ```

- [ ] **3.7** Run LSP diagnostics

- [ ] **3.8** Test effective setpoints are still being logged
  ```sql
  SELECT * FROM effective_setpoints ORDER BY logged_at DESC LIMIT 10;
  ```

### Verification
```bash
# Check setpoints are being batched correctly
sudo -u postgres psql cea_sensors -c \
  "SELECT COUNT(*), date_trunc('minute', logged_at) as minute 
   FROM effective_setpoints 
   WHERE logged_at > NOW() - INTERVAL '5 minutes' 
   GROUP BY minute ORDER BY minute;"
```

---

## Phase 4: Slim the Facade

**Goal:** Reduce DatabaseManager to pure delegation
**Lines Removed:** ~230
**Risk:** Low (just removing duplication)

### Tasks

- [ ] **4.1** Audit all wrapper methods - ensure they're pure delegation
  ```python
  # Good - pure delegation
  async def get_pid_parameters(self, device_type: str) -> Optional[Dict]:
      return await self._pid_repo.get_pid_parameters(device_type)
  
  # Bad - has logic that should be in repository
  async def get_pid_parameters(self, device_type: str) -> Optional[Dict]:
      result = await self._pid_repo.get_pid_parameters(device_type)
      if result:
          result['computed_field'] = ...  # This belongs in repo
      return result
  ```

- [ ] **4.2** Move any remaining logic to appropriate repositories

- [ ] **4.3** Move `load_schedule_state_to_redis()` to ScheduleRepository
  - This loads schedules into Redis at startup
  - Belongs with other schedule operations

- [ ] **4.4** Remove duplicate caching logic if repositories handle it

- [ ] **4.5** Consolidate connection management
  - Keep `_get_pool()`, `close()`
  - Remove any redundant connection helpers

- [ ] **4.6** Clean up imports - remove unused

- [ ] **4.7** Run LSP diagnostics on all changed files

### Verification
```bash
# Count lines
wc -l /home/antoine/ProjectCEA/Infrastructure/automation-service/app/database.py
# Should be ~300-400 lines
```

---

## Phase 5: Final Verification

**Goal:** Ensure everything works before PR
**Risk:** N/A (verification only)

### Tasks

- [ ] **5.1** Run LSP diagnostics on all files
  ```
  database.py
  repositories/*.py
  routes/*.py
  control/*.py
  ```

- [ ] **5.2** Full service restart test
  ```bash
  sudo systemctl restart postgresql redis-server
  sudo systemctl restart can-processor cea-backend automation-service
  ```

- [ ] **5.3** Verify control loop is running
  ```bash
  journalctl -u automation-service -f | grep -i "control\|tick\|loop"
  ```

- [ ] **5.4** Verify API endpoints
  ```bash
  curl http://localhost:8001/api/health
  curl http://localhost:8001/api/setpoints/Flower%20Room/main
  curl http://localhost:8001/api/pid/parameters/humidifier
  curl http://localhost:8001/api/schedules/Flower%20Room
  ```

- [ ] **5.5** Verify Grafana dashboards load
  - Open http://[pi-ip]:3000
  - Check Veg Sector dashboard
  - Check Flower Sector dashboard

- [ ] **5.6** Check effective setpoints are being logged
  ```bash
  sudo -u postgres psql cea_sensors -c \
    "SELECT * FROM effective_setpoints ORDER BY logged_at DESC LIMIT 5;"
  ```

- [ ] **5.7** Monitor for 10 minutes, check for errors
  ```bash
  journalctl -u automation-service -u cea-backend -f
  ```

- [ ] **5.8** If all good, create git commit
  ```bash
  cd /home/antoine/ProjectCEA
  git add -A
  git commit -m "refactor: slim database.py from 2236 to ~300 lines

  - Remove duplicate _create_tables() method
  - Add Alembic for schema migrations  
  - Move batching logic to SetpointRepository
  - Reduce DatabaseManager to pure delegation facade
  - Move load_schedule_state_to_redis to ScheduleRepository
  
  No functional changes - same behavior, cleaner code."
  ```

- [ ] **5.9** Deploy to production
  ```bash
  sudo ./deploy.sh
  ```

- [ ] **5.10** Monitor production for 30 minutes

---

## Rollback Plan

If anything breaks:

```bash
# Immediate rollback (< 30 seconds)
cd /home/antoine/ProjectCEA
sudo ./rollback.sh

# If Alembic schema is wrong
sudo -u postgres psql cea_sensors -c "DROP TABLE alembic_version;"
# Then rollback code
```

---

## Final File Structure

```
Infrastructure/automation-service/
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 001_baseline.py (~800 lines)
├── alembic.ini
├── app/
│   ├── database.py (~300 lines)
│   │   └── DatabaseManager
│   │       ├── __init__()
│   │       ├── initialize()
│   │       ├── close()
│   │       └── ~50 thin wrapper methods
│   └── repositories/
│       ├── __init__.py
│       ├── base.py (~50 lines)
│       ├── pid.py (~280 lines)
│       ├── setpoints.py (~400 lines) ← gained batching
│       ├── schedules.py (~400 lines) ← gained load_to_redis
│       ├── devices.py (~160 lines)
│       ├── room_modes.py (~330 lines)
│       ├── sensors.py (~150 lines)
│       └── control_actions.py (~100 lines)
└── requirements.txt
```

---

## Line Count Summary

| Phase | Lines Removed | Cumulative |
|-------|---------------|------------|
| Phase 1: Deduplicate | ~640 | 1,596 remaining |
| Phase 2: Alembic | ~800 | 796 remaining |
| Phase 3: Batching | ~130 | 666 remaining |
| Phase 4: Slim facade | ~230 | 436 remaining |
| **Final cleanup** | ~100 | **~300-350 lines** |

---

## Success Criteria

- [ ] `database.py` is under 400 lines
- [ ] All services start without errors
- [ ] Control loop runs every 2 seconds
- [ ] API endpoints respond correctly
- [ ] Grafana dashboards load data
- [ ] Effective setpoints are batched and logged
- [ ] Alembic migrations are version controlled
- [ ] Zero functional changes from user perspective
