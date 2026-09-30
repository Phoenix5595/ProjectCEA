import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import {
  DASHBOARD_DEVICES,
  DASHBOARD_LIGHT_INTENSITIES,
  dashboardDeviceDetails,
} from '../../../tests/monitoring/dashboardDeviceFixtures'
import { useSensorPolling } from '../useSensorPolling'

const fixture = vi.hoisted(() => ({
  intensities: {} as Record<string, number>,
}))
vi.mock('../../services/api', () => ({ apiClient: {
  getAllDevices: async () => DASHBOARD_DEVICES,
  getSensorDataBulk: async (keys: string[]) => Object.fromEntries(
    keys.filter(key => key in fixture.intensities).map(key => [key, fixture.intensities[key]])
  ),
  getDevicesForLocationCluster: async (location: string, cluster: string) => ({
    location, cluster, devices: dashboardDeviceDetails(location, cluster),
  }),
  getLiveSensorData: async () => ({}),
  getAllLiveSensorData: async () => [
    { sensor: 'Flower Room_front_dry_bulb_f' },
    { sensor: 'Flower Room_back_dry_bulb_b' },
  ],
} }))

beforeEach(() => {
  vi.useFakeTimers()
  fixture.intensities = { ...DASHBOARD_LIGHT_INTENSITIES }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
  fixture.intensities = {}
})
it('loads canonical grow-room light names and percentages, retaining valid zero', async () => {
  const { result } = renderHook(() => useSensorPolling())
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
  expect(result.current.sensorData).toEqual({
    'Flower Room_main_light_f_1_intensity': 0,
    'Flower Room_main_light_f_2_intensity': 0,
    'Flower Room_main_light_f_3_intensity': 0,
    'Veg Room_main_light_v_1_intensity': 80,
    'Veg Room_main_light_v_2_intensity': 40,
    'Veg Room_main_light_v_3_intensity': 40,
  })
  expect(result.current.lightDisplayNames).toEqual({
    'Flower Room_main_light_f_1': 'Chilled Front',
    'Flower Room_main_light_f_2': 'Apache',
    'Flower Room_main_light_f_3': 'Chilled Back',
    'Veg Room_main_light_v_1': 'Eyefinity Top',
    'Veg Room_main_light_v_2': 'Ridgetop Bottom Right',
    'Veg Room_main_light_v_3': 'Ridgetop Bottom Left',
  })
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
  expect(result.current.sensorData['Flower Room_main_light_f_1_intensity']).toBe(0)
  expect(result.current.sensorData['Veg Room_main_light_v_1_intensity']).toBe(80)
  expect(result.current.devices.some(device => device.location === 'Lab')).toBe(false)
})
