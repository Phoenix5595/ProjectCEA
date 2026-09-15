import { describe, expect, it } from 'vitest'
import { toEventLogEntry, type OperationalEvent } from '../state/eventLogTypes'

const severities = ['info', 'warning', 'error', 'critical'] as const

function makeEvent(severity: OperationalEvent['severity']): OperationalEvent {
  return {
    schema_version: 1,
    event_id: 'event-1',
    occurred_at: '2026-09-03T12:00:00.000Z',
    source: 'automation',
    category: 'relay',
    severity,
    event_type: 'relay.command_failed',
    correlation_id: null,
    causation_id: null,
    entity: {
      entity_type: 'relay',
      entity_id: 'relay-1',
      location: 'Flower Room',
      cluster: 'main',
    },
    actor: null,
    reason_code: 'hardware_error',
    reason_text: 'Relay command failed',
    payload: { device_id: 'relay-1' },
  }
}

describe('toEventLogEntry', () => {
  it('maps the operational envelope and enriches its payload', () => {
    // Given: an operational event with entity context and payload data
    const event = makeEvent('info')

    // When: the event crosses into the event-log store shape
    const entry = toEventLogEntry('123-0', event)

    // Then: identity, timestamp, and entity context are preserved
    expect(entry).toEqual({
      redisId: '123-0',
      eventId: 'event-1',
      type: 'relay.command_failed',
      category: 'relay',
      severity: 'info',
      occurredAt: new Date('2026-09-03T12:00:00.000Z'),
      payload: { device_id: 'relay-1', room: 'Flower Room', cluster: 'main' },
    })
  })

  it.each(severities)('preserves authoritative %s severity unchanged', (severity) => {
    // Given: a relay failure envelope whose severity is authoritative
    const event = makeEvent(severity)

    // When: the envelope is converted to an event-log entry
    const entry = toEventLogEntry('123-0', event)

    // Then: the mapped entry retains the envelope severity exactly
    expect(entry).toHaveProperty('severity', severity)
  })
})
