import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DeviceTable from '../DeviceTable'
import { apiClient } from '../../../services/api'
import type { DeviceRegistryEntry } from '../../../types/device'

vi.mock('../../../services/api', () => ({
  apiClient: {
    getDeviceRegistry: vi.fn(),
    createDevice: vi.fn(),
    updateDevice: vi.fn(),
    deleteDevice: vi.fn(),
  },
}))

const mockDevices: DeviceRegistryEntry[] = [
  {
    device_id: 1,
    device_type: 'light',
    device_name: 'light_f_1',
    display_name: 'Flower Light 1',
    location: 'Flower Room',
    cluster: 'main',
    relay_channel: 10,
    board_id: 2,
    dimming_channel: 0,
    per_room_index: 1,
  },
  {
    device_id: 2,
    device_type: 'heating',
    device_name: 'heating_f_1',
    display_name: 'Flower Heater',
    location: 'Flower Room',
    cluster: 'main',
    channel: 0,
    pid_enabled: false,
    interlock_with: [],
    pid_setpoints: {},
  },
]

describe('DeviceTable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.getDeviceRegistry).mockResolvedValue(mockDevices)
  })

  it('renders device rows from registry data', async () => {
    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('device-row-1')).toBeInTheDocument()
    })

    expect(screen.getByTestId('device-row-2')).toBeInTheDocument()
    expect(screen.getByText('Flower Light 1')).toBeInTheDocument()
    expect(screen.getByText('Flower Heater')).toBeInTheDocument()
  })

  it('shows DFR Board and Channel for lights, dashes for non-lights', async () => {
    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('device-row-1')).toBeInTheDocument()
    })

    const lightRow = screen.getByTestId('device-row-1')
    const heaterRow = screen.getByTestId('device-row-2')

    expect(lightRow.textContent).toContain('2')
    expect(lightRow.textContent).toContain('0')

    const heaterCells = heaterRow.querySelectorAll('td')
    const dfrBoardCell = heaterCells[4]
    const dfrChannelCell = heaterCells[5]
    expect(dfrBoardCell.textContent).toBe('—')
    expect(dfrChannelCell.textContent).toBe('—')
  })

  it('opens add form when Add device button is clicked', async () => {
    const user = userEvent.setup()
    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('add-device-btn')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('add-device-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('add-row')).toBeInTheDocument()
    })

    expect(screen.getByTestId('add-submit')).toBeInTheDocument()
    expect(screen.getByTestId('add-cancel')).toBeInTheDocument()
  })

  it('creates a non-light device via createDevice', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.createDevice).mockResolvedValue({
      device_id: 99,
      device_type: 'heating',
      device_name: 'heating_v_1',
      display_name: 'Veg Heater',
      location: 'Veg Room',
      cluster: 'main',
      channel: 6,
    })

    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('add-device-btn')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('add-device-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('add-row')).toBeInTheDocument()
    })

    const inputs = screen.getByTestId('add-row').querySelectorAll('input, select')
    const displayNameInput = inputs[0] as HTMLInputElement
    const typeSelect = inputs[1] as HTMLSelectElement
    const roomSelect = inputs[2] as HTMLSelectElement
    const channelSelect = inputs[3] as HTMLSelectElement

    await user.type(displayNameInput, 'Veg Heater')
    await user.selectOptions(typeSelect, 'heater')
    await user.selectOptions(roomSelect, 'Veg Room')
    await user.selectOptions(channelSelect, '6')

    await user.click(screen.getByTestId('add-submit'))

    await waitFor(() => {
      expect(apiClient.createDevice).toHaveBeenCalledWith(
        expect.objectContaining({
          device_type: 'heater',
          room: 'Veg Room',
          display_name: 'Veg Heater',
          channel: 6,
        })
      )
    })
  })

  it('creates a light device with board_id and dimming_channel', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.createDevice).mockResolvedValue({
      device_id: 100,
      device_type: 'light',
      device_name: 'light_v_4',
      display_name: 'Veg Light 4',
      location: 'Veg Room',
      cluster: 'main',
      board_id: 0,
      dimming_channel: 0,
    })

    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('add-device-btn')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('add-device-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('add-row')).toBeInTheDocument()
    })

    const inputs = screen.getByTestId('add-row').querySelectorAll('input, select')
    const displayNameInput = inputs[0] as HTMLInputElement
    const typeSelect = inputs[1] as HTMLSelectElement
    const roomSelect = inputs[2] as HTMLSelectElement

    await user.type(displayNameInput, 'Veg Light 4')
    await user.selectOptions(typeSelect, 'light')
    await user.selectOptions(roomSelect, 'Veg Room')

    await user.click(screen.getByTestId('add-submit'))

    await waitFor(() => {
      expect(apiClient.createDevice).toHaveBeenCalledWith(
        expect.objectContaining({
          device_type: 'light',
          room: 'Veg Room',
          display_name: 'Veg Light 4',
          board_id: 0,
          dimming_channel: 0,
        })
      )
    })
  })

  it('opens inline edit when a row is clicked and saves via updateDevice', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.updateDevice).mockResolvedValue({
      device_id: 2,
      device_type: 'heating',
      device_name: 'heating_f_1',
      display_name: 'Updated Heater',
      location: 'Flower Room',
      cluster: 'main',
      channel: 0,
    })

    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('device-row-2')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('device-row-2'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-row-2')).toBeInTheDocument()
    })

    const editInput = screen.getByTestId('edit-row-2').querySelector('input') as HTMLInputElement
    expect(editInput.value).toBe('Flower Heater')

    await user.clear(editInput)
    await user.type(editInput, 'Updated Heater')

    await user.click(screen.getByTestId('edit-save-2'))

    await waitFor(() => {
      expect(apiClient.updateDevice).toHaveBeenCalledWith(
        2,
        expect.objectContaining({
          display_name: 'Updated Heater',
          channel: 0,
        })
      )
    })
  })

  it('shows delete confirmation then deletes via deleteDevice', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.deleteDevice).mockResolvedValue({ success: true })

    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('device-row-1')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('delete-btn-1'))

    await waitFor(() => {
      expect(screen.getByTestId('delete-confirm-1')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('delete-confirm-1'))

    await waitFor(() => {
      expect(apiClient.deleteDevice).toHaveBeenCalledWith(1)
    })
  })

  it('cancels delete when Cancel is clicked in confirmation', async () => {
    const user = userEvent.setup()

    render(<DeviceTable />)

    await waitFor(() => {
      expect(screen.getByTestId('device-row-1')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('delete-btn-1'))

    await waitFor(() => {
      expect(screen.getByTestId('delete-cancel-1')).toBeInTheDocument()
    })

    await user.click(screen.getByTestId('delete-cancel-1'))

    await waitFor(() => {
      expect(screen.queryByTestId('delete-confirm-1')).not.toBeInTheDocument()
    })

    expect(apiClient.deleteDevice).not.toHaveBeenCalled()
  })
})