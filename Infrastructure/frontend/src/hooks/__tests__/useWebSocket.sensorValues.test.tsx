import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'

const { callbackSpy, sensorHandlers } = vi.hoisted(() => ({
  callbackSpy: vi.fn(),
  sensorHandlers: new Set<(message: unknown) => void>(),
}))
const sensorHandler = (message: unknown): void => {
  for (const handler of sensorHandlers) handler(message)
}

vi.mock('../../services/websocket', () => ({
  wsClient: {
    get connectionState() {
      return 'open'
    },
    acquire: vi.fn(),
    release: vi.fn(),
    subscribeConnectionState: vi.fn(() => () => {}),
    on: vi.fn((type: string, handler: (message: unknown) => void) => {
      if (type !== 'sensor_update') return () => {}
      sensorHandlers.add(handler)
      return () => sensorHandlers.delete(handler)
    }),
  },
}))

import { useWebSocket } from '../useWebSocket'

describe('useWebSocket sensor value semantics', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    callbackSpy.mockClear()
    sensorHandlers.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const renderWebSocket = () => renderHook(() => useWebSocket({ onSensorUpdate: callbackSpy }))

  it('still publishes numeric zero and string zero as valid readings', () => {
    const { result } = renderWebSocket()
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb', value: 0, time: Date.now() })
    })
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'vpd', value: '0', time: Date.now() })
    })
    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(0)
    expect(result.current.sensorData['Veg Room_main_vpd']).toBe(0)
    expect(callbackSpy).toHaveBeenCalledWith('Veg Room_main_dry_bulb', 0)
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb'].invalid).toBe(false)
    expect(result.current.sensorMeta['Veg Room_main_vpd'].invalid).toBe(false)
  })

  it('leaves malformed keys unsubscribed from publication when no sensor key resolves', () => {
    const { result } = renderWebSocket()
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', value: 4 })
    })
    expect(result.current.sensorData).toEqual({})
    expect(callbackSpy).not.toHaveBeenCalled()
  })

  it('treats null and missing values as invalid without publishing a numeric reading', () => {
    const { result } = renderWebSocket()
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb', value: null, time: Date.now() })
    })
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'vpd' })
    })
    expect(result.current.sensorData).toEqual({})
    expect(callbackSpy).not.toHaveBeenCalled()
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb'].invalid).toBe(true)
    expect(result.current.sensorMeta['Veg Room_main_vpd'].invalid).toBe(true)
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb'].source).toBe('websocket')
  })

  it('marks non-finite numeric coercion as invalid', () => {
    const { result } = renderWebSocket()
    act(() => {
      sensorHandler?.({ location: 'Lab', cluster: 'main', sensor: 'lab_temp', value: 'not-a-number', time: Date.now() })
    })
    expect(result.current.sensorData).toEqual({})
    expect(result.current.sensorMeta['Lab_main_lab_temp'].invalid).toBe(true)
  })

  it('keeps the last good numeric reading when an invalid sample arrives later', () => {
    const { result } = renderWebSocket()
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb', value: 22, time: Date.now() })
    })
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb', value: null, time: Date.now() })
    })
    expect(result.current.sensorData['Veg Room_main_dry_bulb']).toBe(22)
    expect(callbackSpy).toHaveBeenCalledTimes(1)
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb'].invalid).toBe(true)
  })

  it('parses observed timestamps and marks a missing timestamp as null', () => {
    const { result } = renderWebSocket()
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'dry_bulb', value: 2, timestamp: '2026-09-29T12:00:00Z' })
    })
    expect(result.current.sensorMeta['Veg Room_main_dry_bulb'].observedAtMs).toBe(
      new Date('2026-09-29T12:00:00Z').getTime()
    )
    act(() => {
      sensorHandler?.({ location: 'Veg Room', cluster: 'main', sensor: 'co2', value: 3 })
    })
    expect(result.current.sensorMeta['Veg Room_main_co2'].observedAtMs).toBeNull()
  })

  it('does not deliver sensor callbacks after unmount', () => {
    const { unmount } = renderWebSocket()
    unmount()
    act(() => {
      sensorHandler({
        location: 'Veg Room',
        cluster: 'main',
        sensor: 'dry_bulb',
        value: 22,
      })
    })
    expect(callbackSpy).not.toHaveBeenCalled()
  })
})
