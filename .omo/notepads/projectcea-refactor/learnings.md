# ProjectCEA Refactor Notepad

## Wisdom (Accumulated)

### Critical System Rules
- Control loop latency: ≤5 seconds max, target 1-2 seconds
- VPD is master controller for humidity
- Heating safety must be integrated - crop protection critical
- Redis is source of truth for current values
- TimescaleDB for historical data

### Architecture Decisions
- `cea:*` prefix is the canonical Redis key schema
- DFR0971 uses I2C bus 1, addresses 0x88/0x89/0x90
- MCP23017 uses I2C bus 0, address 0x27
- Sequential I2C is sufficient (parallel adds no value on single bus)

### Code Patterns to Follow
- Use `get_flag(name, default)` from `app.feature_flags`
- All state changes logged with appropriate level (WARNING/CRITICAL/EMERGENCY)
- Hardware operations wrapped in try/except with fallback to simulation
