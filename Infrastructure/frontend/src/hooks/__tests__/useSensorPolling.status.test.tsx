import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

const liveSensorFixtures: Record<string, unknown> = {}

vi.mock('../../services/api', () => ({
  apiClient: {
    getAllDevices: vi.fn(async () => []),
    getSensorDataBulk: vi.fn(async () => ({})),
    getDevicesForLocationCluster: vi.fn(async () => ({ devices: {} })),
    getAllLiveSensorData: vi.fn(async () => []),
    getLiveSensorData: vi.fn(async (location: string, cluster: string) => {
      const fixture = liveSensorFixtures[`${location}:${cluster}`]
      if (fixture === undefined) throw new Error(`Sensor request failed: ${location}:${cluster}`)
      if (fixture instanceof Error) throw fixture
      return fixture
    }),
  },
}))

import { useSensorPolling } from '../useSensorPolling'

const BASE_MS = new Date('2026-09-29T12:00:00Z').getTime()

function clearFixtures(): void {
  for (const folder of Object.keys(liveSensorFixtures)) delete liveSensorFixtures[folder]
}

describe('useSensorPolling status projection', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(BASE_MS)
    clearFixtures()
  })

  afterEach(() => {
    vi.useRealTimers()
    clearFixtures()
  })

  const mountPolling = () => renderHook(() => useSensorPolling({ interval: 5000 }))
  const settleInitialLoad = async () => {
    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
      await Promise.resolve()
    })
  }

  it('projects all-invalid, mixed, failed, and empty zones from live poll snapshots', async () => {
    liveSensorFixtures['Flower Room:front'] = {
      dry_bulb_f: { data: [{ value: null, timestamp: new Date(BASE_MS).toISOString() }] },
      rh_f: { data: [{ value: 'n/a', timestamp: new Date(BASE_MS).toISOString() }] },
    }
    liveSensorFixtures['Flower Room:back'] = {
      dry_bulb_b: { data: [{ value: 20.02, timestamp: new Date(BASE_MS).toISOString() }] },
      co2_b: { data: [{ value: null, timestamp: new Date(BASE_MS).toISOString() }] },
    }
    liveSensorFixtures['Veg Room:main'] = new Error('Sensor request failed: Veg Room')
    liveSensorFixtures['Lab:main'] = {}

    const { result } = mountPolling()
    await settleInitialLoad()

    expect(result.current.zoneStatus['Flower Room:front']).toEqual({
      quality: 'bad',
      newestObservedAtMs: null,
      ageMs: null,
      source: 'poll',
      error: null,
    })
    expect(result.current.zoneStatus['Flower Room:back']).toEqual({
      quality: 'live',
      newestObservedAtMs: BASE_MS,
      ageMs: 0,
      source: 'poll',
      error: null,
    })
    expect(result.current.zoneStatus['Veg Room:main']).toEqual({
      quality: 'missing',
      newestObservedAtMs: null,
      ageMs: null,
      source: null,
      error: 'Sensor request failed: Veg Room',
    })
    expect(result.current.zoneStatus['Lab:main']).toEqual({
      quality: 'missing',
      newestObservedAtMs: null,
      ageMs: null,
      source: null,
      error: null,
    })
  })

  it('falls back to receivedAtMs when a sample carries no timestamp and clamps a future stamp to age zero', async () => {
    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: {
        data: [{ value: 21, timestamp: new Date(BASE_MS + 5_000).toISOString() }],
      },
    }
    const { result } = mountPolling()
    await settleInitialLoad()
    // timestamp BASE+5000 observed ahead of the current clock, so age is clamped at zero.
    expect(result.current.zoneStatus['Veg Room:main'].quality).toBe('live')
    expect(result.current.zoneStatus['Veg Room:main'].ageMs).toBe(0)

    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 22 }] },
    }
    // Repoll replaces the reading; with no timestamp the receipt time is used.
    await act(async () => {
      vi.advanceTimersByTime(5_000)
      await Promise.resolve()
      await Promise.resolve()
    })
    const afterRepoll = result.current.zoneStatus['Veg Room:main']
    expect(afterRepoll.quality).toBe('live')
    expect(afterRepoll.newestObservedAtMs).toBeNull()
    expect(afterRepoll.ageMs).toBe(0)
  })

  it('moves a live zone to stale once its observed stamp crosses the 45-second window', async () => {
    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: {
        data: [{ value: 21, timestamp: new Date(BASE_MS - 44_000).toISOString() }],
      },
    }
    const { result } = mountPolling()
    await settleInitialLoad()
    expect(result.current.zoneStatus['Veg Room:main'].quality).toBe('live')
    expect(result.current.zoneStatus['Veg Room:main'].ageMs).toBe(44_000)

    await act(async () => {
      vi.advanceTimersByTime(1_000)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.zoneStatus['Veg Room:main'].quality).toBe('live')
    expect(result.current.zoneStatus['Veg Room:main'].ageMs).toBe(45_000)
    await act(async () => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current.zoneStatus['Veg Room:main'].quality).toBe('live')
    await act(async () => {
      vi.advanceTimersByTime(999)
    })
    expect(result.current.zoneStatus['Veg Room:main'].quality).toBe('stale')
    expect(result.current.zoneStatus['Veg Room:main'].ageMs).toBe(46_000)
  })

  it('recovers a failed zone reading once polling succeeds again', async () => {
    liveSensorFixtures['Veg Room:main'] = new Error('Sensor request failed: Veg Room')
    const { result } = mountPolling()
    await settleInitialLoad()
    expect(result.current.zoneStatus['Veg Room:main'].error).toBe('Sensor request failed: Veg Room')

    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 21, timestamp: new Date(BASE_MS).toISOString() }] },
    }
    await act(async () => {
      vi.advanceTimersByTime(5_000)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.zoneStatus['Veg Room:main']).toEqual({
      quality: 'live',
      newestObservedAtMs: BASE_MS,
      ageMs: 5_000,
      source: 'poll',
      error: null,
    })
  })
})
