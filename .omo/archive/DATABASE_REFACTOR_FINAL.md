# Database Refactoring - Final Plan

**Goal**: Zero LSP errors, no god objects, clean architecture
**Current State**: 2127 lines, 50 methods, 70+ LSP errors
**Target State**: ~500 lines (facade only), 0 LSP errors

---

## Phase 1: Fix Critical LSP Errors (Signature Mismatches)

### 1.1 Fix `time` module collision (lines 1807-1831)
- **Issue**: `time` imported as module but used as type annotation
- **Fix**: Use `datetime.time` or rename import to `from datetime import time as dt_time`

### 1.2 Fix repository delegation signatures
| Line | Method | Issue | Fix |
|------|--------|-------|-----|
| 1624 | `get_pid_parameters` | Expected 7 args | Match repo signature |
| 1681 | `set_pid_parameters` | Expected 6 args | Match repo signature |
| 1690 | `get_pid_parameter_history` | Expected 2 args | Match repo signature |
| 1696 | `get_all_pid_parameters` | Wrong return type | Fix return type |
| 1702 | `get_pid_control_mode` | Wrong return type | Fix return type |
| 1713 | `set_pid_control_mode` | Wrong param type | Fix param type |
| 1736 | `get_autotune_state` | Expected 1 arg | Match repo signature |
| 1758 | `set_pid_parameters_with_reason` | Expected 7 args | Match repo signature |
| 1819 | `create_schedule` | Wrong return type | Fix return type |
| 1842 | `delete_schedules_bulk` | Expected 1 arg | Match repo signature |

### 1.3 Fix type annotation errors
| Line | Issue | Fix |
|------|-------|-----|
| 156 | `list[Dict]` vs `list[tuple]` | Fix batch buffer type |
| 178-188 | Tuple indexing with string keys | Fix to use dict properly |
| 1362 | `int` vs `bool` for state | Use `int` in signature |
| 1458-1459 | `str` vs `float` | Fix param types |
| 2074 | Missing type args for `dict` | Add `[str, Any]` |

---

## Phase 2: Break Import Cycles

### 2.1 Current cycle
```
database.py → routes/schedules.py → main.py → routes/__init__.py → routes/routes.py → routes/*.py
```

### 2.2 Solution: Dependency Injection
1. Create `app/dependencies.py` with database singleton
2. Routes import from `dependencies.py`, not `database.py`
3. `database.py` imports nothing from routes

```python
# app/dependencies.py
from functools import lru_cache
from .database import DatabaseManager

@lru_cache
def get_database() -> DatabaseManager:
    return DatabaseManager()
```

---

## Phase 3: Move Remaining Inline Code to Repositories

### 3.1 Methods with inline code remaining (move to repos)

| Method | Lines | Target Repository |
|--------|-------|-------------------|
| `log_effective_setpoints` | 1463-1592 (~130 lines) | SetpointRepository |
| `load_schedule_state_to_redis` | 1859-1907 (~50 lines) | ScheduleRepository |
| `_create_room_modes_tables` | 1920-2061 (~140 lines) | RoomModeRepository |
| `update_light_schedule_target` | 2098-2112 | ScheduleRepository |
| `update_light_schedule_times` | 2113-2127 | ScheduleRepository |

### 3.2 Infrastructure methods (keep in database.py)
- `initialize()` - orchestrates repo initialization
- `_get_pool()` - connection pool management
- `_connect_db()` - database connection
- `_connect_redis()` - Redis connection
- `close()` - cleanup
- `flush_batch_buffer()` - batching (consider moving to separate BatchManager)

---

## Phase 4: Extract Batch Buffer to Separate Class

### 4.1 Create `app/batch_manager.py`
Move lines 143-200 (batch buffer logic) to dedicated class:
```python
class BatchManager:
    def __init__(self, pool: asyncpg.Pool):
        self._batch_buffer: list[dict[str, Any]] = []
        self._batch_lock = asyncio.Lock()
    
    async def add_to_batch(self, record: dict) -> None: ...
    async def flush(self) -> int: ...
```

---

## Phase 5: Final Cleanup

### 5.1 Remove duplicate `_create_tables` method
- Line 305 and 819 both define `_create_tables`
- Keep one, remove duplicate

### 5.2 Simplify DatabaseManager to pure facade
Final structure (~400-500 lines):
```python
class DatabaseManager:
    # Infrastructure (keep)
    __init__, initialize, close, _get_pool, _connect_db, _connect_redis
    
    # Delegations only (1-3 lines each)
    # All 50 methods become simple:
    async def method(...):
        return await self._repo.method(...)
```

---

## Phase 6: LSP Verification & Tests

### 6.1 Run LSP diagnostics on all files
```bash
# Target: 0 errors
lsp_diagnostics database.py
lsp_diagnostics repositories/*.py
```

### 6.2 Verify production
```bash
./deploy.sh
curl http://127.0.0.1:8001/health
journalctl -u automation-service -n 20
```

---

## Execution Order

1. **Phase 1.1**: Fix `time` module collision (5 min)
2. **Phase 1.2**: Fix all signature mismatches (30 min)
3. **Phase 1.3**: Fix type annotation errors (15 min)
4. **Phase 3**: Move remaining inline code (45 min)
5. **Phase 4**: Extract BatchManager (20 min)
6. **Phase 5**: Remove duplicates, final cleanup (10 min)
7. **Phase 2**: Break import cycles (30 min) - last because most invasive
8. **Phase 6**: Verify everything (10 min)

**Total estimated time**: ~2.5 hours

---

## Success Criteria

- [ ] `database.py` < 600 lines
- [ ] 0 LSP errors in database.py
- [ ] 0 LSP errors in all repositories
- [ ] No import cycles
- [ ] All tests pass
- [ ] Production deployed and healthy
- [ ] Control loop running correctly

---

## Files to Modify

| File | Changes |
|------|---------|
| `database.py` | Reduce to facade, fix types |
| `repositories/setpoints.py` | Add `log_effective_setpoints` |
| `repositories/schedules.py` | Add `load_schedule_state_to_redis`, `update_*` |
| `repositories/room_modes.py` | Add `_create_tables` |
| `app/batch_manager.py` | NEW - extract batch logic |
| `app/dependencies.py` | NEW - break import cycles |
| `routes/*.py` | Import from dependencies |
