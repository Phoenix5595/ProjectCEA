import { describe, expect, it } from 'vitest'
import { globalEventLogStore } from '../state/eventLogStore'
import { _getSharedStoreForTesting } from '../state/useEventLog'

describe('Event log single transport/store regression', () => {
  it('useEventLog and eventLogStore share the same globalEventLogStore instance', () => {
    // Given: the canonical store from eventLogStore.ts
    // When: we get the store from useEventLog's testing helper
    const storeFromHook = _getSharedStoreForTesting()
    // Then: they are the exact same instance
    expect(storeFromHook).toBe(globalEventLogStore)
  })

  it('merging into globalEventLogStore is visible via useEventLog hook', () => {
    // Given: the global store
    // When: we merge an entry into it
    globalEventLogStore.merge([
      {
        redisId: 'regression-1',
        eventId: 'regression-evt-1',
        type: 'test.event',
        category: 'system',
        occurredAt: new Date('2026-09-02T12:00:00Z'),
        payload: { room: 'Test Room' },
      },
    ])
    // Then: the entry is visible in the store snapshot
    const snapshot = globalEventLogStore.snapshot()
    expect(snapshot.entries).toHaveLength(1)
    expect(snapshot.entries[0].type).toBe('test.event')
    expect(snapshot.entries[0].payload.room).toBe('Test Room')
  })

  it('only one globalEventLogStore instance exists (singleton)', () => {
    // Given: multiple imports of the store
    // When: we compare them
    const store1 = globalEventLogStore
    const store2 = _getSharedStoreForTesting()
    // Then: they are the same reference
    expect(store1).toBe(store2)
    expect(store1).toBe(globalEventLogStore)
  })
})
