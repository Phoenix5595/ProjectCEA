import type { EventLogEntry } from '../state/eventLogStore'

/**
 * Shared deterministic event-log entry factory for component tests. One
 * canonical envelope: future contract changes touch this file only.
 */
export function makeEventEntry(
  redisId: string,
  type: string,
  category: string,
  payload: Record<string, unknown> = {},
  severity: EventLogEntry['severity'] = 'info',
): EventLogEntry {
  return {
    redisId,
    eventId: `evt-${redisId}`,
    type,
    category,
    severity,
    occurredAt: new Date('2026-09-02T12:00:00Z'),
    payload,
    entity: null,
    reasonText: null,
  }
}

export function makeEventEntryWith(overrides: Partial<EventLogEntry> = {}): EventLogEntry {
  return { ...makeEventEntry('1-0', 'relay.state_changed', 'relay', { device_id: 'heater-1', state: 'on' }), ...overrides }
}
