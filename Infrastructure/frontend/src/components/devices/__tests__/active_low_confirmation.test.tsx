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
    dfr0971_boards: [],
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

describe('active_low confirmation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.getConfig).mockResolvedValue(mockConfig)
  })

  it('shows confirmation modal when active_low is changed', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.putConfig).mockResolvedValue({
      pending_restart_required_changes: [],
      restart_hashes: { current: 'def', sidecar: 'abc' },
    })

    render(<SystemSettingsPanel />)
    await waitFor(() => expect(screen.getByTestId('active_low')).toBeInTheDocument())

    const toggle = screen.getByTestId('active_low')
    await user.click(toggle)

    const saveButton = screen.getByTestId('save-button')
    await user.click(saveButton)

    // Modal should appear
    await waitFor(() => {
      expect(screen.getByTestId('active-low-modal')).toBeInTheDocument()
    })

    // putConfig should NOT have been called yet
    expect(apiClient.putConfig).not.toHaveBeenCalled()

    // Type the old value "true"
    const confirmInput = screen.getByTestId('active-low-confirm-input')
    await user.type(confirmInput, 'true')

    // Click confirm
    const confirmButton = screen.getByTestId('active-low-confirm-button')
    await user.click(confirmButton)

    // Now putConfig should be called with active_low: false
    await waitFor(() => {
      expect(apiClient.putConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          hardware: expect.objectContaining({ active_low: false }),
        })
      )
    })
  })
})
