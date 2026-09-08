import type { EventLogEntry } from './eventLogStore';

interface OperationalEventEntity {
  entity_type: string;
  entity_id: string;
  location: string | null;
  cluster: string | null;
}

interface OperationalEvent {
  schema_version: number;
  event_id: string;
  occurred_at: string;
  source: string;
  category: string;
  severity: string;
  event_type: string;
  correlation_id: string | null;
  causation_id: string | null;
  entity: OperationalEventEntity | null;
  actor: { actor_type: string; actor_id: string | null } | null;
  reason_code: string | null;
  reason_text: string | null;
  payload: Record<string, unknown>;
}

interface OperationalEventItem {
  redis_id: string;
  event: OperationalEvent;
}

interface OperationalEventHistory {
  items: readonly OperationalEventItem[];
  newest_cursor: string | null;
  oldest_cursor: string | null;
  earliest_cursor: string | null;
  has_more: boolean;
}

interface OperationalEventCursorReset {
  earliest_cursor: string;
  latest_cursor: string;
}

function toEventLogEntry(redisId: string, event: OperationalEvent): EventLogEntry {
  const payload: Record<string, unknown> = { ...event.payload };
  if (event.entity) {
    if (event.entity.location !== null) payload.room = event.entity.location;
    if (event.entity.cluster !== null) payload.cluster = event.entity.cluster;
  }
  return {
    redisId,
    eventId: event.event_id,
    type: event.event_type,
    category: event.category,
    occurredAt: new Date(event.occurred_at),
    payload,
  };
}

export type {
  OperationalEvent,
  OperationalEventItem,
  OperationalEventHistory,
  OperationalEventCursorReset,
};
export { toEventLogEntry };
