# CEA FRONTEND COMPONENTS

**Generated:** 2025-01-07

## OVERVIEW
React UI library for greenhouse automation: device control, scheduling, setpoint management, and 24h timeline visualization.

## WHERE TO LOOK

| Component | Purpose | Key Patterns |
|-----------|---------|--------------|
| `SetpointTimeline.tsx` | 24h visualization | Pure functional, SVG/Div rendering, extensive prop calc |
| `DeviceManager.tsx` | Hardware config | CRUD table, dynamic zone dropdowns |
| `LightManager.tsx` | Dimming control | 1s polling, "Save All" batching, input synchronization |
| `ScheduleManager.tsx` | Scheduler UI | WebSocket sync, conflict detection, version handling |
| `SetpointEditor.tsx` | Setpoint forms | Mode-aware (DAY/NIGHT), client-side validation |
| `ZoneCard.tsx` | Dashboard widgets | Composed display of sensor data |

## CONVENTIONS

### State Management
- **Inputs**: Store numeric inputs as `string` in `useState` to prevent cursor jumps/focus loss
- **Sync**: `useEffect` loads initial data; WebSocket/Polling updates state
- **Saved/Dirty**: Track `savedValues` vs `inputValues` to show "Modified" state
- **Batching**: Use `Promise.all` for multi-device operations (e.g., "Save All Lights")

### Visualization (`SetpointTimeline`)
- **Coordinate System**: 0-1440 minutes mapped to 0-100% width
- **Rendering**: SVG for diagonal lines (ramps), Divs for periods/blocks
- **Theme**: Uses `window.matchMedia` for dark mode adjustments

### Validation
- **Client-side**: Validate ranges before API calls (Temp 10-35°C, VPD 0-5kPa)
- **Conflicts**: Check schedule overlaps in `utils/conflictDetection` before submit
- **Feedback**: Use Toast for success/error, inline red text for field errors

## ANTI-PATTERNS

- **Never**: Mutate state directly (use setters)
- **Never**: Rely solely on local state for critical data (listen to WebSocket/Poll)
- **Never**: Hardcode zone names (import from `config/zones`)
- **Never**: Block render loop with heavy calculations (memoize timeline calcs)
- **Never**: Ignore 409 Conflict errors (handle stale data in schedules)
