# Fix Logging Module Shadowing Issue

## TL;DR

> **Quick Summary**: Rename `Infrastructure/shared/logging.py` to `infra_logging.py` to avoid shadowing Python's standard library logging module. Update all imports throughout the codebase.
> 
> **Deliverables**:
> - Rename shared/logging.py to infra_logging.py
> - Update shared/__init__.py exports
> - Update all imports across services
> - Verify fix works in all environments
> 
> **Estimated Effort**: Short
> **Parallel Execution**: NO - sequential (file renames must happen first)
> **Critical Path**: Rename → Update imports → Verify

---

## Context

### Problem

The file `Infrastructure/shared/logging.py` shadows Python's built-in `logging` module:

```
Infrastructure/shared/logging.py  ← LOCAL MODULE (your code)
         ↓
   "import logging" ← Python loads YOUR file instead of stdlib!
```

### Impact

| Environment     | Result                              |
| -------------- | ----------------------------------- |
| Systemd        | Works (no explicit PYTHONPATH)     |
| Terminal       | Fails (shadows stdlib)              |
| pytest         | Fails (shadows stdlib)              |

### Root Cause

- Project has local module named `logging` in `shared/`
- When PYTHONPATH includes current directory, Python finds local `logging.py` before stdlib
- Systemd works because it doesn't set PYTHONPATH explicitly

---

## Work Objectives

### Core Objective

Eliminate naming conflict between project logging module and Python stdlib logging.

### Concrete Deliverables

- [ ] Rename `shared/logging.py` to `shared/infra_logging.py`
- [ ] Update `shared/__init__.py` to export from new module
- [ ] Find and update all imports of `shared.logging`
- [ ] Verify fix works in terminal
- [ ] Verify fix works with pytest

### Must Have

- No broken imports after rename
- All services continue to log correctly
- Terminal/pytest can import modules without shadowing

### Must NOT Have

- No remaining references to `shared.logging` (use `shared.infra_logging`)
- No breaking changes to logging functionality

---

## Verification Strategy

### Test Commands

```bash
# Test 1: Verify stdlib logging is not shadowed
python3 -c "import logging; print(logging.__file__)"
# Expected: /usr/lib/python3.11/logging/__init__.py

# Test 2: Verify shared.infra_logging works
cd Infrastructure/automation-service
PYTHONPATH=../shared:. python3 -c "from shared.infra_logging import setup_structured_logging; print('OK')"

# Test 3: Run existing tests
cd Infrastructure/automation-service
PYTHONPATH=../shared:. pytest tests/test_event_bus.py -v
```

---

## Execution

### Step 1: Rename the file

```bash
# Rename logging.py to infra_logging.py
mv Infrastructure/shared/logging.py Infrastructure/shared/infra_logging.py
```

### Step 2: Update shared/__init__.py

Change:
```python
from shared.logging import (
    JsonFormatter,
    ConsoleFormatter,
    LoggingContext,
    StructuredLogger,
    get_logger,
    setup_structured_logging,
)
```

To:
```python
from shared.infra_logging import (
    JsonFormatter,
    ConsoleFormatter,
    LoggingContext,
    StructuredLogger,
    get_logger,
    setup_structured_logging,
)
```

### Step 3: Find all imports

```bash
# Find all files importing shared.logging
grep -r "from shared.logging" --include="*.py" Infrastructure/
grep -r "import shared.logging" --include="*.py" Infrastructure/
```

### Step 4: Update all imports

For each file found, change:
- `from shared.logging import ...` → `from shared.infra_logging import ...`
- `import shared.logging` → `import shared.infra_logging`

### Step 5: Verify

Run verification commands above.

---

## Files to Update

| File | Change |
|------|--------|
| `Infrastructure/shared/logging.py` | Rename to `infra_logging.py` |
| `Infrastructure/shared/__init__.py` | Update import source |
| `Infrastructure/backend/app/main.py` | Update import |
| `Infrastructure/automation-service/app/main.py` | Update import |
| (any other files found in Step 3) | Update imports |

---

## Commit Strategy

| Step | Message |
|------|---------|
| Rename | `refactor: rename shared/logging.py to infra_logging.py` |
| Updates | `refactor: update imports to use shared.infra_logging` |

---

## Success Criteria

- [ ] `python3 -c "import logging; print(logging.__file__)"` shows stdlib path
- [ ] All services start without import errors
- [ ] pytest runs without shadowing errors
- [ ] Logging output remains JSON formatted
