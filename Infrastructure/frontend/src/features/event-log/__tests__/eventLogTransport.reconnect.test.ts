import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import {
  addConnectionRef,
  removeConnectionRef,
  resetTransportState,
  getActiveConnections,
  loadOlder,
  _setReconnectDelayForTesting,
} from '../state/eventLogTransport'
import { _resetSharedStoreForTesting } from '../state/useEventLog'
import { globalEventLogStore } from '../state/eventLogStore'

const mockFetch = vi.fn()

function mockHistoryResponse() {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        items: [],
        newest_cursor: null,
        oldest_cursor: null,
        earliest_cursor: null,
        has_more: false,
        scan: { scanned: 0, limit: 200 },
      }),
  })
}

function mockFailingStreamResponse() {
  return Promise.resolve({
    ok: false,
    status: 500,
  })
}

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

function mockHistoryPage(items: readonly ReturnType<typeof historyItem>[], hasMore: boolean) {
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
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  resetTransportState()
  vi.useRealTimers()
})

describe('eventLogTransport reconnect behavior', () => {
  it('uses a jittered reconnect delay bounded by 1-30 seconds after a stream failure', async () => {
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse())
      .mockImplementationOnce(() => mockFailingStreamResponse())
      .mockImplementationOnce(() => mockFailingStreamResponse())

    addConnectionRef()

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    expect(getActiveConnections()).toBe(1)

    vi.advanceTimersByTime(501)
    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3)
    })
  })

  it('reconnects within the 1-30 second bound after a clean stream end', async () => {
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse())
      .mockImplementationOnce(() =>
        Promise.resolve({
          ok: true,
          status: 200,
          body: new ReadableStream({
            start(controller) {
              controller.close()
            },
          }),
        }),
      )
      .mockImplementationOnce(() => mockFailingStreamResponse())

    addConnectionRef()

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    vi.advanceTimersByTime(501)
    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(3)
    })
  })

  it('reconnects after 45 seconds of no data or heartbeat', async () => {
    const signals: AbortSignal[] = []
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse())
      .mockImplementationOnce((_url: unknown, init: RequestInit) => {
        if (init.signal) signals.push(init.signal)
        return Promise.resolve({
          ok: true,
          status: 200,
          body: new ReadableStream({
            async pull(controller) {
              while (!signals[0]?.aborted) {
                await new Promise((resolve) => setTimeout(resolve, 10))
              }
              controller.close()
            },
          }),
        })
      })
      .mockImplementationOnce(() => mockFailingStreamResponse())

    addConnectionRef()

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    vi.advanceTimersByTime(45_001)
    await vi.waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(3)
    })
  })

  it('merges two overlapping older pages and stops requesting history when exhausted', async () => {
    // Given: the singleton has a bounded history window with more pages
    mockFetch
      .mockImplementationOnce(() => mockHistoryPage([historyItem('5-0', 'five'), historyItem('4-0', 'four')], true))
      .mockImplementationOnce(() => mockHistoryPage([historyItem('4-0', 'four'), historyItem('3-0', 'three')], true))
      .mockImplementationOnce(() => mockHistoryPage([historyItem('3-0', 'three'), historyItem('2-0', 'two')], false))

    // When: bootstrap then two older-page actions overlap at their cursor boundaries
    await import('../state/eventLogTransport').then(({ loadHistory }) => loadHistory())
    await loadOlder()
    await loadOlder()
    const fetchesBeforeExhaustion = mockFetch.mock.calls.length
    await loadOlder()

    // Then: each request uses the current oldest cursor and the store retains unique rows only
    expect(mockFetch.mock.calls[1]?.[0]).toContain('before=4-0')
    expect(mockFetch.mock.calls[2]?.[0]).toContain('before=3-0')
    expect(mockFetch).toHaveBeenCalledTimes(fetchesBeforeExhaustion)
    expect(globalEventLogStore.snapshot().entries.map((entry) => entry.redisId)).toEqual(['2-0', '3-0', '4-0', '5-0'])
  })

  it('does not reconnect after the final consumer unmounts during an active stream', async () => {
    // Given: one owner has an open stream request
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse())
      .mockImplementationOnce((_url: unknown, init: RequestInit) => new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      }))
    addConnectionRef()
    await vi.waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))

    // When: the final owner leaves and fake time advances beyond the retry window
    removeConnectionRef()
    await vi.runAllTimersAsync()
    vi.advanceTimersByTime(30_001)

    // Then: the ownerless singleton makes no more requests
    expect(getActiveConnections()).toBe(0)
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('keeps the stream owned when one of two consumers unmounts', async () => {
    // Given: two owners share an active stream
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse())
      .mockImplementationOnce(() => new Promise(() => undefined))
    addConnectionRef()
    addConnectionRef()
    await vi.waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(2))

    // When: one owner leaves
    removeConnectionRef()

    // Then: the remaining owner retains the singleton stream
    expect(getActiveConnections()).toBe(1)
    expect(mockFetch).toHaveBeenCalledTimes(2)
  })

  it('clears stale entries then performs one latest bootstrap and one tail after a 409 reset', async () => {
    // Given: retained stale data and a stream cursor reset response
    globalEventLogStore.merge([{
      redisId: '1-0', eventId: 'stale', type: 'system.started', category: 'system', occurredAt: new Date(), payload: {},
    }])
    let staleStateWasCleared = false
    mockFetch
      .mockImplementationOnce(() => mockHistoryPage([historyItem('5-0', 'old-bootstrap')], false))
      .mockImplementationOnce(() => Promise.resolve({
        ok: false,
        status: 409,
        json: () => Promise.resolve({ earliest_cursor: '6-0', latest_cursor: '9-0' }),
      }))
      .mockImplementationOnce(() => {
        staleStateWasCleared = !globalEventLogStore.snapshot().entries.some((entry) => entry.eventId === 'stale')
        return mockHistoryPage([historyItem('9-0', 'latest')], false)
      })
      .mockImplementationOnce(() => mockFailingStreamResponse())

    // When: the shared transport receives a trimmed-cursor response
    addConnectionRef()
    await vi.waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(4))

    // Then: stale data is cleared before exactly one replacement bootstrap and tail
    expect(staleStateWasCleared).toBe(true)
    expect(mockFetch.mock.calls[3]?.[0]).toContain('after=9-0')
    expect(globalEventLogStore.snapshot().entries.map((entry) => entry.eventId)).toEqual(['latest'])
  })

  it.each([401, 403])('does not retry after an authentication failure with status %s', async (status) => {
    mockFetch.mockImplementationOnce(() => Promise.resolve({ ok: false, status }))
    addConnectionRef()
    await vi.waitFor(() => expect(mockFetch).toHaveBeenCalledTimes(1))
    vi.advanceTimersByTime(30_001)
    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})
