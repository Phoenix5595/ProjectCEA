import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { SettingsResponse, SystemConfigResponse } from '../../../types/systemConfig'

const apiMocks = vi.hoisted(() => ({
  getConfig: vi.fn(),
  getSettings: vi.fn(),
  putConfig: vi.fn(),
  putSettings: vi.fn(),
}))

vi.mock('../../../services/api', () => ({ apiClient: apiMocks }))

import SystemSettingsPanel from '../SystemSettingsPanel'

const record = <T,>(value: T) => ({ value, updated_at: null, updated_by: null })

const CONFIG: SystemConfigResponse = {
  hardware: {
    i2c_bus: 0,
    mcp_i2c_bus: 0,
    dfr0971_i2c_bus: 1,
    i2c_address: 32,
    active_low: false,
    require_mcp: true,
    dfr0971_boards: [{ board_id: 0, i2c_address: 95, name: 'Flower DFR' }],
  },
  pending_restart_required_changes: [],
  restart_hashes: { current: null, sidecar: null },
}

const pidPair = { kp_min: 0, kp_max: 10, ki_min: 0, ki_max: 1, kd_min: 0, kd_max: 1 }

const SETTING_VALUES = {
  automation: {
    update_interval: record(1),
    last_good_hold_period: record(30),
    binary_hysteresis: record(0.5),
    safety_limits: record({
      min_temperature: 10,
      max_temperature: 35,
      min_humidity: 30,
      max_humidity: 90,
      min_co2: 400,
      max_co2: 2000,
    }),
    pid_limits: record({ heater: pidPair, fan: pidPair, co2: pidPair }),
  },
  soil: {
    poll_interval_seconds: record(60),
    discovery_interval_seconds: record(300),
  },
  onewire: { poll_interval_seconds: record(10) },
  weather: {
    station_icao: record('CYUL'),
    poll_interval_seconds: record(600),
    api_url: record('https://example.invalid'),
    room_name: record('Outside'),
    device_name: record('weather'),
  },
}

const openSchema = { minimum: null, maximum: null, pattern: null, read_only: false }

const SETTINGS = {
  services: Object.fromEntries(
    Object.entries(SETTING_VALUES).map(([service, settings]) => [
      service,
      {
        settings,
        schema: Object.fromEntries(Object.keys(settings).map(key => [key, openSchema])),
      },
    ])
  ),
} as unknown as SettingsResponse

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getConfig.mockResolvedValue(structuredClone(CONFIG))
  apiMocks.getSettings.mockResolvedValue(structuredClone(SETTINGS))
  apiMocks.putConfig.mockResolvedValue({
    pending_restart_required_changes: [],
    restart_hashes: { current: null, sidecar: null },
  })
})

describe('SystemSettingsPanel DFR boards', () => {
  it('offers no board-name input and keeps the stored name when the address changes', async () => {
    render(<SystemSettingsPanel />)

    const address = await screen.findByTestId('board_0_address')
    expect(screen.queryByTestId('board_0_name')).toBeNull()

    fireEvent.change(address, { target: { value: '94' } })
    fireEvent.click(screen.getByTestId('save-button'))

    await waitFor(() => expect(apiMocks.putConfig).toHaveBeenCalledTimes(1))
    expect(apiMocks.putConfig.mock.calls[0][0]).toEqual({
      hardware: { dfr0971_boards: [{ board_id: 0, i2c_address: 94, name: 'Flower DFR' }] },
    })
  })
})
