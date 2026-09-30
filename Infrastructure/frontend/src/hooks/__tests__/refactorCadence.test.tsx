import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useDashboardLiveData } from '../useDashboardLiveData'

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

  it('polls at 0/1/2 seconds even while a fresh socket sample is selected', async () => {
    const { result } = renderHook(() => useDashboardLiveData())
    await advance()
    act(() => emitSensor(22, BASE_MS))
    await advance(1_000)
    await advance(1_000)
    expect(fixture.pollTimes).toEqual([BASE_MS, BASE_MS + 1_000, BASE_MS + 2_000])
    expect(result.current.sensorData[KEY]).toBe(22)
    expect(result.current.sensorMeta[KEY].source).toBe('websocket')
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
    expect(result.current.zoneStatus['Veg Room:main'].quality).toBe('live')
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
