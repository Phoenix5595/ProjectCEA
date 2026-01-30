# Learnings from config-schema-validation task

- Created fixtures directory and YAML files for config validation testing.
- Files cover valid config and multiple invalid cases:
- - duplicate relay channels
- - out-of-range channel
- - invalid dimming reference
- - invalid device type
- Provided a pytest conftest.py to load these fixtures.
