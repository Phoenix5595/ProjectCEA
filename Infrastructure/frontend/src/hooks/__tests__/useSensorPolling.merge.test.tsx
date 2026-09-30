import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

const liveSensorFixtures: Record<string, unknown> = {}

vi.mock('../../services/api', () => ({
  apiClient: {
    getAllDevices: vi.fn(async () => []),
    getSensorDataBulk: vi.fn(async (bulkKeys: string[]) =>
      Object.fromEntries(bulkKeys.map(key => [key, 42]))
    ),
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

describe('useSensorPolling merge behavior', () => {
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
  const advanceOnePoll = async () => {
    await act(async () => {
      vi.advanceTimersByTime(5_000)
      await Promise.resolve()
      await Promise.resolve()
    })
  }

  it('keeps previously received samples from other zones after a failed zone refresh', async () => {
    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 21, timestamp: new Date(BASE_MS).toISOString() }] },
    }
    const { result } = mountPolling()
    await settleInitialLoad()
    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(21)
    const metaBeforeFailure = result.current.sensorMeta['Veg Room_main_dry_bulb']

    liveSensorFixtures['Veg Room:main'] = new Error('Sensor request failed: Veg Room')
    await advanceOnePoll()

    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(21)
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb']).toBe(metaBeforeFailure)
    expect(result.current.zoneStatus['Veg Room:main'].error).toBe(
      'Sensor request failed: Veg Room'
    )
  })

  it('replaces a retained reading with fresher data once the zone recovers', async () => {
    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 21, timestamp: new Date(BASE_MS).toISOString() }] },
    }
    const { result } = mountPolling()
    await settleInitialLoad()

    liveSensorFixtures['Veg Room:main'] = new Error('Sensor request failed: Veg Room')
    await advanceOnePoll()
    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(21)

    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 23, timestamp: new Date(BASE_MS + 5_000).toISOString() }] },
    }
    await advanceOnePoll()
    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(23)
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb']).toMatchObject({
      observedAtMs: BASE_MS + 5_000,
      source: 'poll',
      invalid: false,
    })
    expect(result.current.zoneStatus['Veg Room:main'].error).toBeNull()
  })

  it('removes only live keys of a successful zone refresh whose data is now empty', async () => {
    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 21, timestamp: new Date(BASE_MS).toISOString() }] },
    }
    const { result } = mountPolling()
    await settleInitialLoad()
    // The bulk-poll merge introduces setpoints that are not live keys.
    expect(result.current.sensorData['Veg Room_main_heating_setpoint']).toBe(42)

    liveSensorFixtures['Veg Room:main'] = {}
    await advanceOnePoll()

    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBeUndefined()
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb']).toBeUndefined()
    expect(result.current.sensorData['Veg Room_main_heating_setpoint']).toBe(42)
    expect(result.current.sensorMeta['Veg Room_main_heating_setpoint']).toBeUndefined()
  })

  it('keeps the bulk setpoint and its merge ordering after a repoll of the same zone', async () => {
    liveSensorFixtures['Veg Room:main'] = {}
    const { result } = mountPolling()
    await settleInitialLoad()

    liveSensorFixtures['Veg Room:main'] = {
      dry_bulb: { data: [{ value: 20, timestamp: new Date(BASE_MS + 5_000).toISOString() }] },
    }
    await advanceOnePoll()
    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(20)
    expect(result.current.sensorData['Veg Room_main_heating_setpoint']).toBe(42)
  })
})
