
## Task 4: Parallel I2C - Deferred

**Date:** 2026-02-07

**Finding:** Full parallel I2C execution requires significant refactoring of device_processor.py.
The current architecture processes devices sequentially with dependencies (PID calculations, context building).

**Current Impact:** With DFR0971 retry optimization (Task 3), worst case is ~400ms - well under 1s target.

**Future Enhancement:** Refactor to collect all hardware operations first, then batch execute:
- All relay ops (MCP23017, bus 0) in one asyncio.gather()
- All dimmer ops (DFR0971, bus 1) in parallel asyncio.gather()
- Respecting sequencing constraints (power before signal, signal before power off)

**Decision:** Defer full implementation. Current optimizations sufficient for 1-second target.
