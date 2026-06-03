# Parallel I2C Unified Implementation Plan

## TL;DR

> **Quick Summary**: Parallelize I2C hardware operations across devices to guarantee <1 second control loop execution. Uses per-device operation chains that run in parallel while preserving internal sequencing constraints (relay ON→dimmer, dimmer→relay OFF).
> 
> **Deliverables**:
> - `app/control/hardware_batch.py` - HardwareBatchExecutor class (NEW)
> - `app/control/device_controller.py` - Operation queuing mode (MODIFIED)
> - `app/control/device_processor.py` - Batch execution integration (MODIFIED)
> - `tests/test_parallel_i2c.py` - Comprehensive test coverage (NEW)
> 
> **Estimated Effort**: Medium (13 hours implementation + testing)
> **Parallel Execution**: NO - sequential tasks with dependencies
> **Critical Path**: Task 1 → Task 2 → Task 3 → Task 4 → Task 5

---

## Context

### Original Request
Optimize the automation-service control loop to guarantee 1-second max execution by parallelizing I2C operations across different buses (MCP23017 relays on bus 0, DFR0971 dimmers on bus 1).

### Current Architecture

**Sequential Processing Flow:**
```
DeviceProcessor.process_devices()
  └─> for device_name, device_info in cluster_devices.items() [Line 79]
        └─> DeviceController.process_device()
              └─> DeviceController._apply_control_output()
                    ├─> Relay operations (MCP23017, I2C bus 0): 5-20ms each
                    └─> Dimming operations (DFR0971, I2C bus 1): 50-290ms each
```

**Performance Bottlenecks:**
| Component | Latency | I2C Bus | Notes |
|-----------|---------|---------|-------|
| MCP23017 relays | 5-20ms/op | Bus 0 | Fast, not bottleneck |
| DFR0971 dimmers | 50-290ms/op | Bus 1 | **Primary bottleneck** |
| Sequential total | Sum of all ops | - | ~570ms worst case |

**Typical Scenario:**
- 4 devices × (10ms relay + 150ms dimmer) = ~640ms total (sequential)
- With parallel: max(40ms relays, 600ms dimmers) = ~600ms total
- **Improvement: 6-40% depending on device mix**

### Critical Sequencing Constraints

| Constraint | Rule | Reason |
|------------|------|--------|
| Power before signal | Relay ON → Dimmer set | Power must be on before sending voltage |
| Signal before power off | Dimmer 0 → Relay OFF | Must zero signal before cutting power |
| Per-device atomic | Operations stay grouped | Device state consistency |

### Research Findings
- `device_processor.py` line 79: sequential `for device in devices` loop
- `device_controller.py` lines 388-518: `_control_dimmable_light()` has sequencing logic
- `relay_manager.py`: has interlock checks that MUST be preserved
- `dfr0971.py`: has retry logic with feature-flagged timing
- Feature flag `PARALLEL_I2C` already exists in `feature_flags.py`

### Metis Review Findings

| Gap Identified | Resolution |
|---------------|------------|
| Sequencing model incomplete | Use per-device operation CHAINS, parallelize ACROSS devices |
| Missing baseline metrics | Timing instrumentation already in place from Phase 1 |
| Partial failure handling | Use `return_exceptions=True` in asyncio.gather() |
| Per-operation timeout | 500ms timeout per I2C operation chain |
| Edge cases | Empty batch, I2C bus failure, mid-batch config change |

---

## Proposed Architecture

### New Parallel Processing Design

```
DeviceProcessor.process_devices()
  └─> Check PARALLEL_I2C feature flag
        │
        ├─> If DISABLED: Use existing sequential processing
        │
        └─> If ENABLED:
              │
              ├─> Phase 1: Collect all device operations
              │     ├─> Group by device into operation chains
              │     └─> Each chain: [relay_op?, dimmer_op?, relay_op?]
              │
              └─> Phase 2: Execute chains in parallel
                    └─> asyncio.gather(*device_chains, return_exceptions=True)
                          ├─> Chain A: [relay_ON, dimmer_set] (sequential within)
                          ├─> Chain B: [dimmer_0, relay_OFF] (sequential within)
                          └─> Chain C: [dimmer_set] (single op)
```

### Data Flow Comparison

```
Current Sequential Flow:
Device1 → Device2 → Device3 → Device4
  ↓         ↓         ↓         ↓
Relay+Dimmer Relay+Dimmer Relay+Dimmer Relay+Dimmer
  ↓         ↓         ↓         ↓
[160ms]   [160ms]   [160ms]   [160ms] = 640ms total

Proposed Parallel Flow:
Device1 → Device2 → Device3 → Device4
  ↓         ↓         ↓         ↓
Collect Operations (Build Chains)
  ↓
┌─────────────────────────────────────────┐
│ Execute All Chains in Parallel          │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐    │
│ │Chain 1  │ │Chain 2  │ │Chain 3  │    │
│ │relay→dim│ │dim→relay│ │dim only │    │
│ │[160ms]  │ │[160ms]  │ │[150ms]  │    │
│ └─────────┘ └─────────┘ └─────────┘    │
└─────────────────────────────────────────┘
  ↓
[160ms total] = 75% faster
```

### Phase-Based Execution Model

```python
class HardwareBatchExecutor:
    def __init__(self):
        self._device_chains: Dict[str, DeviceOperationChain] = {}
    
    async def execute(self) -> BatchResult:
        if not get_flag("PARALLEL_I2C"):
            return await self._execute_sequential()
        
        # Execute all device chains in parallel
        # Each chain is internally sequential (respects constraints)
        tasks = [
            self._execute_chain(chain) 
            for chain in self._device_chains.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._aggregate_results(results)
```

---

## Work Objectives

### Core Objective
Reduce P95 control loop execution time to <500ms by parallelizing I2C hardware operations across devices while preserving safety-critical sequencing constraints.

### Concrete Deliverables

| File | Type | Purpose |
|------|------|---------|
| `app/control/hardware_batch.py` | NEW | HardwareBatchExecutor + dataclasses |
| `app/control/device_controller.py` | MODIFIED | Add batch_executor queuing |
| `app/control/device_processor.py` | MODIFIED | Integrate batch execution |
| `tests/test_parallel_i2c.py` | NEW | Comprehensive test suite |

### Definition of Done
- [ ] `pytest tests/` passes with PARALLEL_I2C=true
- [ ] `pytest tests/` passes with PARALLEL_I2C=false (sequential mode)
- [ ] P95 execution time < 500ms with flag enabled
- [ ] All existing device control tests pass unchanged
- [ ] Interlock checks verified working with parallel execution

### Must Have
- Feature flag `PARALLEL_I2C` controls behavior (already exists)
- Preserve relay ON → dimmer sequencing for light turn-on
- Preserve dimmer 0 → relay OFF sequencing for light turn-off
- Preserve interlock checks in `relay_manager.py`
- Preserve retry logic in `dfr0971.py`
- Graceful handling of partial failures
- Per-device-chain timeout (500ms)
- Backwards compatibility when batch_executor is None

### Must NOT Have (Guardrails)
- **DO NOT** modify PID logic, scheduling, or setpoint handling
- **DO NOT** bypass safety interlocks under any circumstances
- **DO NOT** change the external API of RelayManager or DFR0971Manager
- **DO NOT** introduce new dependencies (use stdlib asyncio only)
- **DO NOT** change sensor sampling or Redis operations
- **DO NOT** parallelize operations within the SAME device chain
- **DO NOT** create a "god object" - keep responsibilities separated

---

## Verification Strategy

> **UNIVERSAL RULE: ZERO HUMAN INTERVENTION**
> ALL verification is executed by the agent using tools.

### Test Decision
- **Infrastructure exists**: YES (pytest configured)
- **Automated tests**: YES (Tests-after, not TDD)
- **Framework**: pytest

### Verification Commands
```bash
# All tests pass with parallel enabled
PARALLEL_I2C=true pytest tests/test_parallel_i2c.py -v

# All tests pass with parallel disabled
PARALLEL_I2C=false pytest tests/test_parallel_i2c.py -v

# Timing API shows improvement
curl http://localhost:8001/api/timing/histogram | jq '.p95_ms'

# Feature flag toggle
curl -X POST http://localhost:8001/api/flags \
  -H "Content-Type: application/json" \
  -d '{"name":"PARALLEL_I2C","enabled":true}'
```

---

## Implementation Tasks

### Task 1: Create HardwareBatchExecutor

**File:** `app/control/hardware_batch.py` (NEW)

**What to do:**

```python
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Awaitable
import asyncio
from app.feature_flags import get_flag

@dataclass
class RelayOperation:
    location: str
    cluster: str
    device_name: str
    state: bool  # True=ON, False=OFF
    
@dataclass
class DimmerOperation:
    board_id: str
    channel: int
    intensity: int  # 0-100
    device_name: str

@dataclass
class DeviceOperationChain:
    """Ordered operations for a single device. Executed sequentially."""
    device_name: str
    operations: List[RelayOperation | DimmerOperation] = field(default_factory=list)

@dataclass
class BatchResult:
    success_count: int
    failure_count: int
    failures: Dict[str, str]  # device_name -> error message
    total_time_ms: float

class HardwareBatchExecutor:
    """Collects and executes hardware operations in parallel across devices."""
    
    def __init__(self):
        self._chains: Dict[str, DeviceOperationChain] = {}
    
    def queue_relay_on(self, location: str, cluster: str, device_name: str) -> None:
        """Queue relay ON operation (power before signal)."""
        chain = self._get_or_create_chain(device_name)
        chain.operations.insert(0, RelayOperation(location, cluster, device_name, True))
    
    def queue_relay_off(self, location: str, cluster: str, device_name: str) -> None:
        """Queue relay OFF operation (signal before power off - appends to end)."""
        chain = self._get_or_create_chain(device_name)
        chain.operations.append(RelayOperation(location, cluster, device_name, False))
    
    def queue_dimmer_set(self, board_id: str, channel: int, intensity: int, device_name: str) -> None:
        """Queue dimmer operation."""
        chain = self._get_or_create_chain(device_name)
        # Insert after relay_on but before relay_off
        insert_idx = self._find_dimmer_insert_position(chain)
        chain.operations.insert(insert_idx, DimmerOperation(board_id, channel, intensity, device_name))
    
    def queue_light_on(self, location: str, cluster: str, device_name: str,
                       board_id: str, channel: int, intensity: int) -> None:
        """Queue complete light ON sequence: relay_on -> dimmer_set."""
        self.queue_relay_on(location, cluster, device_name)
        self.queue_dimmer_set(board_id, channel, intensity, device_name)
    
    def queue_light_off(self, location: str, cluster: str, device_name: str,
                        board_id: str, channel: int) -> None:
        """Queue complete light OFF sequence: dimmer_0 -> relay_off."""
        self.queue_dimmer_set(board_id, channel, 0, device_name)
        self.queue_relay_off(location, cluster, device_name)
    
    async def execute(self, relay_manager, dfr0971_manager) -> BatchResult:
        """Execute all queued operations."""
        if not self._chains:
            return BatchResult(0, 0, {}, 0.0)
        
        if not get_flag("PARALLEL_I2C", default=False):
            return await self._execute_sequential(relay_manager, dfr0971_manager)
        
        return await self._execute_parallel(relay_manager, dfr0971_manager)
    
    async def _execute_parallel(self, relay_manager, dfr0971_manager) -> BatchResult:
        """Execute device chains in parallel using asyncio.gather."""
        import time
        start = time.perf_counter()
        
        tasks = [
            asyncio.wait_for(
                self._execute_chain(chain, relay_manager, dfr0971_manager),
                timeout=0.5  # 500ms per chain
            )
            for chain in self._chains.values()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        elapsed = (time.perf_counter() - start) * 1000
        return self._aggregate_results(results, elapsed)
    
    async def _execute_sequential(self, relay_manager, dfr0971_manager) -> BatchResult:
        """Execute device chains sequentially (fallback mode)."""
        import time
        start = time.perf_counter()
        results = []
        
        for chain in self._chains.values():
            try:
                result = await self._execute_chain(chain, relay_manager, dfr0971_manager)
                results.append(result)
            except Exception as e:
                results.append(e)
        
        elapsed = (time.perf_counter() - start) * 1000
        return self._aggregate_results(results, elapsed)
    
    async def _execute_chain(self, chain: DeviceOperationChain, 
                             relay_manager, dfr0971_manager) -> bool:
        """Execute a single device's operation chain sequentially."""
        for op in chain.operations:
            if isinstance(op, RelayOperation):
                success, _ = await asyncio.to_thread(
                    relay_manager.set_device_state,
                    op.location, op.cluster, op.device_name, 
                    1 if op.state else 0, "auto"
                )
                if not success:
                    raise RuntimeError(f"Relay operation failed for {op.device_name}")
            elif isinstance(op, DimmerOperation):
                success = await asyncio.to_thread(
                    dfr0971_manager.set_intensity,
                    op.board_id, op.channel, op.intensity
                )
                if not success:
                    raise RuntimeError(f"Dimmer operation failed for {op.device_name}")
        return True
    
    def _get_or_create_chain(self, device_name: str) -> DeviceOperationChain:
        if device_name not in self._chains:
            self._chains[device_name] = DeviceOperationChain(device_name=device_name)
        return self._chains[device_name]
    
    def _find_dimmer_insert_position(self, chain: DeviceOperationChain) -> int:
        """Find position to insert dimmer op (after relay_on, before relay_off)."""
        for i, op in enumerate(chain.operations):
            if isinstance(op, RelayOperation) and not op.state:
                return i  # Insert before relay_off
        return len(chain.operations)  # Append at end
    
    def _aggregate_results(self, results: list, elapsed_ms: float) -> BatchResult:
        success = 0
        failures = {}
        for i, result in enumerate(results):
            device_name = list(self._chains.keys())[i] if i < len(self._chains) else f"unknown_{i}"
            if isinstance(result, Exception):
                failures[device_name] = str(result)
            else:
                success += 1
        return BatchResult(success, len(failures), failures, elapsed_ms)
```

**Must NOT do:**
- Do NOT modify existing RelayManager or DFR0971Manager classes
- Do NOT bypass interlock checks
- Do NOT add new I2C bus locking (separate buses already)

**Recommended Agent Profile:**
- **Category**: `quick`
- **Skills**: [`git-master`]
- **Reason**: Single new file creation, well-defined interface

**Acceptance Criteria:**
- [ ] File created: `app/control/hardware_batch.py`
- [ ] Classes defined: `RelayOperation`, `DimmerOperation`, `DeviceOperationChain`, `BatchResult`, `HardwareBatchExecutor`
- [ ] `python -c "from app.control.hardware_batch import HardwareBatchExecutor"` → exit code 0
- [ ] Methods exist: `queue_light_on`, `queue_light_off`, `execute`

**Agent-Executed QA Scenarios:**

```
Scenario: Module imports successfully
  Tool: Bash
  Steps:
    1. cd /home/antoine/ProjectCEA/Infrastructure/automation-service
    2. python -c "from app.control.hardware_batch import HardwareBatchExecutor, BatchResult"
    3. Assert: exit code 0
  Evidence: Exit code captured

Scenario: HardwareBatchExecutor has required methods
  Tool: Bash
  Steps:
    1. python -c "from app.control.hardware_batch import HardwareBatchExecutor; e = HardwareBatchExecutor(); assert hasattr(e, 'queue_light_on'); assert hasattr(e, 'queue_light_off'); assert hasattr(e, 'execute')"
    2. Assert: exit code 0
  Evidence: Exit code captured
```

**Commit**: YES
- Message: `feat(automation): add HardwareBatchExecutor for parallel I2C`
- Files: `app/control/hardware_batch.py`

---

### Task 2: Add Queuing Mode to DeviceController

**File:** `app/control/device_controller.py` (MODIFIED)

**What to do:**
- Add optional `batch_executor: HardwareBatchExecutor | None` parameter to:
  - `process_device()`
  - `_control_dimmable_light()`
  - `_control_binary_device()`
- When `batch_executor` is provided: queue ops instead of executing
- When `batch_executor` is None: keep existing direct execution (backwards compatible)

**Code Changes:**

```python
# In _control_dimmable_light(), change from:
if intensity > 0:
    relay_ok, _ = self.relay_manager.set_device_state(...)
    dimmer_ok = self.dfr0971_manager.set_intensity(...)
else:
    dimmer_ok = self.dfr0971_manager.set_intensity(..., 0)
    relay_ok, _ = self.relay_manager.set_device_state(..., 0)

# To:
if batch_executor is not None:
    if intensity > 0:
        batch_executor.queue_light_on(location, cluster, device_name, board_id, channel, intensity)
    else:
        batch_executor.queue_light_off(location, cluster, device_name, board_id, channel)
    return True  # Execution deferred to batch
else:
    # Existing direct execution code
    if intensity > 0:
        relay_ok, _ = self.relay_manager.set_device_state(...)
        ...
```

**Must NOT do:**
- Do NOT change method signatures for external callers (add optional params only)
- Do NOT remove any existing functionality
- Do NOT change logging behavior

**Recommended Agent Profile:**
- **Category**: `quick`
- **Skills**: [`git-master`]

**Acceptance Criteria:**
- [ ] `process_device()` accepts optional `batch_executor` parameter
- [ ] `_control_dimmable_light()` accepts optional `batch_executor` parameter
- [ ] `_control_binary_device()` accepts optional `batch_executor` parameter
- [ ] Existing tests pass unchanged (backwards compatible)

**Agent-Executed QA Scenarios:**

```
Scenario: DeviceController accepts batch_executor parameter
  Tool: Bash
  Steps:
    1. cd /home/antoine/ProjectCEA/Infrastructure/automation-service
    2. python -c "from app.control.device_controller import DeviceController; import inspect; sig = inspect.signature(DeviceController.process_device); assert 'batch_executor' in sig.parameters"
    3. Assert: exit code 0
  Evidence: Exit code captured

Scenario: Backwards compatibility
  Tool: Bash
  Steps:
    1. pytest tests/ -k "device" --tb=short -q 2>/dev/null || true
    2. Assert: No import errors
  Evidence: pytest output
```

**Commit**: YES
- Message: `feat(automation): add batch_executor queuing to DeviceController`
- Files: `app/control/device_controller.py`

---

### Task 3: Integrate into DeviceProcessor

**File:** `app/control/device_processor.py` (MODIFIED)

**What to do:**
- Import `HardwareBatchExecutor` and `get_flag`
- In `process_cluster()` method:
  - Check `get_flag("PARALLEL_I2C")` at start
  - If enabled: create `HardwareBatchExecutor` instance
  - Pass to each `device_controller.process_device()` call
  - After all devices: `await batch_executor.execute()`
  - Handle BatchResult: log failures, record timing
- If flag disabled: no batch_executor passed (existing behavior)

**Code Changes:**

```python
from app.control.hardware_batch import HardwareBatchExecutor
from app.feature_flags import get_flag

async def process_cluster(self, location: str, cluster: str, ...):
    # Check feature flag
    use_parallel = get_flag("PARALLEL_I2C", default=False)
    batch_executor = HardwareBatchExecutor() if use_parallel else None
    
    # Process all devices (queue operations if parallel)
    for device_name, device_info in cluster_devices.items():
        await self.device_controller.process_device(
            ...,
            batch_executor=batch_executor
        )
    
    # Execute batched operations if parallel mode
    if batch_executor is not None:
        result = await batch_executor.execute(
            self.device_controller.relay_manager,
            self.device_controller.dfr0971_manager
        )
        if result.failure_count > 0:
            logger.warning(f"Batch execution had {result.failure_count} failures: {result.failures}")
        logger.debug(f"Batch execution completed in {result.total_time_ms:.1f}ms")
```

**Must NOT do:**
- Do NOT modify the device iteration logic
- Do NOT change how devices are selected or filtered
- Do NOT add new parameters to public API

**Recommended Agent Profile:**
- **Category**: `quick`
- **Skills**: [`git-master`]

**Acceptance Criteria:**
- [ ] PARALLEL_I2C=true triggers batch execution
- [ ] PARALLEL_I2C=false uses sequential execution
- [ ] Batch execution failures logged with device details
- [ ] `python -c "from app.control.device_processor import DeviceProcessor"` → exit 0

**Agent-Executed QA Scenarios:**

```
Scenario: Feature flag controls execution mode
  Tool: Bash
  Steps:
    1. cd /home/antoine/ProjectCEA/Infrastructure/automation-service
    2. python -c "from app.control.device_processor import DeviceProcessor; from app.control.hardware_batch import HardwareBatchExecutor"
    3. Assert: exit code 0
  Evidence: Exit code captured
```

**Commit**: YES
- Message: `feat(automation): integrate parallel I2C batch execution`
- Files: `app/control/device_processor.py`

---

### Task 4: Add Comprehensive Tests

**File:** `tests/test_parallel_i2c.py` (NEW)

**What to do:**
Create comprehensive test suite with 12+ test cases:

**HardwareBatchExecutor tests:**
- `test_queue_relay_on` - verify operation queued
- `test_queue_light_on_creates_chain` - verify [relay, dimmer] chain
- `test_queue_light_off_creates_chain` - verify [dimmer, relay] chain  
- `test_execute_parallel_flag_enabled` - verify asyncio.gather used
- `test_execute_sequential_flag_disabled` - verify sequential execution
- `test_partial_failure_handling` - one device fails, others succeed
- `test_timeout_handling` - chain exceeds 500ms timeout
- `test_empty_batch` - no operations queued

**DeviceController tests:**
- `test_process_device_with_batch_executor` - operations queued not executed
- `test_process_device_without_batch_executor` - backwards compatible

**DeviceProcessor tests:**
- `test_process_cluster_parallel_flag_enabled` - batch execution used
- `test_process_cluster_parallel_flag_disabled` - sequential execution

**Sequencing constraint tests:**
- `test_relay_on_before_dimmer_set` - power before signal
- `test_dimmer_zero_before_relay_off` - signal before power off

**Must NOT do:**
- Do NOT require real hardware for tests
- Do NOT modify existing tests
- Do NOT skip testing error cases

**Recommended Agent Profile:**
- **Category**: `quick`
- **Skills**: [`git-master`]

**Acceptance Criteria:**
- [ ] File created: `tests/test_parallel_i2c.py`
- [ ] Minimum 12 test cases covering all scenarios
- [ ] `pytest tests/test_parallel_i2c.py -v` → All tests PASS

**Agent-Executed QA Scenarios:**

```
Scenario: All parallel I2C tests pass
  Tool: Bash
  Steps:
    1. cd /home/antoine/ProjectCEA/Infrastructure/automation-service
    2. pytest tests/test_parallel_i2c.py -v
    3. Assert: exit code 0
    4. Assert: output contains "passed"
  Evidence: .sisyphus/evidence/task-4-tests.txt
```

**Commit**: YES
- Message: `test(automation): add parallel I2C test suite`
- Files: `tests/test_parallel_i2c.py`

---

### Task 5: Verify Timing Improvement

**What to do:**
- Enable PARALLEL_I2C feature flag
- Run automation service for 5 minutes to collect timing data
- Query timing API endpoint for histogram data
- Verify P95 < 500ms, P99 < 800ms, MAX < 1000ms
- Compare with baseline (flag disabled)
- Document results in evidence file

**Must NOT do:**
- Do NOT modify code in this task (verification only)

**Recommended Agent Profile:**
- **Category**: `quick`
- **Skills**: []

**Acceptance Criteria:**
- [ ] P95 execution time < 500ms with PARALLEL_I2C=true
- [ ] P99 execution time < 800ms with PARALLEL_I2C=true
- [ ] MAX execution time < 1000ms with PARALLEL_I2C=true
- [ ] Evidence file created with timing comparison

**Agent-Executed QA Scenarios:**

```
Scenario: P95 meets target with parallel I2C
  Tool: Bash
  Steps:
    1. curl -X POST http://localhost:8001/api/flags -H "Content-Type: application/json" -d '{"name":"PARALLEL_I2C","enabled":true}'
    2. sleep 60  # Wait 1 minute minimum for data
    3. curl http://localhost:8001/api/timing/histogram
    4. Parse response, extract p95_ms
    5. Assert: p95_ms < 500
  Evidence: .sisyphus/evidence/task-5-timing.json
```

**Commit**: NO (verification only)

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Race conditions** | High | asyncio.Lock per resource if needed, thorough testing |
| **Sequencing violations** | High | Per-device chains maintain order, explicit phase separation |
| **I2C bus contention** | Medium | Separate buses (0 and 1) already physically isolated |
| **Partial failures** | Medium | `return_exceptions=True`, continue with successful ops |
| **Debug difficulty** | Medium | Detailed logging with chain execution timing |
| **Performance regression** | Low | Feature flag for instant rollback |

---

## Rollback Strategy

### Feature Flag Control

```bash
# Disable parallel I2C (instant rollback)
curl -X POST http://localhost:8001/api/flags \
  -H "Content-Type: application/json" \
  -d '{"name":"PARALLEL_I2C","enabled":false}'

# Or via Redis directly
redis-cli SET feature:PARALLEL_I2C false
```

### Automatic Fallback Triggers
- Parallel execution latency > sequential + 50ms consistently
- >10% of batch operations failing
- Device sequencing constraint violations detected

---

## Expected Performance Improvements

| Device Mix | Sequential | Parallel | Improvement |
|------------|-----------|----------|-------------|
| 4 relay-only | 40ms | 40ms | 0% |
| 2 relay + 2 dimmable | 320ms | 160ms | 50% |
| 4 dimmable | 600ms | 160ms | 73% |
| 8 mixed (4+4) | 640ms | 300ms | 53% |

**Target:** P95 < 500ms (currently ~570ms worst case)

---

## Commit Strategy

| Task | Message | Files |
|------|---------|-------|
| 1 | `feat(automation): add HardwareBatchExecutor for parallel I2C` | hardware_batch.py |
| 2 | `feat(automation): add batch_executor queuing to DeviceController` | device_controller.py |
| 3 | `feat(automation): integrate parallel I2C batch execution` | device_processor.py |
| 4 | `test(automation): add parallel I2C test suite` | test_parallel_i2c.py |
| 5 | N/A (verification only) | - |

---

## Success Criteria

### Final Checklist
- [ ] All "Must Have" items present
- [ ] All "Must NOT Have" guardrails respected
- [ ] All tests pass with PARALLEL_I2C=true
- [ ] All tests pass with PARALLEL_I2C=false
- [ ] P95 execution time < 500ms
- [ ] Interlock checks verified working
- [ ] Sequencing constraints preserved
- [ ] Feature flag enables instant rollback
