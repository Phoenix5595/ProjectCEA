import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDashboardLiveData } from '../useDashboardLiveData'
import type { UseSensorPollingReturn } from '../useSensorPolling'
import type { UseWebSocketReturn } from '../useWebSocket'

const now = new Date('2026-09-23T16:00:00Z').getTime()
const key = 'Veg Room_main_dry_bulb'

function makePollingFixture(
  overrides: Partial<UseSensorPollingReturn> = {}
): UseSensorPollingReturn {
  return {
    devices: [],
    sensorData: { [key]: 20 },
    sensorMeta: {
      [key]: { observedAtMs: now, receivedAtMs: now, source: 'poll', invalid: false },
    },
    zoneStatus: {},
    lastPollAt: now,
    lightDisplayNames: {},
    flowerClusterWarnings: [],
    loading: false,
    refresh: vi.fn(),
    ...overrides,
  }
}

function makeWebSocketFixture(overrides: Partial<UseWebSocketReturn> = {}): UseWebSocketReturn {
  return {
    devices: [],
    sensorData: { [key]: 22 },
    sensorMeta: {
      [key]: { observedAtMs: now, receivedAtMs: now, source: 'websocket', invalid: false },
    },
    connectionState: 'open',
    ...overrides,
  }
}

let currentPolling: UseSensorPollingReturn
let currentWebSocket: UseWebSocketReturn

vi.mock('../useSensorPolling', () => ({
  useSensorPolling: vi.fn(() => currentPolling),
}))
vi.mock('../useWebSocket', () => ({ useWebSocket: vi.fn(() => currentWebSocket) }))

describe('useDashboardLiveData', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
    currentPolling = makePollingFixture()
    currentWebSocket = makeWebSocketFixture()
  })

  afterEach(() => {
    vi.useRealTimers()
    currentPolling = makePollingFixture()
    currentWebSocket = makeWebSocketFixture()
  })

  const renderLive = () => renderHook(() => useDashboardLiveData())
  const settleClockTicks = async (ms: number) => {
    await act(async () => {
      vi.advanceTimersByTime(ms)
      await Promise.resolve()
    })
  }

  it('prefers a fresh open WebSocket sample', () => {
    const { result } = renderLive()
    expect(result.current.sensorData[key]).toBe(22)
    expect(result.current.sensorMeta[key].source).toBe('websocket')
    expect(result.current.transport).toBe('websocket')
  })

  it('falls back to polling when the WebSocket sample is stale', () => {
    currentWebSocket = makeWebSocketFixture({
      sensorMeta: {
        [key]: { observedAtMs: now - 46_000, receivedAtMs: now, source: 'websocket', invalid: false },
      },
    })
    const { result } = renderLive()
    expect(result.current.sensorData[key]).toBe(20)
    expect(result.current.sensorMeta[key].source).toBe('poll')
    expect(result.current.transport).toBe('polling')
  })

  it('falls back to polling for an invalid WebSocket sample even when the connection stays open', () => {
    currentWebSocket = makeWebSocketFixture({
      sensorMeta: {
        [key]: { observedAtMs: now, receivedAtMs: now, source: 'websocket', invalid: true },
      },
    })
    const { result } = renderLive()
    expect(result.current.sensorData[key]).toBe(20)
    expect(result.current.sensorMeta[key].source).toBe('poll')
    expect(result.current.transport).toBe('polling')
  })

  it('expires a WebSocket sample on the next clock tick and publishes the poll fallback', async () => {
    currentWebSocket = makeWebSocketFixture({
      sensorMeta: {
        [key]: {
          observedAtMs: now - 1,
          receivedAtMs: now,
          source: 'websocket',
          invalid: false,
        },
      },
    })
    const { result } = renderLive()
    expect(result.current.sensorData[key]).toBe(22)

    // The hook runs a 1000 ms status clock; the observation crosses the exact
    // stale window at 45001 ms and the next 1-second tick applies the flip.
    await settleClockTicks(45_000)

    expect(result.current.sensorData[key]).toBe(20)
    expect(result.current.sensorMeta[key].source).toBe('poll')
    expect(result.current.transport).toBe('polling')
  })

  it('publishes new WebSocket metadata when only the receipt timestamps change', async () => {
    const { result, rerender } = renderLive()
    expect(result.current.sensorMeta[key].observedAtMs).toBe(now)
    const metaBeforeUpdate = result.current.sensorMeta[key]

    currentWebSocket = makeWebSocketFixture({
      sensorMeta: {
        [key]: {
          observedAtMs: now + 1000,
          receivedAtMs: now + 1000,
          source: 'websocket',
          invalid: false,
        },
      },
    })
    rerender()

    expect(result.current.sensorData[key]).toBe(22)
    expect(result.current.sensorMeta[key]).not.toBe(metaBeforeUpdate)
    expect(result.current.sensorMeta[key].observedAtMs).toBe(now + 1000)
    expect(result.current.sensorMeta[key].receivedAtMs).toBe(now + 1000)
  })

  it('projects merged zone status through the real summary helper with the polling error passthrough', () => {
    currentPolling = {
      ...makePollingFixture(),
      zoneStatus: {
        'Veg Room:main': {
          quality: 'live',
          newestObservedAtMs: now,
          ageMs: 0,
          source: 'poll',
          error: 'Sensor request failed: Veg Room',
        },
      },
    }
    const { result } = renderLive()
    // Real helper imports are not mocked; the polling error passes through to
    // the merged status projection for the zone while the fresh ws meta wins.
    expect(result.current.zoneStatus['Veg Room:main']).toEqual({
      quality: 'live',
      source: 'websocket',
      ageMs: 0,
      newestObservedAtMs: now,
      error: 'Sensor request failed: Veg Room',
    })
  })

  it('marks a retained last-good reading bad when its only metadata is invalid', () => {
    currentPolling = makePollingFixture({ sensorData: {}, sensorMeta: {} })
    currentWebSocket = makeWebSocketFixture({
      sensorMeta: {
        [key]: { observedAtMs: now, receivedAtMs: now, source: 'websocket', invalid: true },
      },
    })
    const { result } = renderLive()
    expect(result.current.sensorData[key]).toBe(22)
    expect(result.current.sensorMeta[key].invalid).toBe(true)
    expect(result.current.zoneStatus['Veg Room:main']).toEqual({
      quality: 'bad',
      source: 'websocket',
      ageMs: null,
      newestObservedAtMs: null,
      error: null,
    })
  })
})
