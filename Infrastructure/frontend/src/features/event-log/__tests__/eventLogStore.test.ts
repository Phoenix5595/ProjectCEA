import { describe, expect, it } from 'vitest'
import { EventLogStore, type EventLogEntry } from '../state/eventLogStore'

const entry = (redisId: string, eventId: string): EventLogEntry => ({
  redisId,
  eventId,
  type: 'relay.state_changed',
  category: 'relay',
  occurredAt: new Date(),
  payload: {},
})

describe('EventLogStore', () => {
  it('deduplicates UUID and Redis ID then orders and caps entries', () => {
    // Given: replayed entries and a bounded store
    const store = new EventLogStore(2)
    // When: bootstrap and live entries overlap
    store.merge([entry('2-0', 'b'), entry('1-0', 'a')]); store.merge([entry('2-0', 'b'), entry('3-0', 'c')])
    // Then: the newest two unique Redis entries remain ordered
    expect(store.snapshot().entries.map((item) => item.redisId)).toEqual(['2-0', '3-0'])
  })

  it('keeps the first row when either the event UUID or Redis ID conflicts', () => {
    const store = new EventLogStore()
    const first = entry('1-0', 'event-a')
    store.merge([first])
    store.merge([entry('2-0', 'event-a'), entry('1-0', 'event-b')])
    expect(store.snapshot().entries).toEqual([first])
  })

  it('keeps at most 5,000 entries in the production-sized store', () => {
    const store = new EventLogStore()
    store.merge(Array.from({ length: 5_001 }, (_, index) => entry(`${index + 1}-0`, `event-${index + 1}`)))
    expect(store.snapshot().entries).toHaveLength(5_000)
    expect(store.snapshot().entries[0]?.redisId).toBe('2-0')
  })
})
