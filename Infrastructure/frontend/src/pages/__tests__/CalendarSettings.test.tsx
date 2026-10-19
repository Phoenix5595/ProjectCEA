import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CalendarSettings from '../CalendarSettings'

const mocks = vi.hoisted(() => ({
  apiClient: {
    getCalendarSyncConnection: vi.fn(),
    getFlowerCalendarModeTransitions: vi.fn(),
    updateFlowerCalendarModeTransitions: vi.fn(),
    testCalendarSyncConnection: vi.fn(),
    saveCalendarSyncConnection: vi.fn(),
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

  it('exposes accessible names for calendar connection fields', () => {
    render(<CalendarSettings />)

    expect(screen.getByLabelText('CalDAV base URL')).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(screen.getByLabelText('App password')).toBeInTheDocument()
    expect(screen.getByLabelText('Target calendar URL')).toBeInTheDocument()
  })

  it('persists disabling Flower calendar mode transitions', async () => {
    // Given: Flower calendar control is initially enabled.
    mocks.apiClient.updateFlowerCalendarModeTransitions.mockResolvedValue({ enabled: false })
    const user = userEvent.setup()
    render(<CalendarSettings />)

    // When: the operator turns the Flower-only control off.
    const control = await screen.findByRole('checkbox', {
      name: 'Flower calendar mode transitions',
    })
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
    const control = await screen.findByRole('checkbox', {
      name: 'Flower calendar mode transitions',
    })
    await user.click(control)

    // Then: the real API error is exposed and the saved state remains enabled.
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('Setting is unavailable'))
    expect(control).toBeChecked()
  })

  function guideDetails(): HTMLDetailsElement {
    const summary = screen.getByText('How to connect Nextcloud over Tailscale')
    const details = summary.closest('details')
    if (details === null) throw new Error('guide details missing')
    return details
  }

  async function submitTest(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText('CalDAV base URL'), 'https://nc.tail.ts.net/remote.php/dav/')
    await user.type(screen.getByLabelText('Username'), 'grower')
    await user.type(screen.getByLabelText('App password'), 'secret')
    await user.click(screen.getByRole('button', { name: 'Test' }))
  }

  it('opens the Tailscale guide when no connection is saved', async () => {
    render(<CalendarSettings />)

    await waitFor(() => expect(mocks.apiClient.getCalendarSyncConnection).toHaveBeenCalled())
    expect(guideDetails()).toHaveAttribute('open')
  })

  it('collapses the Tailscale guide when a connection is saved', async () => {
    mocks.apiClient.getCalendarSyncConnection.mockResolvedValue({
      id: 1,
      enabled: true,
      caldav_base_url: 'https://nc.tail.ts.net/remote.php/dav',
      account_email: 'a',
      target_calendar_url: 'https://nc.tail.ts.net/remote.php/dav/calendars/a/culture/',
    })
    render(<CalendarSettings />)

    await waitFor(() => expect(guideDetails()).not.toHaveAttribute('open'))
  })

  it('explains an auth failure and highlights the app-password step', async () => {
    mocks.apiClient.getCalendarSyncConnection.mockResolvedValue({
      id: 1,
      enabled: true,
      caldav_base_url: 'https://nc.tail.ts.net/remote.php/dav',
      account_email: 'a',
      target_calendar_url: 'https://nc.tail.ts.net/remote.php/dav/calendars/a/culture/',
    })
    mocks.apiClient.testCalendarSyncConnection.mockRejectedValue({
      response: { status: 400, data: { detail: { code: 'calendar_auth_failed' } } },
    })
    const user = userEvent.setup()
    render(<CalendarSettings />)
    await waitFor(() => expect(guideDetails()).not.toHaveAttribute('open'))

    await submitTest(user)

    const message =
      'Nextcloud rejected the user name or app password. Create a new app password and use your Nextcloud login name.'
    await waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith(message))
    expect(screen.getByRole('alert')).toHaveTextContent('Nextcloud rejected the user name or app password')
    expect(document.getElementById('nextcloud-guide-app-password')).toHaveAttribute('aria-current', 'step')
    expect(guideDetails()).toHaveAttribute('open')
  })

  it('falls back to a generic message for errors without a code', async () => {
    mocks.apiClient.testCalendarSyncConnection.mockRejectedValue(new Error('x'))
    const user = userEvent.setup()
    render(<CalendarSettings />)

    await submitTest(user)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Connection failed. Follow the guide below and try again.'
    )
    expect(document.querySelector('[aria-current="step"]')).toBeNull()
  })

  it('clears the error help when the credentials change', async () => {
    mocks.apiClient.testCalendarSyncConnection.mockRejectedValue({
      response: { status: 400, data: { detail: { code: 'calendar_auth_failed' } } },
    })
    const user = userEvent.setup()
    render(<CalendarSettings />)
    await submitTest(user)
    await screen.findByRole('alert')

    await user.type(screen.getByLabelText('Username'), 'x')

    expect(screen.queryByRole('alert')).toBeNull()
  })
})
