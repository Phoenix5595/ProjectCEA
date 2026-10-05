import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DeviceRegistryEntry } from '../../../../types/device'
import { logger } from '../../../../utils/logger'
import type {
  ControlMonitoringResponse,
  MonitoringApi,
  MonitoringResponse,
  ProjectionPublicationResponse,
} from '../../api'
import { createMonitoringPanelChartFeed } from '../../charts/MonitoringChartFeed'
import { flowerManifest } from '../../config'
import type { TimeseriesPanelSpec } from '../../config'
import { createPanelAlignment } from '../../data/panelAlignment'
import { MonitoringStore } from '../monitoringStore'

interface Deferred<T> {
  readonly promise: Promise<T>
  resolve(value: T): void
}

function deferred<T>(): Deferred<T> {
  const resolvers = Promise.withResolvers<T>()
  return {
    promise: resolvers.promise,
    resolve(value): void {
      resolvers.resolve(value)
    },
  }
}

function unresolvedApi(): MonitoringApi {
  const pending = <T>(): Promise<T> => Promise.withResolvers<T>().promise
  return {
    sensorRange: () => pending(),
    sensorLive: () => pending(),
    sensorStats: () => pending(),
    controlRange: () => pending(),
    controlTail: () => pending(),
    controlProjection: () => pending(),
  } as unknown as MonitoringApi
}

function device(
  location: string,
  deviceName: string,
  deviceType = 'light',
  displayName: string | null = 'Display Name'
): DeviceRegistryEntry {
  return {
    device_id: 1,
    device_type: deviceType,
    device_name: deviceName,
    display_name: displayName,
    location,
    cluster: location === 'Flower Room' ? 'front' : 'main',
    per_room_index: 1,
  }
}

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await Promise.resolve()
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('monitoring store device registry', () => {
  it('loads room lights once per activation without blocking history or polling', async () => {
    const request = deferred<DeviceRegistryEntry[]>()
    let calls = 0
    const store = new MonitoringStore('Flower Room', unresolvedApi(), {
      pollIntervalMs: 250,
      now: () => new Date(0),
      loadDeviceRegistry: () => {
        calls += 1
        return request.promise
      },
    })
    const unsubscribe = store.subscribe(() => {})
    await flushPromises()

    expect(calls).toBe(1)
    expect(store.getSnapshot().loading).toBe(true)
    request.resolve([
      device('Flower Room', 'light_f_1', 'light', '  Chilled Front QA  '),
      device('Veg Room', 'light_v_1', 'light', 'Eyefinity Top'),
      device('Flower Room', 'heater_f_1', 'heater', 'Heater'),
    ])
    await flushPromises()

    expect(store.getSnapshot().data.lightRegistry).toEqual([
      {
        device_name: 'light_f_1',
        display_name: '  Chilled Front QA  ',
        per_room_index: 1,
      },
    ])
    expect(store.getSnapshot().errors).toEqual([])

    await vi.advanceTimersByTimeAsync(5_000)
    expect(calls).toBe(1)
    unsubscribe()
  })

  it('discards an old room generation after unsubscribe and remount', async () => {
    const oldRequest = deferred<DeviceRegistryEntry[]>()
    const newRequest = deferred<DeviceRegistryEntry[]>()
    const flower = new MonitoringStore('Flower Room', unresolvedApi(), {
      loadDeviceRegistry: () => oldRequest.promise,
    })
    const stopFlower = flower.subscribe(() => {})
    await flushPromises()
    stopFlower()

    const veg = new MonitoringStore('Veg Room', unresolvedApi(), {
      loadDeviceRegistry: () => newRequest.promise,
    })
    const stopVeg = veg.subscribe(() => {})
    await flushPromises()

    oldRequest.resolve([device('Flower Room', 'light_f_1')])
    newRequest.resolve([
      device('Flower Room', 'light_f_1'),
      device('Veg Room', 'light_v_1'),
    ])
    await flushPromises()

    expect(flower.getSnapshot().data.lightRegistry).toBeUndefined()
    expect(veg.getSnapshot().data.lightRegistry?.map(entry => entry.device_name)).toEqual([
      'light_v_1',
    ])
    stopVeg()
  })

  it('ignores a prior activation completion after the same store is reopened', async () => {
    const oldRequest = deferred<DeviceRegistryEntry[]>()
    const currentRequest = deferred<DeviceRegistryEntry[]>()
    let calls = 0
    const store = new MonitoringStore('Flower Room', unresolvedApi(), {
      loadDeviceRegistry: () => (calls++ === 0 ? oldRequest.promise : currentRequest.promise),
    })
    const stopFirst = store.subscribe(() => {})
    await flushPromises()
    stopFirst()

    const stopSecond = store.subscribe(() => {})
    await flushPromises()
    oldRequest.resolve([device('Flower Room', 'light_f_1', 'light', 'Stale name')])
    currentRequest.resolve([device('Flower Room', 'light_f_1', 'light', 'Current name')])
    await flushPromises()

    expect(calls).toBe(2)
    expect(store.getSnapshot().data.lightRegistry?.[0]?.display_name).toBe('Current name')
    stopSecond()
  })

  it('keeps last-good names on failure and retries metadata explicitly', async () => {
    const warn = vi.spyOn(logger, 'warn').mockImplementation(() => {})
    let calls = 0
    const loadDeviceRegistry = (): Promise<DeviceRegistryEntry[]> => {
      calls += 1
      if (calls === 1 || calls === 3) return Promise.reject(new Error('registry unavailable'))
      return Promise.resolve([
        device('Flower Room', 'light_f_1', 'light', calls === 2 ? 'Chilled Front' : 'Chilled Front QA'),
      ])
    }
    const store = new MonitoringStore('Flower Room', unresolvedApi(), {
      loadDeviceRegistry,
    })
    const unsubscribe = store.subscribe(() => {})
    await flushPromises()

    expect(calls).toBe(1)
    expect(store.getSnapshot().errors).toEqual([])
    expect(warn).toHaveBeenCalledWith('device registry load failed', expect.any(Error))

    store.retry()
    await flushPromises()
    expect(store.getSnapshot().data.lightRegistry?.[0]?.display_name).toBe('Chilled Front')

    unsubscribe()
    const reactivate = store.subscribe(() => {})
    await flushPromises()
    expect(calls).toBe(3)
    expect(store.getSnapshot().data.lightRegistry?.[0]?.display_name).toBe('Chilled Front')

    store.retry()
    await flushPromises()
    expect(calls).toBe(4)
    expect(store.getSnapshot().data.lightRegistry?.[0]?.display_name).toBe('Chilled Front QA')
    expect(store.getSnapshot().errors).toEqual([])
    reactivate()
  })
  it('updates the rendered label after delayed metadata while preserving recorded intensity', async () => {
    const start = new Date(0)
    const end = new Date(60)
    const provenance = {
      origin: 'recorded' as const,
      quality: 'exact' as const,
      is_aggregated: false,
    }
    const sensorRange: MonitoringResponse = {
      metadata: {
        generated_at: end,
        tier: 'raw',
        range: { start, end },
        room: { room: 'Flower Room', nodes: ['front'] },
      },
      series: [],
      statistics: [],
    }
    const controlHistory: ControlMonitoringResponse = {
      range: { start, end },
      runtime_snapshot_version: 1,
      cursors: [],
      flush_health: [],
      climate: [],
      lights: [
        {
          name: 'light_f_1',
          metric: 'light_f_1',
          provenance,
          warnings: [],
          points: [
            {
              timestamp: start,
              value: 40,
              nominal_value: 40,
              device_name: 'light_f_1',
              provenance,
            },
          ],
          steps: [],
          linear: [],
        },
      ],
      devices: [],
      pid: [],
      photoperiod: [],
    }
    const projection: ProjectionPublicationResponse = { quality: 'unavailable', value: [] }
    const api = {
      sensorRange: async () => sensorRange,
      sensorLive: async () => [],
      sensorStats: async () => sensorRange,
      controlRange: async () => controlHistory,
      controlTail: async () => controlHistory,
      controlProjection: async () => projection,
    } as unknown as MonitoringApi
    const registryRequest = deferred<DeviceRegistryEntry[]>()
    let registryCalls = 0
    const store = new MonitoringStore('Flower Room', api, {
      now: () => end,
      loadDeviceRegistry: () => {
        registryCalls += 1
        return registryRequest.promise
      },
    })
    const equipmentPanel = flowerManifest.panels.find(
      (panel): panel is TimeseriesPanelSpec =>
        panel.kind === 'timeseries' && panel.id === 'flower-systems'
    )
    if (equipmentPanel === undefined) throw new Error('Flower equipment panel is required')
    const feed = createMonitoringPanelChartFeed({
      alignment: createPanelAlignment(),
      panel: equipmentPanel,
      seriesSpecs: equipmentPanel.series,
      now: () => end,
    })
    const initial = store.getSnapshot()
    const disconnect = feed.connect(
      {
        getSnapshot: () => store.getSnapshot(),
        subscribe: listener => store.subscribe(listener),
      },
      initial
    )
    await flushPromises()

    const before = feed.getData()
    const previousLight = before.series[0]
    if (previousLight === undefined) throw new Error('recorded light series is required')
    expect(registryCalls).toBe(1)
    expect(previousLight.label).toBe('light_f_1 - Intensity')

    registryRequest.resolve([
      device('Flower Room', 'light_f_1', 'light', '  Chilled Front QA  '),
    ])
    await flushPromises()

    const after = feed.getData()
    const currentLight = after.series[0]
    expect(after.series).toHaveLength(1)
    expect(currentLight?.key).toBe(previousLight.key)
    expect(currentLight?.label).toBe('Chilled Front QA - Intensity')
    expect(currentLight?.y).toEqual(previousLight.y)
    expect(currentLight?.y[after.x.indexOf(start.getTime())]).toBe(40)
    expect(store.getSnapshot().data.controlHistory?.lights[0]?.points[0]?.value).toBe(40)
    disconnect()
  })

})
