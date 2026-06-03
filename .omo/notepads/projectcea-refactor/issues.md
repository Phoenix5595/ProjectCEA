# Issues / Gotchas

## DFR0971 Board 0x59 Hacks
- Lines 205-216: Special case for 0x59 in set_voltage()
- Lines 391-395: Special case in store_settings()
- Lines 524-528: Special case in DFR0971Manager.add_board()
- Lines 424-457: force_reinitialize() method exists only for 0x59

## Hardware Batch Parallel
- Line 355: `get_flag("PARALLEL_I2C", default=False)` enables parallel path
- Lines 363-387: asyncio.gather() parallel execution path
- Lines 388-403: Sequential fallback path

## Heating Safety TODO
- device_processor.py line 67: TODO comment for failsafe logic
- Need to wire HeatingFailureSafety in process_devices()

## Redis Key Patterns
- OLD: sensor:{cluster}:{sensor}:last_good, mode:{location}:{cluster}
- NEW: cea:sensor:{location}:{cluster}:{sensor}_last_good, cea:mode:{location}:{cluster}
