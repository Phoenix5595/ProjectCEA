import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CalendarSettings from '../CalendarSettings'

const mocks = vi.hoisted(() => ({
  apiClient: {
    getCalendarSyncConnection: vi.fn(),
    getFlowerCalendarModeTransitions: vi.fn(),
    updateFlowerCalendarModeTransitions: vi.fn(),
  },
  toast: { error: vi.fn(), success: vi.fn() },
}))

vi.mock('../../services/api', () => ({ apiClient: mocks.apiClient }))
vi.mock('sonner', () => ({ toast: mocks.toast }))

describe('CalendarSettings', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mocks.apiClient.getCalendarSyncConnection.mockResolvedValue(null)
    mocks.apiClient.getFlowerCalendarModeTransitions.mockResolvedValue({ enabled: true })
  })

  it('persists disabling Flower calendar mode transitions', async () => {
    // Given: Flower calendar control is initially enabled.
    mocks.apiClient.updateFlowerCalendarModeTransitions.mockResolvedValue({ enabled: false })
    const user = userEvent.setup()
    render(<CalendarSettings />)

    // When: the operator turns the Flower-only control off.
    const control = await screen.findByRole('checkbox', { name: 'Flower calendar mode transitions' })
    await user.click(control)

    // Then: the persisted API receives the disabled setting and the UI reflects it.
    await waitFor(() => {
      expect(mocks.apiClient.updateFlowerCalendarModeTransitions).toHaveBeenCalledWith(false)
      expect(control).not.toBeChecked()
    })
  })

  it('surfaces a genuine save error without changing the persisted control state', async () => {
    // Given: the persistence API rejects disabling Flower calendar control.
    mocks.apiClient.updateFlowerCalendarModeTransitions.mockRejectedValue(
      new Error('Setting is unavailable')
    )
    const user = userEvent.setup()
    render(<CalendarSettings />)

    // When: the operator tries to disable the control.
    const control = await screen.findByRole('checkbox', { name: 'Flower calendar mode transitions' })
    await user.click(control)

    // Then: the real API error is exposed and the saved state remains enabled.
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('Setting is unavailable'))
    expect(control).toBeChecked()
  })
})
