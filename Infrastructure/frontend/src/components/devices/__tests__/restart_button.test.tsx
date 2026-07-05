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

const mockConfigWithPending = {
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
  pending_restart_required_changes: ['control.safety_limits.max_temperature'],
  restart_hashes: { current: 'abc', sidecar: 'def' },
}

describe('restart button', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers({ shouldAdvanceTime: true })
  })

  it('shows flashing restart button with count and hides after restart completes', async () => {
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })

    vi.mocked(apiClient.getConfig)
      .mockResolvedValueOnce(mockConfigWithPending)
      .mockResolvedValueOnce({
        ...mockConfigWithPending,
        pending_restart_required_changes: ['control.safety_limits.max_temperature'],
      })
      .mockResolvedValueOnce({
        ...mockConfigWithPending,
        pending_restart_required_changes: [],
      })

    vi.mocked(apiClient.restartService).mockResolvedValue({
      status: 'restarting',
      delay_seconds: 1,
      command: 'sudo -n systemctl restart automation-service.service',
    })

    render(<SystemSettingsPanel />)

    // Button should be visible with count
    await waitFor(() => {
      const button = screen.getByTestId('restart-button')
      expect(button).toBeInTheDocument()
      expect(button).toHaveTextContent('Restart to apply (1)')
      expect(button.classList.contains('flash-red')).toBe(true)
    })

    // Click restart
    const button = screen.getByTestId('restart-button')
    await user.click(button)

    // Should show "Restarting..."
    await waitFor(() => {
      expect(screen.getByTestId('restart-button')).toHaveTextContent('Restarting...')
    })

    // Advance timers for first poll (2s)
    vi.advanceTimersByTime(2000)
    await waitFor(() => expect(apiClient.getConfig).toHaveBeenCalledTimes(2))

    // Advance timers for second poll (2s more)
    vi.advanceTimersByTime(2000)
    await waitFor(() => expect(apiClient.getConfig).toHaveBeenCalledTimes(3))

    // Button should hide after pending changes clear
    await waitFor(() => {
      expect(screen.queryByTestId('restart-button')).not.toBeInTheDocument()
    })

    vi.useRealTimers()
  })
})
