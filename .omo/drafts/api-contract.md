# Unified Event Schema and API Contract

## Overview

This document defines the unified **TimelineEvent** schema that consolidates control history events and configuration change events into a single, consistent API. The design follows existing patterns from the codebase while providing a clean, extensible foundation for future event types.

## Design Rationale

The existing system has two distinct event sources:

1. **Control History** (`/api/control/history`): Tracks device state changes (on/off events with reasons)
2. **Config Events** (`ConfigEventType`): Tracks configuration changes (setpoints, schedules, PID params, modes)

These serve related purposes in the dashboard timeline but use different data structures. This contract unifies them into a single **TimelineEvent** schema with a consistent API endpoint.

---

## Event Type Enumerations

### TimelineEventType (Unified)

```typescript
// TypeScript - Infrastructure/frontend/src/types/events.ts
export enum TimelineEventType {
  // Control events (device state changes)
  DEVICE_ON = "device_on",
  DEVICE_OFF = "device_off",
  DEVICE_STATE_CHANGE = "device_state_change",
  
  // Configuration change events
  SETPOINT_CHANGED = "setpoint_changed",
  PID_PARAMS_CHANGED = "pid_params_changed",
  SCHEDULE_CHANGED = "schedule_changed",
  RAMP_TIMES_CHANGED = "ramp_times_changed",
  MODE_CHANGED = "mode_changed",
  
  // System events
  ALARM_TRIGGERED = "alarm_triggered",
  ALARM_CLEARED = "alarm_cleared",
  SYSTEM_STARTUP = "system_startup",
  SYSTEM_SHUTDOWN = "system_shutdown",
}
```

```python
# Pydantic - Infrastructure/automation-service/app/models/events.py
from enum import Enum
from typing import Literal


class TimelineEventType(str, Enum):
    """Unified event types for timeline API.
    
    Consolidates control_history events and config_change events
    into a single enumeration for consistent API responses.
    """
    
    # Device control events
    DEVICE_ON = "device_on"
    DEVICE_OFF = "device_off"
    DEVICE_STATE_CHANGE = "device_state_change"
    
    # Configuration change events
    SETPOINT_CHANGED = "setpoint_changed"
    PID_PARAMS_CHANGED = "pid_params_changed"
    SCHEDULE_CHANGED = "schedule_changed"
    RAMP_TIMES_CHANGED = "ramp_times_changed"
    MODE_CHANGED = "mode_changed"
    
    # System events
    ALARM_TRIGGERED = "alarm_triggered"
    ALARM_CLEARED = "alarm_cleared"
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"
```

---

## Unified TimelineEvent Schema

### TypeScript Interface

```typescript
// Infrastructure/frontend/src/types/events.ts

/** Unified timeline event for dashboard display. */
export interface TimelineEvent {
  /** Unique event identifier. */
  id: string;
  
  /** Event timestamp (ISO 8601 format). */
  timestamp: string;
  
  /** Event category for filtering. */
  event_type: TimelineEventType;
  
  /** Human-readable event title. */
  title: string;
  
  /** Detailed event description. */
  description: string;
  
  /** Location/room identifier (e.g., "Flower Room"). */
  location: string;
  
  /** Cluster identifier (e.g., "main"). */
  cluster: string;
  
  /** Device name (for control events). */
  device_name?: string;
  
  /** Previous state (for state change events). */
  old_value?: string | number | boolean | null;
  
  /** New state (for state change events). */
  new_value?: string | number | boolean | null;
  
  /** Operational mode (for control events). */
  mode?: string;
  
  /** Reason/cause of the event. */
  reason?: string | null;
  
  /** Load percentage (for dimmer devices). */
  load_percent?: number | null;
  
  /** Configuration type (for config change events). */
  config_type?: string;
  
  /** Additional metadata as key-value pairs. */
  metadata?: Record<string, unknown>;
}

/** Filter options for timeline query. */
export interface TimelineQueryParams {
  location: string;
  cluster: string;
  limit?: number;  // Default: 10, Max: 100
  event_types?: TimelineEventType[];  // Optional filter
  start_time?: string;  // ISO 8601
  end_time?: string;    // ISO 8601
}

/** API response wrapper. */
export interface TimelineResponse {
  events: TimelineEvent[];
  total: number;
  limit: number;
  offset: number;
}
```

### Pydantic Model

```python
# Infrastructure/automation-service/app/models/events.py
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class TimelineEventType(str, Enum):
    """Unified event types for timeline API.
    
    Consolidates control_history events and config_change events
    into a single enumeration for consistent API responses.
    """
    
    # Device control events
    DEVICE_ON = "device_on"
    DEVICE_OFF = "device_off"
    DEVICE_STATE_CHANGE = "device_state_change"
    
    # Configuration change events
    SETPOINT_CHANGED = "setpoint_changed"
    PID_PARAMS_CHANGED = "pid_params_changed"
    SCHEDULE_CHANGED = "schedule_changed"
    RAMP_TIMES_CHANGED = "ramp_times_changed"
    MODE_CHANGED = "mode_changed"
    
    # System events
    ALARM_TRIGGERED = "alarm_triggered"
    ALARM_CLEARED = "alarm_cleared"
    SYSTEM_STARTUP = "system_startup"
    SYSTEM_SHUTDOWN = "system_shutdown"


class TimelineEvent(BaseModel):
    """Unified timeline event for dashboard display.
    
    This model consolidates control history entries and configuration
    change events into a single schema for the timeline API.
    
    Attributes:
        id: Unique event identifier (UUID)
        timestamp: Event timestamp in ISO 8601 format
        event_type: Category of event for filtering/display
        title: Human-readable event title
        description: Detailed event description
        location: Room/location identifier
        cluster: Cluster identifier (e.g., "main")
        device_name: Device name for control events
        old_value: Previous state value
        new_value: New state value
        mode: Operational mode at time of event
        reason: Reason/cause of the event
        load_percent: Load percentage for dimming devices
        config_type: Configuration category for config events
        metadata: Additional key-value metadata
    """
    
    id: str = Field(..., description="Unique event identifier (UUID)")
    timestamp: str = Field(..., description="Event timestamp (ISO 8601)")
    event_type: TimelineEventType = Field(..., description="Event category")
    title: str = Field(..., description="Human-readable event title")
    description: str = Field(..., description="Detailed event description")
    location: str = Field(..., description="Room/location identifier")
    cluster: str = Field(..., description="Cluster identifier")
    
    # Device-specific fields (for control events)
    device_name: str | None = Field(None, description="Device name")
    old_value: Any | None = Field(None, description="Previous state value")
    new_value: Any | None = Field(None, description="New state value")
    mode: str | None = Field(None, description="Operational mode")
    reason: str | None = Field(None, description="Reason/cause of event")
    load_percent: float | None = Field(None, description="Load percentage")
    
    # Config-specific fields (for config change events)
    config_type: str | None = Field(None, description="Configuration type")
    
    # Extended metadata
    metadata: dict[str, Any] | None = Field(None, description="Additional metadata")
    
    class Config:
        use_enum_values = True


class TimelineQueryParams(BaseModel):
    """Query parameters for timeline endpoint."""
    
    location: str = Field(..., description="Location/room identifier")
    cluster: str = Field(..., description="Cluster identifier")
    limit: int = Field(10, ge=1, le=100, description="Max events to return")
    event_types: list[TimelineEventType] | None = Field(
        None, description="Filter by event types"
    )
    start_time: datetime | None = Field(None, description="Start of time range")
    end_time: datetime | None = Field(None, description="End of time range")


class TimelineResponse(BaseModel):
    """API response wrapper for timeline endpoint."""
    
    events: list[TimelineEvent] = Field(..., description="List of events")
    total: int = Field(..., description="Total matching events")
    limit: int = Field(..., description="Limit applied")
    offset: int = Field(0, description="Offset applied")
```

---

## API Endpoint Specification

### GET /api/events/timeline/{location}/{cluster}

Returns a unified timeline of events for a specific location and cluster.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `location` | string | Yes | Room identifier (e.g., "Flower Room") |
| `cluster` | string | Yes | Cluster identifier (e.g., "main") |

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `limit` | integer | No | 10 | Max events to return (1-100) |
| `event_types` | string | No | — | Comma-separated event types to filter |
| `start_time` | ISO 8601 | No | — | Start of time range |
| `end_time` | ISO 8601 | No | — | End of time range |

#### Example Request

```
GET /api/events/timeline/Flower%20Room/main?limit=10&event_types=device_on,device_off,setpoint_changed
```

#### Example Response

```json
{
  "events": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2026-03-07T10:30:00Z",
      "event_type": "device_on",
      "title": "Heater Turned On",
      "description": "Heater activated due to temperature below setpoint",
      "location": "Flower Room",
      "cluster": "main",
      "device_name": "heater_1",
      "old_value": 0,
      "new_value": 1,
      "mode": "DAY",
      "reason": "PID output 75%, temp 21.5C below setpoint 23C",
      "load_percent": 75,
      "metadata": null
    },
    {
      "id": "550e8400-e29b-41d4-a716-446655440001",
      "timestamp": "2026-03-07T10:15:00Z",
      "event_type": "setpoint_changed",
      "title": "Temperature Setpoint Updated",
      "description": "Day temperature setpoint changed from 23.0°C to 24.0°C",
      "location": "Flower Room",
      "cluster": "main",
      "device_name": null,
      "old_value": 23.0,
      "new_value": 24.0,
      "mode": "DAY",
      "reason": "Manual adjustment",
      "load_percent": null,
      "config_type": "temperature",
      "metadata": {
        "parameter": "temp_setpoint",
        "unit": "°C"
      }
    }
  ],
  "total": 2,
  "limit": 10,
  "offset": 0
}
```

#### Error Responses

| Status | Description |
|--------|-------------|
| 400 | Invalid location, cluster, or query parameters |
| 404 | Location/cluster not found in configuration |

---

## Data Transformation

### From Control History

The existing `control_history` entries map to TimelineEvent as follows:

| Control History Field | TimelineEvent Field |
|----------------------|---------------------|
| `timestamp` | `timestamp` |
| `location` | `location` |
| `cluster` | `cluster` |
| `device_name` | `device_name` |
| `old_state` | `old_value` |
| `new_state` | `new_value` |
| `mode` | `mode` |
| `reason` | `reason` |
| `load_percent` | `load_percent` |

**Event type derivation:**
- `old_state = 0, new_state = 1` → `DEVICE_ON`
- `old_state = 1, new_state = 0` → `DEVICE_OFF`
- Otherwise → `DEVICE_STATE_CHANGE`

### From Config Change Events

The existing `ConfigChangeEvent` from `app/events/__init__.py` maps to TimelineEvent as follows:

| ConfigChangeEvent Field | TimelineEvent Field |
|------------------------|---------------------|
| `timestamp` | `timestamp` |
| `event_type` | Maps to `TimelineEventType` |
| `location` | `location` |
| `cluster` | `cluster` |
| `config_type` | `config_type` |
| `data` | `metadata` |

---

## Frontend Integration

### API Client Method

```typescript
// Infrastructure/frontend/src/services/api.ts

import type { TimelineEvent, TimelineEventType } from '../types/events';

async getTimelineEvents(
  location: string,
  cluster: string,
  options?: {
    limit?: number;
    eventTypes?: TimelineEventType[];
    startTime?: string;
    endTime?: string;
  }
): Promise<TimelineEvent[]> {
  const params = new URLSearchParams({
    location,
    cluster,
    ...(options?.limit && { limit: String(options.limit) }),
    ...(options?.eventTypes?.length && { 
      event_types: options.eventTypes.join(',') 
    }),
    ...(options?.startTime && { start_time: options.startTime }),
    ...(options?.endTime && { end_time: options.endTime }),
  });
  
  const response = await this.automationClient.get(
    `/api/events/timeline/${encodeURIComponent(location)}/${cluster}?${params}`
  );
  return response.data.events ?? [];
}
```

---

## Implementation Notes

### Query Strategy

The timeline endpoint should aggregate from multiple sources:

1. **control_history table**: Device on/off events
2. **effective_setpoints table**: Setpoint change events (tracked via trigger)
3. **automation_state table**: Mode change events
4. **In-memory/Redis**: Recent config change events (for real-time display)

### Performance Considerations

- Use database indexes on `(location, cluster, timestamp)`
- Apply time range filters at the database level
- Limit result set before transformation
- Consider caching recent events in Redis

### Backward Compatibility

- The existing `/api/control/history` endpoint should remain functional
- The new `/api/events/timeline` endpoint provides enhanced functionality
- Frontend can migrate gradually to the new endpoint

---

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `Infrastructure/automation-service/app/models/events.py` | Create | Pydantic models for TimelineEvent |
| `Infrastructure/automation-service/app/routes/events.py` | Create | Timeline API endpoint |
| `Infrastructure/frontend/src/types/events.ts` | Create | TypeScript interfaces |
| `Infrastructure/frontend/src/services/api.ts` | Modify | Add getTimelineEvents method |
| `.sisyphus/drafts/api-contract.md` | Create | This document |

---

## References

- Existing control_history endpoint: `Infrastructure/automation-service/app/routes/devices.py:250-281`
- ConfigEventType enum: `Infrastructure/automation-service/app/events/__init__.py:42-50`
- Frontend API pattern: `Infrastructure/frontend/src/services/api.ts:337-347`
- Control history repository: `Infrastructure/automation-service/app/repositories/control_actions.py:61-98`
