import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SystemSettingsPanel from '../SystemSettingsPanel'
import { apiClient } from '../../../services/api'

vi.mock('../../../services/api', () => ({
  apiClient: {
    getConfig: vi.fn(),
    putConfig: vi.fn(),
    restartService: vi.fn(),
  },
}))

const mockConfig = {
  hardware: {
    i2c_bus: 0,
    mcp_i2c_bus: 1,
    dfr0971_i2c_bus: 1,
    i2c_address: 39,
    active_low: true,
    require_mcp: true,
    dfr0971_boards: [
      { board_id: 0, i2c_address: 136, name: 'Board 0' },
    ],
  },
  safety_limits: {
    min_temperature: 10.0,
    max_temperature: 35.0,
    min_humidity: 30.0,
    max_humidity: 90.0,
    min_co2: 400.0,
    max_co2: 2000.0,
  },
  tuning: {
    update_interval: 2,
    last_good_hold_period: 300,
    binary_hysteresis: 0.5,
  },
  pid_limits: {
    heater: { kp_min: 0, kp_max: 100, ki_min: 0, ki_max: 10, kd_min: 0, kd_max: 5 },
    fan: { kp_min: 0, kp_max: 100, ki_min: 0, ki_max: 10, kd_min: 0, kd_max: 5 },
    co2: { kp_min: 0, kp_max: 100, ki_min: 0, ki_max: 10, kd_min: 0, kd_max: 5 },
  },
  pending_restart_required_changes: [],
  restart_hashes: { current: 'abc', sidecar: 'abc' },
}

describe('SystemSettingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.getConfig).mockResolvedValue(mockConfig)
  })

  it('renders all 3 sections', async () => {
    render(<SystemSettingsPanel />)
    await waitFor(() => expect(screen.getByText('Hardware')).toBeInTheDocument())
    expect(screen.getByText('Safety Limits')).toBeInTheDocument()
    expect(screen.getByText('Tuning')).toBeInTheDocument()
  })

  it('edits max_temperature and calls putConfig on save', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.putConfig).mockResolvedValue({
      pending_restart_required_changes: ['control.safety_limits.max_temperature'],
      restart_hashes: { current: 'def', sidecar: 'abc' },
    })

    render(<SystemSettingsPanel />)
    await waitFor(() => expect(screen.getByTestId('max_temperature')).toBeInTheDocument())

    const input = screen.getByTestId('max_temperature')
    await user.clear(input)
    await user.type(input, '38')

    const saveButton = screen.getByTestId('save-button')
    await user.click(saveButton)

    await waitFor(() => {
      expect(apiClient.putConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          safety_limits: expect.objectContaining({ max_temperature: 38 }),
        })
      )
    })

    // Restart button should be visible with count
    await waitFor(() => {
      expect(screen.getByTestId('restart-button')).toHaveTextContent('Restart to apply (1)')
    })
  })
})
