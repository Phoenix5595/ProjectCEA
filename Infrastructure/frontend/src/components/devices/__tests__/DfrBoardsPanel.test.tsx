import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DfrBoardsPanel from '../DfrBoardsPanel'
import { apiClient } from '../../../services/api'

vi.mock('../../../services/api', () => ({
  apiClient: {
    getDfrAssignments: vi.fn(),
    getLightsByRoom: vi.fn(),
    createLight: vi.fn(),
    updateLight: vi.fn(),
    deleteLight: vi.fn(),
    testLight: vi.fn(),
    assignDfrChannel: vi.fn(),
    updateDeviceConfig: vi.fn(),
  },
}))

const mockAssignments = {
  boards: [
    { board_id: 0, i2c_address: '0x88', name: 'Board 0', available: true },
    { board_id: 1, i2c_address: '0x89', name: 'Board 1', available: true },
  ],
  assignments: {
    '0': {
      '0': { location: 'Flower Room', cluster: 'main', device_name: 'light_f_1', display_name: 'Flower Light 1' },
      '1': null,
    },
    '1': {
      '0': null,
      '1': null,
    },
  },
  lights: [
    { location: 'Flower Room', cluster: 'main', device_name: 'light_f_1', display_name: 'Flower Light 1', dimming_board_id: 0, dimming_channel: 0 },
  ],
}

const mockRoomLights = [
  { device_id: 10, device_name: 'light_f_1', display_name: 'Flower Light 1', location: 'Flower Room', cluster: 'main', state: 0, mode: 'auto', channel: -1, bound_relay_channel: null },
]

describe('DfrBoardsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.getDfrAssignments).mockResolvedValue(mockAssignments)
    vi.mocked(apiClient.getLightsByRoom).mockResolvedValue(mockRoomLights)
  })

  it('does not render the Add light button (removed in favor of DeviceTable)', async () => {
    render(<DfrBoardsPanel />)

    await waitFor(() => {
      expect(screen.getByTestId('dfr-slot-0-1')).toBeInTheDocument()
    })

    expect(screen.queryByTestId('add-btn-0-1')).not.toBeInTheDocument()
    expect(screen.queryByTestId('add-form-0-1')).not.toBeInTheDocument()
    expect(screen.queryByTestId('add-submit-0-1')).not.toBeInTheDocument()
    expect(screen.queryByTestId('add-index-0-1')).not.toBeInTheDocument()
  })

  it('shows test progress and disables sibling actions while test is running', async () => {
    const user = userEvent.setup()
    let resolveTest: (value: { success: boolean }) => void = () => {}
    vi.mocked(apiClient.testLight).mockImplementation(
      () => new Promise((resolve) => {
        resolveTest = resolve
      })
    )

    render(<DfrBoardsPanel />)

    await waitFor(() => {
      expect(screen.getByTestId('test-btn-0-0')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('test-btn-0-0'))

    await waitFor(() => {
      expect(screen.getByTestId('test-progress-0-0')).toBeInTheDocument()
    })

    expect(screen.getByTestId('test-btn-0-0')).toBeDisabled()

    resolveTest({ success: true })

    await waitFor(() => {
      expect(screen.queryByTestId('test-progress-0-0')).not.toBeInTheDocument()
    })
  }, 10000)

  it('shows confirmation message before removing a light', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.deleteLight).mockResolvedValue({ success: true })

    render(<DfrBoardsPanel />)

    await waitFor(() => {
      expect(screen.getByTestId('remove-btn-0-0')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('remove-btn-0-0'))

    await waitFor(() => {
      expect(screen.getByTestId('remove-confirm-0-0')).toBeInTheDocument()
    })

    expect(screen.getByText(/Remove light\? \(Its relay will also be unbound\.\)/)).toBeInTheDocument()

    await user.click(screen.getByTestId('remove-confirm-0-0'))

    await waitFor(() => {
      expect(apiClient.deleteLight).toHaveBeenCalledWith(10)
    })
  }, 10000)

  it('shows DFR board_id and channel label instead of relay identity', async () => {
    render(<DfrBoardsPanel />)

    await waitFor(() => {
      expect(screen.getByTestId('dfr-slot-0-0')).toBeInTheDocument()
    })

    const slotLabel = screen.getByTestId('dfr-slot-0-0').querySelector('.text-xs.font-semibold')
    expect(slotLabel).not.toBeNull()
    const labelText = slotLabel?.textContent ?? ''
    expect(labelText).toBe('DFR0 · CH0')
    expect(labelText).not.toContain('R{')
    expect(labelText).not.toContain('GPA')
    expect(labelText).not.toContain('GPB')
  }, 10000)
})
