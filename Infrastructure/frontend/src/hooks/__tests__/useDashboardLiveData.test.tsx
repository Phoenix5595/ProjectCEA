import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useDashboardLiveData } from '../useDashboardLiveData'
import type { UseSensorPollingReturn } from '../useSensorPolling'
import type { UseWebSocketReturn } from '../useWebSocket'

const now = new Date('2026-09-23T16:00:00Z').getTime()
const key = 'Veg Room_main_dry_bulb'

const polling: UseSensorPollingReturn = {
  devices: [],
  sensorData: { [key]: 20 },
  sensorMeta: { [key]: { observedAtMs: now, receivedAtMs: now, source: 'poll', invalid: false } },
  zoneStatus: {},
  lastPollAt: now,
  lightDisplayNames: {},
  flowerClusterWarnings: [],
  loading: false,
  refresh: vi.fn(),
}

const websocket: UseWebSocketReturn = {
  devices: [],
  sensorData: { [key]: 22 },
  sensorMeta: {
    [key]: { observedAtMs: now, receivedAtMs: now, source: 'websocket', invalid: false },
  },
  connectionState: 'open',
}

vi.mock('../useSensorPolling', () => ({
  useSensorPolling: vi.fn(() => polling),
  deriveZoneSensorStatus: vi.fn(() => ({
    quality: 'live',
    newestObservedAtMs: now,
    ageMs: 0,
    source: 'poll',
    error: null,
  })),
}))
vi.mock('../useWebSocket', () => ({ useWebSocket: vi.fn(() => websocket) }))

describe('useDashboardLiveData', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(now)
  })

  it('prefers a fresh open WebSocket sample', () => {
    const { result } = renderHook(() => useDashboardLiveData())
    expect(result.current.sensorData[key]).toBe(22)
    expect(result.current.sensorMeta[key].source).toBe('websocket')
    expect(result.current.transport).toBe('websocket')
    vi.useRealTimers()
  })

  it('falls back to polling when the WebSocket sample is stale', () => {
    websocket.sensorMeta[key] = { ...websocket.sensorMeta[key], observedAtMs: now - 46_000 }
    const { result } = renderHook(() => useDashboardLiveData())
    expect(result.current.sensorData[key]).toBe(20)
    expect(result.current.sensorMeta[key].source).toBe('poll')
    expect(result.current.transport).toBe('polling')
    vi.useRealTimers()
  })
})
