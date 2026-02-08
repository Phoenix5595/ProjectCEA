# Config Schema Validation - Prevent Relay Channel Conflicts

## TL;DR

> **Quick Summary**: Add Pydantic schema validation for automation_config.yaml at config load time to prevent relay channel conflicts and other misconfigurations from ever starting the service.
> 
> **Deliverables**:
> - Pydantic model hierarchy for automation_config.yaml
> - Startup-time validation integrated into ConfigLoader
> - Fail-fast behavior with clear, actionable error messages
> - TDD test suite for all validation rules
> - Updated AGENTS.md documentation
> 
> **Estimated Effort**: Medium (2-3 days)
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 0 (revert) → Task 1 (schema) → Task 2 (integration) → Task 6 (docs)

---

## Context

### Original Request
Prevent the relay channel conflict bug class from happening again. Veg Room lights were OFF because both Flower Room and Veg Room devices mapped to relay channels 3, 4, 5 - the control loop kept fighting itself every 2 seconds.

### Interview Summary
**Key Discussions**:
- Solution Level: Full Schema Validation (Option C) with Pydantic models
- Prevention Point: Config load time - invalid configs cannot start the service
- Failure Behavior: Fail hard with clear error messages
- Validation Scope: ALL devices with relay channels (lights, fans, heaters, dehumidifiers)
- Test Strategy: TDD - write failing tests first
- Documentation: Update relevant AGENTS.md files

**Research Findings**:
- Current validation in `validation.py` only runs on API updates, not at startup
- ConfigLoader in `app/config.py` loads YAML without Pydantic validation
- Existing Pydantic patterns in routes/ (IntensityControl, SetpointUpdate, etc.) can be followed
- Hardware separation is CRITICAL: relay channels (MCP23017, 0-15) vs dimming (DFR0971 DAC, board_id + channel)

### Metis Review
**Identified Gaps** (addressed):
- Cross-domain validation question: Validation will be per-domain (relay conflicts separate from dimming conflicts)
- Migration path: Not needed - current production config is valid after recent fix
- Error message formatting: Will follow existing logging patterns in the codebase
- Config versioning: Out of scope - can be added later if needed

---

## Work Objectives

### Core Objective
Create Pydantic schema validation for automation_config.yaml that catches configuration errors (especially relay channel conflicts) at config load time, preventing invalid configurations from ever starting the automation service.

### Concrete Deliverables
- `app/models/config_schema.py` - Pydantic models for automation_config.yaml
- Modified `app/config.py` - Integration with ConfigLoader
- `tests/config/test_config_validation.py` - TDD test suite
- Updated `AGENTS.md` files - New validation rules documented

### Definition of Done
- [ ] `pytest tests/config/test_config_validation.py` → All tests pass
- [ ] Service startup with duplicate relay channels → Fails with clear error
- [ ] Service startup with valid config → Succeeds normally
- [ ] `grep -r "validate" app/config.py` → Shows validation integration

### Must Have
- Relay channel conflict detection (duplicate channels across all devices)
- Relay channel range validation (0-15)
- Dimming board/channel reference validation
- device_type enum validation
- Clear, actionable error messages on validation failure
- Fail-fast startup behavior (invalid config = no service start)
- TDD tests covering all validation rules

### Must NOT Have (Guardrails)
- NO conflating relay and dimming hardware in validation logic
- NO cross-domain checks (relay channel vs dimming_board_id are separate)
- NO config versioning or migration system (out of scope)
- NO soft-fail or "degraded mode" startup options
- NO changes to existing API validation behavior (routes/devices.py stays as-is)
- NO modifications to hardware drivers or control loop logic

---

## Verification Strategy (MANDATORY)

### Test Decision
- **Infrastructure exists**: YES - pytest already configured
- **User wants tests**: TDD
- **Framework**: pytest (existing)

### TDD Structure

Each TODO follows RED-GREEN-REFACTOR:

**Task Structure:**
1. **RED**: Write failing test first
   - Test file: `tests/config/test_config_validation.py`
   - Test command: `pytest tests/config/test_config_validation.py -v`
   - Expected: FAIL (test exists, implementation doesn't)
2. **GREEN**: Implement minimum code to pass
   - Command: `pytest tests/config/test_config_validation.py -v`
   - Expected: PASS
3. **REFACTOR**: Clean up while keeping green
   - Command: `pytest tests/config/test_config_validation.py -v`
   - Expected: PASS (still)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 0 (FIRST - Before anything else):
└── Task 0: Revert today's commits to known good state

Wave 1 (After Wave 0):
├── Task 1: Create Pydantic schema models (TDD)
└── Task 3: Create test fixtures (valid/invalid configs)

Wave 2 (After Wave 1):
├── Task 2: Integrate validation into ConfigLoader
└── Task 4: Add startup failure tests

Wave 3 (After Wave 2):
└── Task 5: Update AGENTS.md documentation
```

### Dependency Matrix

| Task | Depends On | Blocks | Can Parallelize With |
|------|------------|--------|---------------------|
| 0 | None | 1, 2, 3, 4, 5 | None (must be first) |
| 1 | 0 | 2, 4 | 3 |
| 2 | 1 | 4, 5 | None |
| 3 | 0 | 2, 4 | 1 |
| 4 | 2, 3 | 5 | None |
| 5 | 4 | None | None (final) |

### Agent Dispatch Summary

| Wave | Tasks | Recommended Agents |
|------|-------|-------------------|
| 0 | 0 | delegate_task(category="quick", load_skills=["git-master"], run_in_background=false) |
| 1 | 1, 3 | delegate_task(category="unspecified-high", load_skills=[], run_in_background=true) |
| 2 | 2, 4 | dispatch after Wave 1 completes |
| 3 | 5 | final documentation task |

---

## TODOs

- [ ] 0. Revert Today's Commits to Known Good State

  **What to do**:
  - Revert all 3 commits from today (2026-01-29):
    - `edda103` - fix(scheduler): allow 0% intensity for NIGHT schedules
    - `95f3f1a` - Update MCP23017 to I2C bus 0 address 0x27 (new relay board)
    - `29ef9ec` - Fix SQL functions: add ::REAL casts, remove duplicate code blocks
  - Also discard uncommitted changes to `automation_config.yaml`
  - Sync production config after revert: `sudo ./deploy.sh`
  - Verify services start correctly after revert

  **Must NOT do**:
  - Do NOT delete .sisyphus/ files (plans, drafts are needed)
  - Do NOT revert changes older than today
  - Do NOT force push (these commits haven't been shared)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Simple git revert operation
  - **Skills**: `["git-master"]`
    - `git-master`: Git operations including revert
  - **Skills Evaluated but Omitted**:
    - All others not applicable

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 0 (MUST BE FIRST)
  - **Blocks**: All other tasks (1, 2, 3, 4, 5)
  - **Blocked By**: None

  **References**:

  **Git State**:
  - Current HEAD: `edda103`
  - Target state: Parent of `29ef9ec` (first commit today)
  - Commits to revert: 3 total

  **Deployment References**:
  - `./deploy.sh` - Production deployment script
  - `/opt/projectcea/current/` - Production symlink

  **Acceptance Criteria**:

  **Git Revert:**
  - [ ] All 3 commits from today reverted
  - [ ] `git log --oneline -5` shows no commits from 2026-01-29
  - [ ] `automation_config.yaml` matches state from yesterday

  **Automated Verification:**
  ```bash
  # Agent runs:
  cd /home/antoine/ProjectCEA
  
  # Verify today's commits are gone
  git log --oneline --since="2026-01-29 00:00" | wc -l
  # Assert: Output is "0"
  
  # Verify config is back to known good state
  git diff HEAD -- Infrastructure/automation-service/automation_config.yaml | wc -l
  # Assert: Output is "0" (no uncommitted changes)
  
  # Verify services still work (after deploy)
  sudo systemctl is-active automation-service
  # Assert: Output is "active"
  ```

  **Deployment:**
  - [ ] `sudo ./deploy.sh` completes successfully
  - [ ] `sudo systemctl status automation-service` shows active

  **Commit**: NO (this is a revert, creates its own commits)

---

- [ ] 1. Create Pydantic Schema Models (TDD)

  **What to do**:
  - Create `app/models/config_schema.py` with Pydantic models
  - Model hierarchy:
    - `AutomationConfig` (root model)
    - `HardwareConfig` (i2c_bus, i2c_address, simulation, dfr0971_boards)
    - `DFR0971Board` (board_id, i2c_address, name)
    - `DeviceConfig` (base device fields)
    - `LightDeviceConfig(DeviceConfig)` (adds dimming fields)
    - `DeviceType` (Enum: light, fan, heater, dehumidifier, humidifier, co2, vent)
  - Add validators:
    - `validate_relay_channel_range`: channel must be 0-15
    - `validate_relay_channel_uniqueness`: no duplicate channels across all devices
    - `validate_dimming_board_reference`: dimming_board_id must exist in dfr0971_boards
    - `validate_dimming_channel_range`: dimming_channel must be 0-1
    - `validate_device_type`: must be valid enum value
  - Write failing tests FIRST, then implement

  **Starter Schema Template** (use as starting point):
  ```python
  # app/models/config_schema.py
  """Pydantic schema validation for automation_config.yaml."""
  
  from enum import Enum
  from typing import Any
  
  from pydantic import BaseModel, field_validator, model_validator
  
  
  class DeviceType(str, Enum):
      """Valid device types."""
      LIGHT = "light"
      FAN = "fan"
      HEATER = "heater"
      DEHUMIDIFIER = "dehumidifier"
      HUMIDIFIER = "humidifier"
      CO2 = "co2"
      VENT = "vent"
  
  
  class DFR0971Board(BaseModel):
      """DFR0971 DAC board configuration."""
      board_id: int
      i2c_address: int
      name: str
  
  
  class HardwareConfig(BaseModel):
      """Hardware configuration section."""
      i2c_bus: int
      i2c_address: int
      simulation: bool = False
      dfr0971_boards: list[DFR0971Board] = []
  
  
  class DeviceConfig(BaseModel):
      """Base device configuration."""
      channel: int
      device_type: DeviceType
      display_name: str | None = None
      pid_enabled: bool = False
      interlock_with: list[str] = []
      # Dimming fields (optional, for lights)
      dimming_enabled: bool = False
      dimming_type: str | None = None
      dimming_board_id: int | None = None
      dimming_channel: int | None = None
      safety_level: int = 0
  
      @field_validator("channel")
      @classmethod
      def validate_relay_channel_range(cls, v: int) -> int:
          """Relay channel must be 0-15 (MCP23017 has 16 channels)."""
          if not 0 <= v <= 15:
              raise ValueError(f"Relay channel {v} out of range (must be 0-15)")
          return v
  
      @field_validator("dimming_channel")
      @classmethod
      def validate_dimming_channel_range(cls, v: int | None) -> int | None:
          """Dimming channel must be 0-1 (DFR0971 has 2 channels per board)."""
          if v is not None and not 0 <= v <= 1:
              raise ValueError(f"Dimming channel {v} out of range (must be 0-1)")
          return v
  
  
  class AutomationConfig(BaseModel):
      """Root configuration model for automation_config.yaml."""
      hardware: HardwareConfig
      devices: dict[str, dict[str, dict[str, DeviceConfig]]]  # location -> cluster -> device_name -> config
      control: dict[str, Any] = {}
  
      @model_validator(mode="after")
      def validate_no_duplicate_relay_channels(self) -> "AutomationConfig":
          """Ensure no two devices share the same relay channel."""
          seen_channels: dict[int, str] = {}  # channel -> "location/cluster/device"
          
          for location, clusters in self.devices.items():
              for cluster, devices in clusters.items():
                  for device_name, device_config in devices.items():
                      channel = device_config.channel
                      full_path = f"{location}/{cluster}/{device_name}"
                      
                      if channel in seen_channels:
                          raise ValueError(
                              f"Duplicate relay channel {channel}: "
                              f"used by both '{seen_channels[channel]}' and '{full_path}'"
                          )
                      seen_channels[channel] = full_path
          
          return self
  
      @model_validator(mode="after")
      def validate_dimming_board_references(self) -> "AutomationConfig":
          """Ensure dimming_board_id references exist in hardware.dfr0971_boards."""
          valid_board_ids = {b.board_id for b in self.hardware.dfr0971_boards}
          
          for location, clusters in self.devices.items():
              for cluster, devices in clusters.items():
                  for device_name, device_config in devices.items():
                      if device_config.dimming_enabled and device_config.dimming_board_id is not None:
                          if device_config.dimming_board_id not in valid_board_ids:
                              raise ValueError(
                                  f"Device '{location}/{cluster}/{device_name}' references "
                                  f"dimming_board_id={device_config.dimming_board_id} "
                                  f"which does not exist in hardware.dfr0971_boards "
                                  f"(valid IDs: {valid_board_ids})"
                              )
          
          return self
  ```

  **Must NOT do**:
  - Do NOT conflate relay and dimming validation (separate validators)
  - Do NOT add cross-domain checks (relay vs dimming)
  - Do NOT modify existing validation.py (this is new, parallel validation)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Schema design requires careful modeling but isn't visual or ultra-complex
  - **Skills**: `[]`
    - No special skills needed - pure Python/Pydantic work
  - **Skills Evaluated but Omitted**:
    - `frontend-ui-ux`: Not applicable - backend schema work
    - `playwright`: Not applicable - no browser testing

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 3)
  - **Blocks**: Tasks 2, 4
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References** (existing code to follow):
  - `app/routes/lights.py:1-30` - Existing Pydantic model patterns (IntensityControl, VoltageControl)
  - `app/routes/setpoints.py:1-50` - More complex Pydantic models with nested structures
  - `app/routes/devices.py:20-60` - DeviceMappingUpdate, DeviceConfigUpdate models
  - `app/validation.py:1-100` - Existing validation logic patterns to align with

  **Config Structure References** (what we're modeling):
  - `automation_config.yaml:1-200` - Full YAML structure to model
  - `automation_config.yaml` devices section - Device hierarchy (location → cluster → device_name)
  - `automation_config.yaml` hardware section - DFR0971 board definitions

  **Test References** (testing patterns to follow):
  - `tests/` directory structure - Follow existing test organization
  - `tests/test_*.py` - Existing test patterns in the project

  **External References**:
  - Pydantic v2 docs: https://docs.pydantic.dev/latest/concepts/validators/
  - Pydantic model_validator: https://docs.pydantic.dev/latest/concepts/validators/#model-validators

  **Acceptance Criteria**:

  **TDD Phase - RED:**
  - [ ] Test file created: `tests/config/test_config_validation.py`
  - [ ] Test: `test_relay_channel_out_of_range` - expects ValidationError for channel=16
  - [ ] Test: `test_duplicate_relay_channels` - expects ValidationError for two devices with channel=3
  - [ ] Test: `test_invalid_dimming_board_reference` - expects ValidationError for dimming_board_id=99
  - [ ] Test: `test_invalid_device_type` - expects ValidationError for device_type="invalid"
  - [ ] Test: `test_valid_config_passes` - expects no error for valid config
  - [ ] `pytest tests/config/test_config_validation.py -v` → FAIL (5 failures, no implementation)

  **TDD Phase - GREEN:**
  - [ ] Schema file created: `app/models/config_schema.py`
  - [ ] All validators implemented
  - [ ] `pytest tests/config/test_config_validation.py -v` → PASS (5 tests, 0 failures)

  **Automated Verification:**
  ```bash
  # Agent runs:
  cd /home/antoine/ProjectCEA/Infrastructure/automation-service
  pytest tests/config/test_config_validation.py -v --tb=short
  # Assert: Exit code 0
  # Assert: Output contains "5 passed"
  ```

  **Commit**: YES
  - Message: `feat(config): add Pydantic schema models for automation_config.yaml`
  - Files: `app/models/config_schema.py`, `tests/config/test_config_validation.py`
  - Pre-commit: `pytest tests/config/test_config_validation.py`

---

- [ ] 2. Integrate Validation into ConfigLoader

  **What to do**:
  - Modify `app/config.py` ConfigLoader class
  - After YAML is loaded, parse it through Pydantic schema
  - If validation fails, raise clear error with field path and message
  - If validation passes, proceed with existing behavior
  - Add `validate_config()` method that can be called explicitly
  - Ensure validation runs automatically in `__init__` or `load()`

  **Exact Integration Point** (app/config.py line 48):
  ```python
  # BEFORE (current code):
  def load(self) -> None:
      """Load configuration from YAML files."""
      # Load main config
      with open(self.config_path) as f:
          self._config = yaml.safe_load(f) or {}
      # ... rest of method
  
  # AFTER (with validation):
  def load(self) -> None:
      """Load configuration from YAML files."""
      # Load main config
      with open(self.config_path) as f:
          self._config = yaml.safe_load(f) or {}
      
      # Validate config using Pydantic schema
      self._validate_config()
      
      # ... rest of method (unchanged)
  
  def _validate_config(self) -> None:
      """Validate loaded config against Pydantic schema.
      
      Raises:
          ValueError: If config validation fails, with clear field path and message.
      """
      from app.models.config_schema import AutomationConfig
      from pydantic import ValidationError
      
      try:
          AutomationConfig.model_validate(self._config)
          logger.info("Config validation passed")
      except ValidationError as e:
          # Format error with clear field paths
          errors = []
          for error in e.errors():
              field_path = " -> ".join(str(loc) for loc in error["loc"])
              errors.append(f"  {field_path}: {error['msg']}")
          
          error_msg = f"Config validation failed:\n" + "\n".join(errors)
          logger.error(error_msg)
          raise ValueError(error_msg) from e
  ```

  **Must NOT do**:
  - Do NOT change existing ConfigLoader public API (getters, reload, etc.)
  - Do NOT add "soft fail" or "warn only" modes
  - Do NOT modify how the YAML is read (only add validation after load)

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration work requires understanding existing code flow
  - **Skills**: `[]`
    - No special skills needed
  - **Skills Evaluated but Omitted**:
    - `git-master`: Will be used for commit, but not primary skill needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential after Wave 1)
  - **Blocks**: Tasks 4, 5
  - **Blocked By**: Task 1 (needs schema models)

  **References**:

  **Primary Code References**:
  - `app/config.py:1-200` - ConfigLoader class (entire file) - where integration happens
  - `app/config.py:__init__` - Initialization where validation should be added
  - `app/config.py:load()` or `reload()` - YAML loading methods

  **Schema References** (created in Task 1):
  - `app/models/config_schema.py` - Import and use the Pydantic models

  **Pattern References**:
  - `app/container.py:initialize()` - How startup errors are currently handled
  - Existing exception handling patterns in the codebase

  **Acceptance Criteria**:

  **Implementation:**
  - [ ] ConfigLoader imports and uses Pydantic schema from `app/models/config_schema.py`
  - [ ] Validation runs during ConfigLoader initialization
  - [ ] ValidationError includes: field path, error message, suggested fix
  - [ ] Valid configs load normally (no behavior change for valid configs)

  **Automated Verification:**
  ```bash
  # Agent runs - test invalid config rejection:
  cd /home/antoine/ProjectCEA/Infrastructure/automation-service
  
  # Create temporary invalid config
  cat > /tmp/invalid_config.yaml << 'EOF'
  hardware:
    i2c_bus: 1
    i2c_address: 32
  devices:
    "Test Room":
      main:
        device_1:
          channel: 3
          device_type: light
        device_2:
          channel: 3
          device_type: fan
  EOF
  
  # Test that ConfigLoader rejects it
  python3 -c "
  import sys
  sys.path.insert(0, 'app')
  from config import ConfigLoader
  try:
      c = ConfigLoader(config_path='/tmp/invalid_config.yaml')
      print('ERROR: Should have raised ValidationError')
      sys.exit(1)
  except Exception as e:
      if 'duplicate' in str(e).lower() or 'channel' in str(e).lower():
          print('SUCCESS: Validation caught duplicate channel')
          sys.exit(0)
      else:
          print(f'ERROR: Wrong exception: {e}')
          sys.exit(1)
  "
  # Assert: Exit code 0
  # Assert: Output contains "SUCCESS"
  ```

  **Commit**: YES
  - Message: `feat(config): integrate Pydantic validation into ConfigLoader`
  - Files: `app/config.py`
  - Pre-commit: `pytest tests/config/`

---

- [ ] 3. Create Test Fixtures (Valid/Invalid Configs)

  **What to do**:
  - Create `tests/config/fixtures/` directory
  - Create `valid_config.yaml` - a minimal valid configuration
  - Create `invalid_duplicate_relay.yaml` - duplicate relay channels
  - Create `invalid_channel_range.yaml` - channel > 15
  - Create `invalid_dimming_ref.yaml` - references non-existent dimming board
  - Create `invalid_device_type.yaml` - unknown device_type value
  - Create a `conftest.py` with pytest fixtures that load these files

  **Concrete Fixture Content**:

  **tests/config/fixtures/valid_config.yaml**:
  ```yaml
  hardware:
    i2c_bus: 1
    i2c_address: 32
    simulation: true
    dfr0971_boards:
      - board_id: 0
        i2c_address: 88
        name: Test Board
  devices:
    Test Room:
      main:
        light_1:
          channel: 0
          device_type: light
          dimming_enabled: true
          dimming_board_id: 0
          dimming_channel: 0
        fan_1:
          channel: 1
          device_type: fan
  control:
    update_interval: 2
  ```

  **tests/config/fixtures/invalid_duplicate_relay.yaml**:
  ```yaml
  hardware:
    i2c_bus: 1
    i2c_address: 32
  devices:
    Test Room:
      main:
        device_1:
          channel: 3
          device_type: light
        device_2:
          channel: 3
          device_type: fan
  ```

  **tests/config/fixtures/invalid_channel_range.yaml**:
  ```yaml
  hardware:
    i2c_bus: 1
    i2c_address: 32
  devices:
    Test Room:
      main:
        device_1:
          channel: 16
          device_type: heater
  ```

  **tests/config/fixtures/invalid_dimming_ref.yaml**:
  ```yaml
  hardware:
    i2c_bus: 1
    i2c_address: 32
    dfr0971_boards:
      - board_id: 0
        i2c_address: 88
        name: Board 0
  devices:
    Test Room:
      main:
        light_1:
          channel: 0
          device_type: light
          dimming_enabled: true
          dimming_board_id: 99
          dimming_channel: 0
  ```

  **tests/config/fixtures/invalid_device_type.yaml**:
  ```yaml
  hardware:
    i2c_bus: 1
    i2c_address: 32
  devices:
    Test Room:
      main:
        device_1:
          channel: 0
          device_type: invalid_type
  ```

  **tests/config/conftest.py**:
  ```python
  """Pytest fixtures for config validation tests."""
  
  from pathlib import Path
  
  import pytest
  import yaml
  
  
  FIXTURES_DIR = Path(__file__).parent / "fixtures"
  
  
  @pytest.fixture
  def valid_config() -> dict:
      """Load valid test configuration."""
      with open(FIXTURES_DIR / "valid_config.yaml") as f:
          return yaml.safe_load(f)
  
  
  @pytest.fixture
  def invalid_duplicate_relay_config() -> dict:
      """Load config with duplicate relay channels."""
      with open(FIXTURES_DIR / "invalid_duplicate_relay.yaml") as f:
          return yaml.safe_load(f)
  
  
  @pytest.fixture
  def invalid_channel_range_config() -> dict:
      """Load config with out-of-range relay channel."""
      with open(FIXTURES_DIR / "invalid_channel_range.yaml") as f:
          return yaml.safe_load(f)
  
  
  @pytest.fixture
  def invalid_dimming_ref_config() -> dict:
      """Load config with invalid dimming board reference."""
      with open(FIXTURES_DIR / "invalid_dimming_ref.yaml") as f:
          return yaml.safe_load(f)
  
  
  @pytest.fixture
  def invalid_device_type_config() -> dict:
      """Load config with invalid device type."""
      with open(FIXTURES_DIR / "invalid_device_type.yaml") as f:
          return yaml.safe_load(f)
  ```

  **Must NOT do**:
  - Do NOT copy the full production config (use minimal test configs)
  - Do NOT include secrets or sensitive data in fixtures

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Creating YAML test fixtures is straightforward
  - **Skills**: `[]`
    - No special skills needed
  - **Skills Evaluated but Omitted**:
    - All skills omitted - simple file creation task

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 1 (with Task 1)
  - **Blocks**: Tasks 2, 4
  - **Blocked By**: Task 0 (must revert first)

  **References**:

  **Structure Reference**:
  - `automation_config.yaml:1-50` - Minimal structure needed for valid config
  - `automation_config.yaml` devices section - Required fields per device

  **Pattern References**:
  - `tests/` existing test structure - Follow organization patterns

  **Acceptance Criteria**:

  **Files Created:**
  - [ ] `tests/config/fixtures/valid_config.yaml` exists and is valid YAML
  - [ ] `tests/config/fixtures/invalid_duplicate_relay.yaml` has two devices with same channel
  - [ ] `tests/config/fixtures/invalid_channel_range.yaml` has channel: 16
  - [ ] `tests/config/fixtures/invalid_dimming_ref.yaml` has dimming_board_id: 99
  - [ ] `tests/config/fixtures/invalid_device_type.yaml` has device_type: "invalid_type"
  - [ ] `tests/config/conftest.py` with pytest fixtures

  **Automated Verification:**
  ```bash
  # Agent runs:
  cd /home/antoine/ProjectCEA/Infrastructure/automation-service
  
  # Verify fixtures exist and are valid YAML
  python3 -c "
  import yaml
  import os
  
  fixtures_dir = 'tests/config/fixtures'
  required = ['valid_config.yaml', 'invalid_duplicate_relay.yaml', 
              'invalid_channel_range.yaml', 'invalid_dimming_ref.yaml',
              'invalid_device_type.yaml']
  
  for f in required:
      path = os.path.join(fixtures_dir, f)
      assert os.path.exists(path), f'Missing: {path}'
      with open(path) as fp:
          yaml.safe_load(fp)  # Validates YAML syntax
      print(f'OK: {f}')
  
  print('All fixtures valid')
  "
  # Assert: Exit code 0
  # Assert: Output contains "All fixtures valid"
  ```

  **Commit**: YES (groups with Task 1)
  - Message: `test(config): add validation test fixtures`
  - Files: `tests/config/fixtures/*.yaml`, `tests/config/conftest.py`
  - Pre-commit: None (just YAML files)

---

- [x] 4. Add Startup Failure Integration Tests

  **What to do**:
  - Create `tests/config/test_startup_validation.py`
  - Test that automation-service fails to start with invalid configs
  - Test that automation-service starts normally with valid config
  - Use subprocess or pytest fixtures to test actual startup behavior
  - Verify error messages are clear and actionable

  **Must NOT do**:
  - Do NOT actually start the full service (use ConfigLoader directly)
  - Do NOT test hardware initialization (out of scope)
  - Do NOT modify production config during tests

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Integration testing requires understanding startup flow
  - **Skills**: `[]`
    - No special skills needed
  - **Skills Evaluated but Omitted**:
    - `playwright`: Not applicable - no browser testing

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after Tasks 1, 2, 3)
  - **Blocks**: Task 5
  - **Blocked By**: Tasks 2, 3 (needs integration and fixtures)

  **References**:

  **Code References**:
  - `app/config.py` - ConfigLoader with validation (from Task 2)
  - `app/container.py:initialize()` - Service initialization flow
  - `tests/config/fixtures/` - Test fixtures (from Task 3)

  **Pattern References**:
  - Existing pytest patterns in `tests/`

  **Acceptance Criteria**:

  **Tests Created:**
  - [ ] `tests/config/test_startup_validation.py` exists
  - [ ] Test: `test_startup_fails_on_duplicate_relay_channels`
  - [ ] Test: `test_startup_fails_on_invalid_channel_range`
  - [ ] Test: `test_startup_fails_on_invalid_dimming_reference`
  - [ ] Test: `test_startup_succeeds_with_valid_config`
  - [ ] Test: `test_error_message_contains_field_path`

  **Automated Verification:**
  ```bash
  # Agent runs:
  cd /home/antoine/ProjectCEA/Infrastructure/automation-service
  pytest tests/config/test_startup_validation.py -v --tb=short
  # Assert: Exit code 0
  # Assert: Output contains "5 passed" or similar
  ```

  **Commit**: YES
  - Message: `test(config): add startup validation integration tests`
  - Files: `tests/config/test_startup_validation.py`
  - Pre-commit: `pytest tests/config/`

---

- [ ] 5. Update AGENTS.md Documentation

  **What to do**:
  - Update `/home/antoine/ProjectCEA/AGENTS.md` - Add config validation to CRITICAL RULES
  - Update `/home/antoine/ProjectCEA/Infrastructure/automation-service/AGENTS.md` - Add validation details
  - Document:
    - New validation rules and what they catch
    - How to read validation error messages
    - How to fix common validation errors
    - Hardware mapping reference (relay channels, dimming boards)
  - Add examples of valid and invalid configurations

  **Must NOT do**:
  - Do NOT remove existing documentation
  - Do NOT add implementation details that could become stale
  - Do NOT duplicate the Pydantic schema (reference the code instead)

  **Recommended Agent Profile**:
  - **Category**: `writing`
    - Reason: Documentation task
  - **Skills**: `[]`
    - No special skills needed
  - **Skills Evaluated but Omitted**:
    - All skills omitted - documentation task

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 3 (final task)
  - **Blocks**: None (final)
  - **Blocked By**: Task 4 (needs all implementation complete)

  **References**:

  **Files to Update**:
  - `/home/antoine/ProjectCEA/AGENTS.md` - Root AGENTS.md
  - `/home/antoine/ProjectCEA/Infrastructure/automation-service/AGENTS.md` - Service-specific docs

  **Content References**:
  - `.sisyphus/drafts/prevent-relay-conflicts.md` - Hardware mapping tables
  - `app/models/config_schema.py` - Validation rules (from Task 1)
  - `automation_config.yaml` - Current correct configuration

  **Acceptance Criteria**:

  **Documentation Updated:**
  - [ ] Root AGENTS.md has new rule about config validation
  - [ ] Service AGENTS.md documents validation behavior
  - [ ] Hardware mapping table is accurate and complete
  - [ ] Error message examples included
  - [ ] Common fix procedures documented

  **Automated Verification:**
  ```bash
  # Agent runs:
  cd /home/antoine/ProjectCEA
  
  # Verify key content exists
  grep -q "config validation" AGENTS.md && echo "Root AGENTS.md updated"
  grep -q "relay channel" Infrastructure/automation-service/AGENTS.md && echo "Service AGENTS.md updated"
  grep -q "MCP23017" Infrastructure/automation-service/AGENTS.md && echo "Hardware mapping documented"
  
  # All checks pass
  echo "Documentation verification complete"
  # Assert: Exit code 0
  ```

  **Commit**: YES
  - Message: `docs(agents): add config validation rules and hardware mapping`
  - Files: `AGENTS.md`, `Infrastructure/automation-service/AGENTS.md`
  - Pre-commit: None (documentation only)

---

## Commit Strategy

| After Task | Message | Files | Verification |
|------------|---------|-------|--------------|
| 0 | `revert: undo today's changes (3 commits)` | Multiple files | `sudo systemctl is-active automation-service` |
| 1 + 3 | `feat(config): add Pydantic schema models for automation_config.yaml` | `app/models/config_schema.py`, `tests/config/` | `pytest tests/config/test_config_validation.py` |
| 2 | `feat(config): integrate Pydantic validation into ConfigLoader` | `app/config.py` | `pytest tests/config/` |
| 4 | `test(config): add startup validation integration tests` | `tests/config/test_startup_validation.py` | `pytest tests/config/` |
| 5 | `docs(agents): add config validation rules and hardware mapping` | `AGENTS.md`, `Infrastructure/automation-service/AGENTS.md` | Manual review |

---

## Success Criteria

### Verification Commands
```bash
# Full test suite passes
cd /home/antoine/ProjectCEA/Infrastructure/automation-service
pytest tests/config/ -v
# Expected: All tests pass

# Duplicate relay channel is caught
python3 -c "
from app.models.config_schema import AutomationConfig
AutomationConfig.model_validate({'devices': {'Room': {'main': {'d1': {'channel': 3}, 'd2': {'channel': 3}}}}})
" 2>&1 | grep -i "duplicate"
# Expected: ValidationError with "duplicate" in message

# Valid config loads normally
python3 -c "
from app.config import ConfigLoader
c = ConfigLoader()
print('Config loaded successfully')
"
# Expected: "Config loaded successfully"
```

### Final Checklist
- [ ] All "Must Have" items implemented and tested
- [ ] All "Must NOT Have" guardrails respected
- [ ] All tests pass: `pytest tests/config/ -v`
- [ ] Documentation updated in both AGENTS.md files
- [ ] Production config (`automation_config.yaml`) passes validation
