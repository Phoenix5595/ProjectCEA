## Learnings from batch interval adjustment
- Reduced effective_setpoints batch interval from 10s to 5s to improve Grafana data freshness.
- Trade-off: increases DB write load due to more frequent batch flushes.
- Noted for future considerations when balancing data latency and DB capacity.

## ConfigEventBus Implementation (2026-02-23)

### Architecture Decisions

1. **Singleton Pattern with `__new__` + `__init__`**
   - Used `__new__` for singleton instance management
   - Used `__init__` with `hasattr` check to prevent re-initialization
   - Class-level type annotations required for strict type checking

2. **Bounded Queue for Backpressure**
   - `asyncio.Queue(maxsize=100)` prevents memory exhaustion
   - `put_nowait()` for non-blocking publish (returns `False` on full)
   - Graceful degradation: log warning and drop event if queue full

3. **Async Iterator Pattern for Subscribe**
   - `subscribe()` returns `AsyncIterator[ConfigChangeEvent]`
   - Uses `while True` loop with `yield` for continuous event stream
   - Subscriber count tracking for monitoring

### Type Checking Notes

- Class attributes must be declared at class level for pyright strict mode
- `dict[str, object]` preferred over `dict[str, Any]` to avoid `reportExplicitAny`
- No implicit string concatenation in f-strings (use single line or explicit concat)

### Integration Points

**Publishers (future):**
- `room_modes.py` - publish `RAMP_TIMES_CHANGED`, `MODE_CHANGED`
- `setpoints.py` - publish `SETPOINT_CHANGED`
- `pid.py` - publish `PID_PARAMS_CHANGED`

**Consumers (future):**
- `control_engine.py` - react to config changes in control loop
- `scheduler.py` - update schedules on `SCHEDULE_CHANGED`

### Event Types

```python
class ConfigEventType(Enum):
    RAMP_TIMES_CHANGED = "ramp_times_changed"
    SETPOINT_CHANGED = "setpoint_changed"
    PID_PARAMS_CHANGED = "pid_params_changed"
    SCHEDULE_CHANGED = "schedule_changed"
    MODE_CHANGED = "mode_changed"
```

### Usage Example

```python
from app.events import get_event_bus, ConfigChangeEvent, ConfigEventType

# Publisher
bus = get_event_bus()
event = ConfigChangeEvent(
    event_type=ConfigEventType.RAMP_TIMES_CHANGED,
    location="Flower Room",
    cluster="main",
    config_type="ramp_times",
    data={"ramp_up_minutes": 30}
)
success = await bus.publish(event)

# Consumer
async for event in bus.subscribe():
    match event.event_type:
        case ConfigEventType.RAMP_TIMES_CHANGED:
            await handle_ramp_change(event)
        case ConfigEventType.SETPOINT_CHANGED:
            await handle_setpoint_change(event)
```

### Performance Characteristics

- Queue size: 100 events max
- Publish: O(1) non-blocking
- Subscribe: O(1) per event
- Memory: ~1KB per event (dataclass + dict)

### Next Steps

1. ~~Integrate with `room_modes.py` for ramp times changes~~ (DONE)
2. ~~Integrate with `control_engine.py` for event consumption~~ (DONE - Task 2)
3. Add metrics for queue size and event throughput

## ConfigEventBus Integration in Control Engine (2026-02-23 - Task 2)

### Implementation Location
- **File**: `Infrastructure/automation-service/app/background_tasks.py`
- **Method**: `_config_event_consumer_loop()` (lines 316-360)
- **Task Variable**: `self._config_event_task`

### How It Works

1. **Background Task Pattern**: Runs as independent asyncio task alongside control loop
2. **Event Subscription**: Uses `async for event in event_bus.subscribe()` pattern
3. **Immediate Response**: On `RAMP_TIMES_CHANGED` event, fetches fresh schedules from DB and updates scheduler immediately (bypasses 60s poll interval)
4. **Non-Blocking**: Completely independent from control loop - no blocking

### Edge Case Handling

The scheduler's existing ramp calculation handles edge cases gracefully:
- If `ramp_duration < elapsed_time`, progress is clamped to 1.0
- Result: immediate jump to target value (graceful, not error)

```python
# In setpoint_manager.py RampState.get_current_value():
progress = min(elapsed_minutes / self.duration_minutes, 1.0)

# In scheduler.py light intensity calculation:
progress = min(max(elapsed / ramp_duration, 0.0), 1.0)
```

### Code Style Notes

- Avoid implicit string concatenation in multi-line f-strings
- Use explicit `+` concatenation or single-line strings for pyright strict mode

### Testing

Test by:
1. Publish `RAMP_TIMES_CHANGED` event via API
2. Verify log: "Processing config event: ramp_times_changed for {location}/{cluster}"
3. Verify log: "Scheduler updated with X schedules after config change event"
