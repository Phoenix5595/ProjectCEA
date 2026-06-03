# Refactor: Extract DatabaseManager Methods to Repositories

**Based on Audit**: `.sisyphus/drafts/code-audit-results.md`

---

## TL;DR

> **Quick Summary**: DatabaseManager is a god object with 1200+ lines and 6+ responsibilities. Extract ~40 direct DB methods into their respective repository classes.
> 
> **Deliverables**:
> - ScheduleRepository with schedule CRUD methods
> - PIDRepository with PID parameter methods
> - RoomModeRepository with room mode methods
> - Slimmed DatabaseManager (~400 lines, facade only)
> 
> **Estimated Effort**: Medium (2-3 hours)
> **Risk Level**: MEDIUM (touches core DB layer)

---

## Context

### Audit Findings

DatabaseManager currently violates Single Responsibility Principle:

| Responsibility | Should Be |
|----------------|-----------|
| DB connection pool management | ✅ Keep in DatabaseManager |
| Redis connection management | ✅ Keep in DatabaseManager |
| Query caching | ✅ Keep in DatabaseManager |
| Batch buffering | ✅ Keep in DatabaseManager |
| Repository coordination | ✅ Keep in DatabaseManager |
| **Direct schedule queries** | ❌ Move to ScheduleRepository |
| **Direct PID queries** | ❌ Move to PIDRepository |
| **Direct room mode queries** | ❌ Move to RoomModeRepository |

### Existing Repository Pattern

The codebase already uses repositories correctly for some domains:
- `repositories/control_actions.py`
- `repositories/alarms.py`
- `repositories/sensor_metadata.py`

This refactoring extends the same pattern to schedules, PIDs, and room modes.

---

## Work Objectives

### Core Objective
Extract direct DB methods from DatabaseManager into domain-specific repositories, following existing repository pattern.

### Concrete Deliverables
- [ ] `app/repositories/schedules.py` - ScheduleRepository
- [ ] `app/repositories/pid.py` - PIDRepository  
- [ ] `app/repositories/room_modes.py` - RoomModeRepository
- [ ] Updated `app/database.py` - facade methods delegate to repos

### Definition of Done
- [ ] DatabaseManager < 500 lines
- [ ] All existing tests pass
- [ ] No new LSP errors introduced
- [ ] API behavior unchanged

### Must Have
- Maintain exact same method signatures on DatabaseManager (facade pattern)
- Use existing asyncpg pool from DatabaseManager
- Follow existing repository patterns in codebase

### Must NOT Have (Guardrails)
- NO breaking API changes (callers must not need changes)
- NO changes to Redis caching behavior
- NO changes to batch buffering behavior
- NO new dependencies

---

## Test Strategy

### Existing Test Coverage
- Check `tests/` directory for existing DatabaseManager tests
- Run `pytest` to establish baseline

### Verification Commands
```bash
cd Infrastructure/automation-service
pytest -v  # All tests must pass
ruff check app/  # No lint errors
```

---

## TODOs

### Task 1: Create ScheduleRepository

**What to do**:
1. Create `app/repositories/schedules.py`
2. Move these methods from DatabaseManager:
   - `get_schedules()`
   - `get_room_schedule()`
   - `create_schedule()`
   - `update_schedule()`
   - `delete_schedule()`
   - `delete_schedules_bulk()`
3. Repository takes `pool` in constructor
4. Add facade methods in DatabaseManager that delegate to repo

**References**:
- `app/repositories/control_actions.py` - existing repository pattern
- `app/database.py:get_schedules` - method to extract

**Acceptance Criteria**:
- [ ] ScheduleRepository exists with all schedule methods
- [ ] DatabaseManager.get_schedules() delegates to repo
- [ ] `pytest tests/` passes
- [ ] `ruff check` clean

**Recommended Agent**: `category="quick"`, `load_skills=[]`
**Parallelization**: Can run in parallel with Task 2, 3

---

### Task 2: Create PIDRepository

**What to do**:
1. Create `app/repositories/pid.py`
2. Move these methods from DatabaseManager:
   - `get_pid_parameters()`
   - `set_pid_parameters()`
   - `get_autotune_state()`
   - `set_autotune_state()`
3. Add facade methods in DatabaseManager

**References**:
- `app/repositories/control_actions.py` - existing pattern
- `app/database.py` - methods to extract

**Acceptance Criteria**:
- [ ] PIDRepository exists with all PID methods
- [ ] DatabaseManager delegates to repo
- [ ] Tests pass, ruff clean

**Recommended Agent**: `category="quick"`, `load_skills=[]`
**Parallelization**: Can run in parallel with Task 1, 3

---

### Task 3: Create RoomModeRepository

**What to do**:
1. Create `app/repositories/room_modes.py`
2. Move these methods from DatabaseManager:
   - `get_room_modes()`
   - `set_room_modes()`
   - `get_active_mode()`
   - `get_all_modes()`
3. Add facade methods in DatabaseManager

**References**:
- `app/repositories/control_actions.py` - existing pattern
- `app/database.py` - methods to extract

**Acceptance Criteria**:
- [ ] RoomModeRepository exists
- [ ] DatabaseManager delegates to repo
- [ ] Tests pass, ruff clean

**Recommended Agent**: `category="quick"`, `load_skills=[]`
**Parallelization**: Can run in parallel with Task 1, 2

---

### Task 4: Wire Repositories in Container

**What to do**:
1. Update `app/container.py` to create repository instances
2. Inject repositories into DatabaseManager constructor
3. Update DatabaseManager to use injected repos

**Depends On**: Tasks 1, 2, 3

**References**:
- `app/container.py` - dependency injection setup
- `app/database.py` - DatabaseManager constructor

**Acceptance Criteria**:
- [ ] Container creates all new repositories
- [ ] DatabaseManager receives repos via constructor
- [ ] Full test suite passes

**Recommended Agent**: `category="quick"`, `load_skills=[]`
**Parallelization**: Must run AFTER Tasks 1-3

---

### Task 5: Final Verification

**What to do**:
1. Run full test suite
2. Check DatabaseManager line count (target: <500)
3. Verify no regression in API behavior
4. Run linting

**Depends On**: Task 4

**Verification Commands**:
```bash
pytest -v
ruff check app/
wc -l app/database.py  # Should be < 500
```

**Acceptance Criteria**:
- [ ] All tests pass
- [ ] DatabaseManager < 500 lines
- [ ] No lint errors
- [ ] No LSP errors in new files

---

## Execution Strategy

```
Wave 1 (Parallel):
├── Task 1: Create ScheduleRepository
├── Task 2: Create PIDRepository
└── Task 3: Create RoomModeRepository

Wave 2 (After Wave 1):
└── Task 4: Wire Repositories in Container

Wave 3 (After Wave 2):
└── Task 5: Final Verification
```

---

## Commit Strategy

| After Task | Message | Files |
|------------|---------|-------|
| 1 | `refactor(db): extract ScheduleRepository` | repositories/schedules.py, database.py |
| 2 | `refactor(db): extract PIDRepository` | repositories/pid.py, database.py |
| 3 | `refactor(db): extract RoomModeRepository` | repositories/room_modes.py, database.py |
| 4 | `refactor(db): wire repositories in container` | container.py, database.py |
| 5 | `refactor(db): complete DatabaseManager extraction` | (none, verification only) |

---

## Rollback Strategy

If any task fails:
1. `git stash` uncommitted changes
2. `git reset --hard HEAD~N` to last good commit
3. Investigate and retry

---

## Success Criteria

- [ ] DatabaseManager reduced from 1200+ to <500 lines
- [ ] 3 new repository classes created
- [ ] All existing tests pass
- [ ] No breaking changes to API
- [ ] Code follows existing repository pattern
