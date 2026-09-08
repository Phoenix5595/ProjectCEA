import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { globalEventLogStore } from '../state/eventLogStore'
import {
  _setReconnectDelayForTesting,
  addConnectionRef,
  getLastCursor,
  loadHistory,
  loadOlder,
  removeConnectionRef,
  resetTransportState,
} from '../state/eventLogTransport'
import { _resetSharedStoreForTesting } from '../state/useEventLog'

const mockFetch = vi.fn()

function historyItem(redisId: string, eventId: string) {
  return {
    redis_id: redisId,
    event: {
      schema_version: 1,
      event_id: eventId,
      occurred_at: '2026-09-07T00:00:00Z',
      source: 'automation',
      category: 'relay',
      severity: 'info',
      event_type: 'relay.state_changed',
      correlation_id: null,
      causation_id: null,
      entity: null,
      actor: null,
      reason_code: null,
      reason_text: null,
      payload: {},
    },
  }
}

function historyResponse(items: readonly ReturnType<typeof historyItem>[], hasMore = false) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({
      items,
      newest_cursor: items[0]?.redis_id ?? null,
      oldest_cursor: items.at(-1)?.redis_id ?? null,
      earliest_cursor: items.at(-1)?.redis_id ?? null,
      has_more: hasMore,
      scan: { scanned: items.length, limit: 200 },
    }),
  })
}

beforeEach(() => {
  mockFetch.mockClear()
  vi.stubGlobal('fetch', mockFetch)
  resetTransportState()
  _resetSharedStoreForTesting()
  _setReconnectDelayForTesting(500)
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  removeConnectionRef()
  resetTransportState()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('event log transport lifecycle', () => {
  it('keeps an idle stream open when the server sends heartbeat comments', async () => {
    // Given: an owner with a stream that sends the server's periodic heartbeat comment
    const signals: AbortSignal[] = []
    mockFetch
      .mockImplementationOnce(() => historyResponse([]))
      .mockImplementationOnce((_url: unknown, init: RequestInit) => {
        if (init.signal) signals.push(init.signal)
        return Promise.resolve({
          ok: true,
          status: 200,
          body: new ReadableStream({
            start(controller) {
              const heartbeatTimer = setInterval(() => controller.enqueue(new TextEncoder().encode(': heartbeat\n\n')), 30_000)
              init.signal?.addEventListener('abort', () => {
                clearInterval(heartbeatTimer)
                controller.close()
              })
            },
          }),
        })
      })

    // When: the original stale deadline passes after a heartbeat arrives
    addConnectionRef()
    await vi.waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))
    await vi.advanceTimersByTimeAsync(30_000)
    await vi.advanceTimersByTimeAsync(15_001)

    // Then: heartbeat activity has postponed the watchdog rather than reconnecting
    expect(signals[0]?.aborted).toBe(false)
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('replaces stale rows when an older history cursor is trimmed', async () => {
    // Given: a page cursor that Redis has trimmed between history requests
    mockFetch
      .mockImplementationOnce(() => historyResponse([historyItem('5-0', 'five'), historyItem('4-0', 'four')], true))
      .mockImplementationOnce(() => Promise.resolve({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ earliest_cursor: '8-0', latest_cursor: '10-0' }),
      }))
      .mockImplementationOnce(() => historyResponse([historyItem('10-0', 'ten')]))
    await loadHistory()

    // When: the user requests an older page with the trimmed cursor
    await loadOlder()

    // Then: the singleton atomically replaces stale rows with a fresh history window
    expect(globalEventLogStore.snapshot().entries.map((entry) => entry.redisId)).toEqual(['10-0'])
    expect(getLastCursor()).toBe('10-0')
    expect(globalEventLogStore.snapshot().paging.hasMore).toBe(false)
  })
})
