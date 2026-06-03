# Task 4: Replace Relative Imports with Absolute Imports

**Date:** 2026-06-02
**Scope:** `Infrastructure/automation-service/app/` (routes/, services/, control/)

## Summary

Replaced all non-TYPE_CHECKING relative imports (`from ..X`) with absolute imports (`from app.X`) across the automation-service.

## Files Changed

| File | Change | Count |
|------|--------|-------|
| `app/routes/debug.py` | `from ..` → `from app.` (4 top-level + 4 lazy imports) | 8 |
| `app/routes/timing.py` | `from ..control.timing` → `from app.control.timing` (2) | 2 |
| `app/routes/room_modes.py` | `from ..` → `from app.` (3 top-level + 4 lazy imports) | 7 |
| `app/services/mode_transition_service.py` | `from ..` → `from app.` (2 top-level + 3 lazy imports) | 5 |

## Intentionally Skipped

- `app/control/device_controller.py` lines 13-14: inside `TYPE_CHECKING` block (per rules)
- All `__init__.py` files (per rules)
- `from .websocket import` in `room_modes.py`: single-dot same-package import, not in scope
- `from shared.X` imports: already absolute (shared package, not `app` package)

## Verification

- **`grep -rn "from \.\."`**: Only `device_controller.py` TYPE_CHECKING block remains
- **`ruff check --fix`**: 3 errors fixed, 0 remaining
- **`python -c "from app.main import app"`**: All imports resolve (verified with correct PYTHONPATH including `Infrastructure/`)
