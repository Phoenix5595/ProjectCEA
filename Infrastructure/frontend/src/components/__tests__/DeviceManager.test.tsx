import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import DeviceManager from '../DeviceManager'
import { apiClient } from '../../services/api'

vi.mock('../../services/api', () => ({
  apiClient: {
    getChannels: vi.fn(),
    getRelayBoardState: vi.fn(),
    getLightsByRoom: vi.fn(),
    getDfrAssignments: vi.fn(),
    updateChannelDevice: vi.fn(),
    clearChannelDevice: vi.fn(),
    controlDevice: vi.fn(),
    controlChannel: vi.fn(),
    setDeviceMode: vi.fn(),
  },
}))

const mockChannelsResponse = {
  channels: {
    '0': { channel: 0, device_name: 'light_f_1', display_name: 'Flower Light 1', device_type: 'light', location: 'Flower Room', cluster: 'main', light_name: 'Flower Light 1' },
    '1': { channel: 1, device_name: null, display_name: null, device_type: null, location: null, cluster: null, light_name: null },
  },
  light_names: [
    { name: 'Flower Light 1', device_name: 'light_f_1', location: 'Flower Room', cluster: 'main', bound_relay_channel: 0, device_id: 10 },
    { name: 'Flower Light 2', device_name: 'light_f_2', location: 'Flower Room', cluster: 'main', bound_relay_channel: 5, device_id: 11 },
    { name: 'Flower Light 3', device_name: 'light_f_3', location: 'Flower Room', cluster: 'main', bound_relay_channel: null, device_id: 12 },
  ],
}

const mockRelayState = {
  channels: Array(16).fill(false),
  timestamps: Array(16).fill(null),
  mcp_connected: true,
  simulation: false,
}

describe('DeviceManager relay dropdown greyout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.getChannels).mockResolvedValue(mockChannelsResponse)
    vi.mocked(apiClient.getRelayBoardState).mockResolvedValue(mockRelayState)
    vi.mocked(apiClient.getDfrAssignments).mockResolvedValue({ boards: [], assignments: {}, lights: [] })
    vi.mocked(apiClient.getLightsByRoom).mockResolvedValue([
      { device_id: 10, device_name: 'light_f_1', display_name: 'Flower Light 1', location: 'Flower Room', cluster: 'main', state: 0, mode: 'auto', channel: -1, bound_relay_channel: 0 },
      { device_id: 11, device_name: 'light_f_2', display_name: 'Flower Light 2', location: 'Flower Room', cluster: 'main', state: 0, mode: 'auto', channel: -1, bound_relay_channel: 5 },
      { device_id: 12, device_name: 'light_f_3', display_name: 'Flower Light 3', location: 'Flower Room', cluster: 'main', state: 0, mode: 'auto', channel: -1, bound_relay_channel: null },
    ])
  })

  it('greys out lights bound to another relay channel', async () => {
    const user = userEvent.setup()
    render(<DeviceManager />)

    await waitFor(() => {
      expect(screen.getByText('R4')).toBeInTheDocument()
    })

    const channel1Row = screen.getByText('R4').closest('tr')
    expect(channel1Row).not.toBeNull()
    await user.click(channel1Row!)

    await waitFor(() => {
      expect(screen.getByDisplayValue('Select type')).toBeInTheDocument()
    })

    const typeSelect = screen.getByDisplayValue('Select type')
    await user.selectOptions(typeSelect, 'light')

    await waitFor(() => {
      const lightSelect = screen.queryByDisplayValue('Select light')
      expect(lightSelect).toBeInTheDocument()
    })

    const lightSelect = screen.getByDisplayValue('Select light') as HTMLSelectElement
    const options = Array.from(lightSelect.options)

    const flowerLight2Option = options.find((o) => o.textContent?.includes('Flower Light 2'))
    expect(flowerLight2Option).toBeDefined()
    expect(flowerLight2Option?.disabled).toBe(true)

    const flowerLight3Option = options.find((o) => o.textContent?.includes('Flower Light 3'))
    expect(flowerLight3Option).toBeDefined()
    expect(flowerLight3Option?.disabled).toBe(false)
  })
})
