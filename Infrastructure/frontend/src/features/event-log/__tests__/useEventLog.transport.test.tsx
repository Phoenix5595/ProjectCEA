import { render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { _resetSharedStoreForTesting, useEventLog } from '../state/useEventLog'
import { globalEventLogStore } from '../state/eventLogStore'
import {
  resetTransportState,
  getActiveConnections,
  getAbortController,
  _setReconnectDelayForTesting,
} from '../state/eventLogTransport'

const mockFetch = vi.fn()

beforeEach(() => {
  mockFetch.mockClear()
  vi.stubGlobal('fetch', mockFetch)
  resetTransportState()
  _resetSharedStoreForTesting()
  _setReconnectDelayForTesting(500)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function mockHistoryResponse(items: Array<{ redis_id: string; event: object }> = [], hasMore = false) {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () =>
      Promise.resolve({
        items,
        newest_cursor: items[0]?.redis_id ?? null,
        oldest_cursor: items[items.length - 1]?.redis_id ?? null,
        earliest_cursor: items[items.length - 1]?.redis_id ?? null,
        has_more: hasMore,
        scan: { scanned: items.length, limit: 200 },
      }),
  })
}

function mockStreamResponse() {
  return Promise.resolve({
    ok: true,
    status: 200,
    body: new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(': connected\n\n'))
        controller.close()
      },
    }),
  })
}

function TestComponent() {
  useEventLog()
  return null
}

describe('useEventLog transport contract', () => {
  it('loads history from /api/events/history with limit=200', async () => {
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse([]))
      .mockImplementationOnce(() => mockStreamResponse())

    render(<TestComponent />)

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalled()
    })

    const [url] = mockFetch.mock.calls[0]
    expect(url).toContain('/api/events/history')
    expect(url).toContain('limit=200')
  })

  it('does not use query-token authentication', async () => {
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse([]))
      .mockImplementationOnce(() => mockStreamResponse())

    render(<TestComponent />)

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalled()
    })

    for (const call of mockFetch.mock.calls) {
      const url = call[0] as string
      expect(url).not.toMatch(/[?&]token=/)
    }
  })

  it('calls both history and stream endpoints', async () => {
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse([]))
      .mockImplementationOnce(() => mockStreamResponse())

    render(<TestComponent />)

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    const [historyUrl] = mockFetch.mock.calls[0]
    const [streamUrl] = mockFetch.mock.calls[1]
    expect(historyUrl).toContain('/api/events/history')
    expect(streamUrl).toContain('/api/events/stream')
  })

  it('merges history entries into the global store and starts after newest_cursor', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const historyItems = [
      {
        redis_id: '2-0',
        event: {
          schema_version: 1,
          event_id: 'evt-2',
          occurred_at: '2026-09-03T12:00:00Z',
          source: 'automation',
          category: 'relay',
          severity: 'info',
          event_type: 'relay.state_changed',
          correlation_id: null,
          causation_id: null,
          entity: { entity_type: 'relay', entity_id: 'relay-1', location: 'Flower Room', cluster: 'main' },
          actor: null,
          reason_code: null,
          reason_text: null,
          payload: { family: 'relay', state: true },
        },
      },
      {
        redis_id: '1-0',
        event: {
          schema_version: 1,
          event_id: 'evt-1',
          occurred_at: '2026-09-03T11:59:00Z',
          source: 'automation',
          category: 'system',
          severity: 'info',
          event_type: 'system.started',
          correlation_id: null,
          causation_id: null,
          entity: null,
          actor: null,
          reason_code: null,
          reason_text: null,
          payload: { family: 'system', component: 'automation', state: 'running' },
        },
      },
    ]

    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse(historyItems))
      .mockImplementationOnce(() => mockStreamResponse())

    render(<TestComponent />)

    await vi.waitFor(() => {
      expect(mockFetch).toHaveBeenCalledTimes(2)
    })

    const snapshot = globalEventLogStore.snapshot()
    expect(snapshot.entries).toHaveLength(2)
    expect(snapshot.entries[0].redisId).toBe('1-0')
    expect(snapshot.entries[1].redisId).toBe('2-0')

    const calls = mockFetch.mock.calls.filter((call) => {
      const url = call[0] as string
      return url.includes('/api/events/stream')
    })
    expect(calls.length).toBeGreaterThanOrEqual(1)
    expect(calls[0][0]).toContain('after=2-0')
    vi.useRealTimers()
  })

  it('reconnects after a clean stream close', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse([]))
      .mockImplementationOnce(() => mockStreamResponse())
      .mockImplementationOnce(() => mockStreamResponse())

    render(<TestComponent></TestComponent>)

    await vi.waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(2)
    })

    vi.advanceTimersByTime(501)
    await vi.waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThanOrEqual(3)
    })

    const eventApiCalls = mockFetch.mock.calls.filter((call) => {
      const url = call[0] as string
      return url.includes('/api/events/history') || url.includes('/api/events/stream')
    })
    expect(eventApiCalls.length).toBeGreaterThanOrEqual(3)

    vi.useRealTimers()
  })

  it('does not abort the shared transport when a second consumer mounts then unmounts', async () => {
    mockFetch
      .mockImplementationOnce(() => mockHistoryResponse([]))
      .mockImplementationOnce(() => mockStreamResponse())

    function SecondConsumer() {
      useEventLog()
      return null
    }

    const { unmount: unmountSecond } = render(<SecondConsumer />)

    await vi.waitFor(() => {
      expect(getActiveConnections()).toBe(1)
    })

    unmountSecond()

    await vi.waitFor(() => {
      expect(getActiveConnections()).toBe(0)
    })

    expect(getAbortController()).toBeNull()
  })
})
