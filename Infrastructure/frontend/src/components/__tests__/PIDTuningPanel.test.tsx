import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { DeviceRegistryEntry } from '../../types/device'
import type { PIDHistoryEntry, PIDModeInfo, PIDParameters } from '../../types/pid'
import PIDTuningPanel from '../PIDTuningPanel'

const mocks = vi.hoisted(() => ({
  apiClient: {
    getPIDParametersForRoom: vi.fn(),
    getPIDModeForRoom: vi.fn(),
    getPIDParameterHistoryForRoom: vi.fn(),
    updatePIDParametersForRoom: vi.fn(),
    setPIDModeForRoom: vi.fn(),
  },
}))

vi.mock('../../services/api', () => ({ apiClient: mocks.apiClient }))

const parameters: PIDParameters = { kp: 1.4, ki: 0.25, kd: 0.08 }
const modeInfo: PIDModeInfo = {
  device_type: 'heating',
  mode: 'on_off',
  hysteresis_high: 0.8,
  hysteresis_low: 0.2,
  autotune_active: false,
}
const history: PIDHistoryEntry[] = [
  {
    location: 'Veg Room',
    cluster: 'main',
    device_type: 'heating',
    changed_at: '2026-09-25T12:00:00.000Z',
    kp: 1.4,
    ki: 0.25,
    kd: 0.08,
    binary_hysteresis: 0.1,
    source: 'manual',
    updated_by: 'fixture operator',
  },
]

const devices: DeviceRegistryEntry[] = [
  {
    device_id: 101,
    device_type: 'heating',
    device_name: 'veg_heater_a',
    display_name: 'Heater A',
    location: 'Veg Room',
    cluster: 'main',
    channel: 0,
    pid_enabled: false,
  },
  {
    device_id: 102,
    device_type: 'heating',
    device_name: 'veg_heater_b',
    display_name: 'Heater B',
    location: 'Veg Room',
    cluster: 'main',
    channel: 1,
    pid_enabled: false,
  },
  {
    device_id: 103,
    device_type: 'heater',
    device_name: 'legacy_heater',
    display_name: 'Legacy heater type',
    location: 'Veg Room',
    cluster: 'main',
    channel: 2,
    pid_enabled: true,
  },
  {
    device_id: 104,
    device_type: 'light',
    device_name: 'grow_light',
    display_name: 'Grow light',
    location: 'Veg Room',
    cluster: 'main',
    relay_channel: 3,
  },
]

function renderPanel(registry = devices) {
  return render(<PIDTuningPanel location="Veg Room" cluster="main" devices={registry} />)
}

describe('PIDTuningPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.apiClient.getPIDParametersForRoom.mockResolvedValue(parameters)
    mocks.apiClient.getPIDModeForRoom.mockResolvedValue(modeInfo)
    mocks.apiClient.getPIDParameterHistoryForRoom.mockResolvedValue(history)
    mocks.apiClient.updatePIDParametersForRoom.mockResolvedValue(parameters)
    mocks.apiClient.setPIDModeForRoom.mockImplementation(
      async (
        _location: string,
        _cluster: string,
        deviceType: string,
        update: { mode: string; hysteresis_high?: number; hysteresis_low?: number }
      ) => ({
        ...modeInfo,
        device_type: deviceType,
        mode: update.mode,
        hysteresis_high: update.hysteresis_high ?? modeInfo.hysteresis_high,
        hysteresis_low: update.hysteresis_low ?? modeInfo.hysteresis_low,
      })
    )
  })

  it('uses assigned registry-backed heating types even when pid_enabled is false and explains shared scope', async () => {
    renderPanel()

    const selector = await screen.findByRole('combobox', { name: 'Assigned PID device type' })
    expect(selector).toHaveValue('heating')
    expect(selector.querySelectorAll('option')).toHaveLength(1)
    expect(
      screen.getByText(/2 assigned devices share the heating tuning scope/)
    ).toBeInTheDocument()
    expect(screen.getByText(/Registry PID enabled:/).textContent).toContain('no')
    expect(mocks.apiClient.getPIDParametersForRoom).toHaveBeenCalledWith(
      'Veg Room',
      'main',
      'heating'
    )
    expect(screen.getByText(/manual · fixture operator/)).toBeInTheDocument()
    expect(screen.getByText('Kp 1.400')).toBeInTheDocument()
  })

  it('saves positive ON/OFF high and low thresholds through the room mode endpoint', async () => {
    renderPanel()
    const high = await screen.findByRole('spinbutton', { name: 'Hysteresis high' })
    const low = screen.getByRole('spinbutton', { name: 'Hysteresis low' })
    fireEvent.change(high, { target: { value: '0.9' } })
    fireEvent.change(low, { target: { value: '0.1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save ON/OFF thresholds' }))

    await waitFor(() =>
      expect(mocks.apiClient.setPIDModeForRoom).toHaveBeenCalledWith(
        'Veg Room',
        'main',
        'heating',
        { mode: 'on_off', hysteresis_high: 0.9, hysteresis_low: 0.1 }
      )
    )
    expect(mocks.apiClient.updatePIDParametersForRoom).not.toHaveBeenCalled()
  })
  it('rejects non-finite gain drafts locally before sending an update', async () => {
    mocks.apiClient.getPIDModeForRoom.mockResolvedValue({ ...modeInfo, mode: 'pid' })
    renderPanel()
    const kp = await screen.findByRole('spinbutton', { name: 'PID KP' })
    fireEvent.change(kp, { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save PID gains' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('finite values for Kp, Ki, and Kd')
    expect(mocks.apiClient.updatePIDParametersForRoom).not.toHaveBeenCalled()
  })

  it('posts gains without binary_hysteresis and preserves drafts on 400 and 429 errors', async () => {
    mocks.apiClient.getPIDModeForRoom.mockResolvedValue({ ...modeInfo, mode: 'pid' })
    mocks.apiClient.updatePIDParametersForRoom
      .mockRejectedValueOnce({
        response: { status: 400, data: { detail: 'gain outside configured range' } },
      })
      .mockRejectedValueOnce({ response: { status: 429, data: { detail: 'too many updates' } } })
    renderPanel()
    const kp = await screen.findByRole('spinbutton', { name: 'PID KP' })

    fireEvent.change(kp, { target: { value: '1.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save PID gains' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('400: gain outside configured range')
    expect(kp).toHaveValue(1.5)

    fireEvent.change(kp, { target: { value: '1.7' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save PID gains' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('429: too many updates')
    )
    expect(kp).toHaveValue(1.7)
    expect(mocks.apiClient.updatePIDParametersForRoom).toHaveBeenNthCalledWith(
      1,
      'Veg Room',
      'main',
      'heating',
      { kp: 1.5, ki: 0.25, kd: 0.08 }
    )
    expect(mocks.apiClient.updatePIDParametersForRoom).toHaveBeenNthCalledWith(
      2,
      'Veg Room',
      'main',
      'heating',
      { kp: 1.7, ki: 0.25, kd: 0.08 }
    )
  })
  it('ignores a slow PID setting response after the registry type selection changes', async () => {
    let resolveSlowParameters: (value: PIDParameters) => void = () => undefined
    const slowParameters = new Promise<PIDParameters>(resolve => {
      resolveSlowParameters = resolve
    })
    const co2: DeviceRegistryEntry = {
      device_id: 105,
      device_type: 'co2',
      device_name: 'veg_co2',
      display_name: 'CO₂',
      location: 'Veg Room',
      cluster: 'main',
      channel: 5,
      pid_enabled: true,
    }
    mocks.apiClient.getPIDParametersForRoom.mockImplementationOnce(() => slowParameters)
    mocks.apiClient.getPIDModeForRoom.mockResolvedValueOnce({ ...modeInfo, mode: 'pid' })
    renderPanel([...devices, co2])
    const selector = await screen.findByRole('combobox', { name: 'Assigned PID device type' })
    expect(selector).toHaveValue('co2')

    fireEvent.change(selector, { target: { value: 'heating' } })
    expect(await screen.findByRole('combobox', { name: 'PID control mode' })).toHaveValue('on_off')
    await act(async () => {
      resolveSlowParameters({ kp: 99, ki: 99, kd: 99 })
      await slowParameters
    })

    expect(screen.getByRole('combobox', { name: 'PID control mode' })).toHaveValue('on_off')
  })

  it('labels AUTO as autotune and displays history at its backend changed_at timestamp', async () => {
    const rendered = renderPanel()
    const mode = await screen.findByRole('combobox', { name: 'PID control mode' })
    mocks.apiClient.getPIDModeForRoom.mockResolvedValueOnce({ ...modeInfo, mode: 'auto_pid' })
    fireEvent.change(mode, { target: { value: 'auto_pid' } })

    await waitFor(() =>
      expect(mocks.apiClient.setPIDModeForRoom).toHaveBeenCalledWith(
        'Veg Room',
        'main',
        'heating',
        { mode: 'auto_pid' }
      )
    )
    await waitFor(() => expect(mode).toHaveValue('auto_pid'))
    expect(within(mode).getByRole('option', { name: 'Autotune (AUTO)' })).toBeInTheDocument()
    expect(
      rendered.container.querySelector('time[datetime="2026-09-25T12:00:00.000Z"]')
    ).toBeInTheDocument()
  })

  it('keeps ON/OFF thresholds editable before switching from PID mode', async () => {
    mocks.apiClient.getPIDModeForRoom.mockResolvedValue({ ...modeInfo, mode: 'pid' })
    renderPanel()

    expect(await screen.findByRole('combobox', { name: 'PID control mode' })).toHaveValue('pid')
    fireEvent.change(await screen.findByRole('spinbutton', { name: 'Hysteresis high' }), {
      target: { value: '0.9' },
    })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Hysteresis low' }), {
      target: { value: '0.1' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save ON/OFF thresholds' }))

    await waitFor(() =>
      expect(mocks.apiClient.setPIDModeForRoom).toHaveBeenCalledWith(
        'Veg Room',
        'main',
        'heating',
        { mode: 'on_off', hysteresis_high: 0.9, hysteresis_low: 0.1 }
      )
    )
  })

  it('shows unavailable tuning instead of backend fallback defaults', async () => {
    mocks.apiClient.getPIDParametersForRoom.mockResolvedValue({
      ...parameters,
      source: 'default',
    })
    renderPanel()

    expect(await screen.findByRole('alert')).toHaveTextContent(/PID configuration is unavailable/)
    expect(screen.queryByRole('spinbutton', { name: 'PID KP' })).not.toBeInTheDocument()
    expect(mocks.apiClient.updatePIDParametersForRoom).not.toHaveBeenCalled()
  })
})
