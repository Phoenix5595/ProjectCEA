import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDashboardLiveData } from '../useDashboardLiveData'
import { useSensorPolling } from '../useSensorPolling'

const fixture = vi.hoisted(() => ({
  pollTimes: [] as number[],
  subscribers: new Set<(message: unknown) => void>(),
}))

vi.mock('../../services/api', () => ({
  apiClient: {
    getAllDevices: vi.fn(async () => []),
    getSensorDataBulk: vi.fn(async () => ({})),
    getDevicesForLocationCluster: vi.fn(async () => ({ devices: {} })),
    getAllLiveSensorData: vi.fn(async () => [
      { sensor: 'dry_bulb_f' },
      { sensor: 'dry_bulb_b' },
    ]),
    getLiveSensorData: vi.fn(async (location: string) => {
      if (location !== 'Veg Room') return {}
      fixture.pollTimes.push(Date.now())
      return { dry_bulb: { data: [{ value: 21, timestamp: new Date().toISOString() }] } }
    }),
  },
}))

vi.mock('../../services/websocket', () => ({
  wsClient: {
    connectionState: 'open',
    acquire: vi.fn(),
    release: vi.fn(),
    subscribeConnectionState: () => () => {},
    on: (type: string, handler: (message: unknown) => void) => {
      if (type !== 'sensor_update') return () => {}
      fixture.subscribers.add(handler)
      return () => fixture.subscribers.delete(handler)
    },
  },
}))

const BASE_MS = new Date('2026-09-23T16:00:00Z').getTime()
const KEY = 'Veg Room_main_dry_bulb'

function emitSensor(value: number, observedAtMs: number): void {
  for (const handler of fixture.subscribers) {
    handler({
      location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb', value,
      time: new Date(observedAtMs).toISOString(),
    })
  }
}

async function advance(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}

describe('dashboard refresh cadence', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(BASE_MS)
    fixture.pollTimes.length = 0
    fixture.subscribers.clear()
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    fixture.subscribers.clear()
  })

  it('polls through REST at 0/1/2 seconds when the socket has no samples', async () => {
    const { result } = renderHook(() => useDashboardLiveData())
    await advance()
    await advance(1_000)
    await advance(1_000)
    expect(fixture.pollTimes).toEqual([BASE_MS, BASE_MS + 1_000, BASE_MS + 2_000])
    expect(result.current.sensorMeta[KEY].source).toBe('poll')
  })

  it('stops REST polling while an open socket emits a sample every 0.5 seconds', async () => {
    const { result } = renderHook(() => useDashboardLiveData())
    await advance()
    act(() => emitSensor(22, BASE_MS))
    for (let sample = 1; sample <= 6; sample++) {
      await advance(500)
      act(() => emitSensor(22 + sample, Date.now()))
    }
    await advance(2_000)
    expect(fixture.pollTimes).toEqual([BASE_MS])
    expect(result.current.sensorMeta[KEY].source).toBe('websocket')
  })

  it('resumes REST polling within six seconds after socket messages stop', async () => {
    renderHook(() => useDashboardLiveData())
    await advance()
    act(() => emitSensor(22, BASE_MS))
    await advance(5_999)
    expect(fixture.pollTimes).toEqual([BASE_MS])
    await advance(1)
    expect(fixture.pollTimes).toEqual([BASE_MS, BASE_MS + 6_000])
  })

  it('refreshes immediately when enabled changes from false to true', async () => {
    const { rerender } = renderHook(
      ({ enabled }) => useSensorPolling({ interval: 1000, enabled }),
      { initialProps: { enabled: false } }
    )
    await advance()
    expect(fixture.pollTimes).toEqual([])
    rerender({ enabled: true })
    await advance()
    expect(fixture.pollTimes).toEqual([BASE_MS])
  })

  it('applies between-tick expiry on the next existing one-second tick', async () => {
    const { result } = renderHook(() => useDashboardLiveData())
    await advance()
    act(() => emitSensor(22, BASE_MS))
    await advance(45_000)
    expect(result.current.sensorData[KEY]).toBe(22)
    await advance(1)
    expect(result.current.sensorMeta[KEY].source).toBe('websocket')
    await advance(999)
    expect(result.current.sensorData[KEY]).toBe(21)
    expect(result.current.sensorMeta[KEY].source).toBe('poll')
  })

  it('stops polling and removes live socket recipients on unmount', async () => {
    const { unmount } = renderHook(() => useDashboardLiveData())
    await advance(1_000)
    const requests = [...fixture.pollTimes]
    unmount()
    await advance(120_000)
    expect(fixture.pollTimes).toEqual(requests)
    expect(fixture.subscribers.size).toBe(0)
  })
})
