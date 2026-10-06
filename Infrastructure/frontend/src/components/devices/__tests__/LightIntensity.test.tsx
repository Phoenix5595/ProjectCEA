import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import LightIntensity from '../../LightIntensity'
import type { LightIntensityProps, LightPendingSaveResult } from '../../LightIntensity'

const mocks = vi.hoisted(() => ({
  getZoneLightsStatus: vi.fn(),
  setLightIntensity: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))
vi.mock('../../../services/api', () => ({ apiClient: {
  getZoneLightsStatus: mocks.getZoneLightsStatus,
  setLightIntensity: mocks.setLightIntensity,
} }))
vi.mock('sonner', () => ({ toast: { success: mocks.success, error: mocks.error } }))
vi.mock('../../../utils/logger', () => ({ logger: { error: vi.fn() } }))

interface ZoneLightsStatus {
  lights: Array<{ device: string; display_name: string; intensity: number; target_intensity: number }>
}
interface TargetResponse {
  success: boolean
  rows_updated?: number
}
interface Deferred<T> {
  promise: Promise<T>
  resolve(value: T): void
  reject(error: Error): void
}
function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}
function status(names = ['light_1', 'light_2'], target = 40): ZoneLightsStatus {
  return { lights: names.map(name => ({
    device: name, display_name: name, intensity: 0, target_intensity: target,
  })) }
}
const baseProps: LightIntensityProps = {
  location: 'Flower Room', cluster: 'main', activeModeId: 2, targetEditingEnabled: true,
}
function mount(overrides: Partial<LightIntensityProps> = {}) {
  const ref = createRef<{ savePendingChanges(): Promise<LightPendingSaveResult>; discardPendingChanges(): void }>()
  const onPendingChange = vi.fn()
  let props = { ...baseProps, onPendingChange, ...overrides }
  const rendered = render(<LightIntensity {...props} ref={ref} />)
  return {
    ...rendered, ref, onPendingChange,
    update(next: Partial<LightIntensityProps>) {
      props = { ...props, ...next }
      rendered.rerender(<LightIntensity {...props} ref={ref} />)
    },
  }
}
async function settle() {
  await act(async () => { await vi.advanceTimersByTimeAsync(0) })
}
function edit(name: string, value: number) {
  fireEvent.change(screen.getByRole('spinbutton', { name: `${name} light target` }), {
    target: { value: String(value) },
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  mocks.getZoneLightsStatus.mockResolvedValue(status())
  mocks.setLightIntensity.mockResolvedValue({ success: true })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('live light targets', () => {
  it('keeps external-mode edits bound to their original mode until explicit discard, even hidden', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 65)
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 2, devices: ['light_1'] })

    widget.update({ activeModeId: 3, hiddenNormalControls: true })
    await settle()
    expect(screen.getByRole('alert')).toHaveTextContent('Stale light edits belong to mode 2')
    expect(screen.getByText(/Pending light targets: light_1: 65%/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save light targets' })).toBeDisabled()
    expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument()
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 2, devices: ['light_1'] })
    let outcome: LightPendingSaveResult | undefined
    await act(async () => { outcome = await widget.ref.current!.savePendingChanges() })
    expect(outcome).toEqual({ savedDevices: [], failedDevices: ['light_1'] })
    expect(mocks.setLightIntensity).not.toHaveBeenCalled()
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(mocks.getZoneLightsStatus).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button', { name: 'Discard light edits' }))
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: null, devices: [] })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText('Light intensity')).not.toBeInTheDocument()
    widget.update({ hiddenNormalControls: false })
    await settle()
    edit('light_1', 70)
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 3, devices: ['light_1'] })
  })

  it('returns partial success, clears only successful fixtures and lets failed fixtures retry', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 60)
    edit('light_2', 75)
    mocks.setLightIntensity.mockResolvedValueOnce({ success: true })
      .mockRejectedValueOnce(new Error('fixture write failed'))
    let outcome: LightPendingSaveResult | undefined
    await act(async () => { outcome = await widget.ref.current!.savePendingChanges() })
    expect(outcome).toEqual({ savedDevices: ['light_1'], failedDevices: ['light_2'] })
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 2, devices: ['light_2'] })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(40)
    expect(screen.getByRole('spinbutton', { name: 'light_2 light target' })).toHaveValue(75)
    expect(mocks.setLightIntensity).toHaveBeenNthCalledWith(
      1, 'Flower Room', 'main', 'light_1', 60, { expectedModeId: 2 }
    )
    expect(mocks.setLightIntensity).toHaveBeenNthCalledWith(
      2, 'Flower Room', 'main', 'light_2', 75, { expectedModeId: 2 }
    )
    expect(mocks.success).not.toHaveBeenCalled()

    mocks.setLightIntensity.mockResolvedValueOnce({ success: true })
    await act(async () => { outcome = await widget.ref.current!.savePendingChanges() })
    expect(outcome).toEqual({ savedDevices: ['light_2'], failedDevices: [] })
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: null, devices: [] })
    const reads = mocks.getZoneLightsStatus.mock.calls.length
    await act(async () => { outcome = await widget.ref.current!.savePendingChanges() })
    expect(outcome).toEqual({ savedDevices: [], failedDevices: [] })
    expect(mocks.getZoneLightsStatus).toHaveBeenCalledTimes(reads)
  })

  it.each([{ success: false }, { success: true, rows_updated: 0 }])(
    'reports unsuccessful writes from the live panel without a success message: %j',
    async response => {
      mount()
      await settle()
      edit('light_1', 60)
      mocks.setLightIntensity.mockResolvedValue(response)
      fireEvent.click(screen.getByRole('button', { name: 'Save light targets' }))
      await settle()
      expect(mocks.error).toHaveBeenCalledWith(expect.stringContaining('light_1'))
      expect(mocks.success).not.toHaveBeenCalled()
      expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(60)
      expect(screen.getByRole('button', { name: 'Discard light edits' })).toBeEnabled()
    }
  )

  it('blocks duplicate saves and editing in flight, and keeps the failed captured value pending', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 60)
    const write = deferred<TargetResponse>()
    mocks.setLightIntensity.mockReturnValueOnce(write.promise)
    let first!: Promise<LightPendingSaveResult>
    act(() => { first = widget.ref.current!.savePendingChanges() })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Discard light edits' })).toBeDisabled()
    let duplicate: LightPendingSaveResult | undefined
    await act(async () => { duplicate = await widget.ref.current!.savePendingChanges() })
    expect(duplicate).toEqual({ savedDevices: [], failedDevices: ['light_1'] })
    expect(mocks.setLightIntensity).toHaveBeenCalledTimes(1)
    await act(async () => { write.reject(new Error('write failed')); await first })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(60)
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toBeEnabled()
  })

  it('does not carry successful old-room saves or late status reads into another room', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 60)
    const write = deferred<TargetResponse>()
    const oldRead = deferred<ZoneLightsStatus>()
    mocks.setLightIntensity.mockReturnValueOnce(write.promise)
    mocks.getZoneLightsStatus.mockReturnValueOnce(oldRead.promise)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    let saving!: Promise<LightPendingSaveResult>
    act(() => { saving = widget.ref.current!.savePendingChanges() })
    mocks.getZoneLightsStatus.mockResolvedValueOnce(status(['veg_light'], 85))
    widget.update({ location: 'Veg Room', activeModeId: 1 })
    expect(screen.queryByRole('spinbutton', { name: 'light_1 light target' })).not.toBeInTheDocument()
    await act(async () => {
      oldRead.resolve(status(['old_room_light'], 12))
      write.resolve({ success: true })
      await saving
    })
    await settle()
    expect(screen.queryByText('old_room_light')).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'veg_light light target' })).toHaveValue(85)
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: null, devices: [] })
    edit('veg_light', 90)
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 1, devices: ['veg_light'] })
  })

  it('polls every five seconds without overlapping slow requests', async () => {
    const read = deferred<ZoneLightsStatus>()
    mocks.getZoneLightsStatus.mockReturnValueOnce(read.promise)
    mount()
    await act(async () => { await vi.advanceTimersByTimeAsync(15_000) })
    expect(mocks.getZoneLightsStatus).toHaveBeenCalledTimes(1)
    await act(async () => { read.resolve(status()); await read.promise })
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    expect(mocks.getZoneLightsStatus).toHaveBeenCalledTimes(2)
  })

  it('ignores a late status response from the previous mode without rebinding edits', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 65)
    const oldRead = deferred<ZoneLightsStatus>()
    mocks.getZoneLightsStatus.mockReturnValueOnce(oldRead.promise)
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000) })
    mocks.getZoneLightsStatus.mockResolvedValueOnce(status(['active_light'], 90))
    widget.update({ activeModeId: 3 })
    await act(async () => { oldRead.resolve(status(['old_mode_light'], 5)) })
    await settle()
    expect(screen.queryByText('old_mode_light')).not.toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: 'active_light light target' })).toHaveValue(90)
    expect(screen.getByRole('spinbutton', { name: 'active_light light target' })).toBeDisabled()
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 2, devices: ['light_1'] })
  })

  it('retains valid mode-level edits during same-mode submode changes', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 65)
    widget.update({ activeModeId: 2, compact: true })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save light targets' })).toBeEnabled()
    await act(async () => { await widget.ref.current!.savePendingChanges() })
    expect(mocks.setLightIntensity).toHaveBeenCalledWith(
      'Flower Room', 'main', 'light_1', 65, { expectedModeId: 2 }
    )
  })

  it('preserves the ten-percent minimum and cancels the delayed clamp on discard or valid replacement', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 5)
    expect(screen.getByText('Minimum target is 10%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save light targets' })).toBeDisabled()
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(10)
    await act(async () => { await widget.ref.current!.savePendingChanges() })
    expect(mocks.setLightIntensity).toHaveBeenCalledWith(
      'Flower Room', 'main', 'light_1', 10, { expectedModeId: 2 }
    )
    edit('light_1', 5)
    fireEvent.click(screen.getByRole('button', { name: 'Discard light edits' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(40)
    expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: null, devices: [] })
    edit('light_1', 5)
    edit('light_1', 75)
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(75)
  })

  it.each(['room', 'mode', 'save'] as const)(
    'fences a delayed minimum clamp after a %s boundary',
    async boundary => {
      const widget = mount()
      await settle()
      edit('light_1', 5)
      if (boundary === 'room') {
        mocks.getZoneLightsStatus.mockResolvedValueOnce(status(['veg_light'], 85))
        widget.update({ location: 'Veg Room', activeModeId: 1 })
        await settle()
      } else if (boundary === 'mode') {
        widget.update({ activeModeId: 3, hiddenNormalControls: true })
      } else {
        await act(async () => { await widget.ref.current!.savePendingChanges() })
      }
      await act(async () => { await vi.advanceTimersByTimeAsync(2_000) })
      expect(mocks.setLightIntensity).not.toHaveBeenCalled()
      if (boundary === 'room') {
        expect(screen.getByRole('spinbutton', { name: 'veg_light light target' })).toHaveValue(85)
        expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: null, devices: [] })
      } else if (boundary === 'mode') {
        expect(screen.getByText(/Pending light targets: light_1: 5%/)).toBeInTheDocument()
        expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: 2, devices: ['light_1'] })
      } else {
        expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toHaveValue(5)
        expect(screen.getByRole('button', { name: 'Save light targets' })).toBeDisabled()
      }
    }
  )

  it('blocks target editing and writes until the page confirms a matching running mode', async () => {
    const widget = mount()
    await settle()
    edit('light_1', 65)
    widget.update({ targetEditingEnabled: false })
    expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Save light targets' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Discard light edits' })).toBeEnabled()
    let outcome: LightPendingSaveResult | undefined
    await act(async () => { outcome = await widget.ref.current!.savePendingChanges() })
    expect(outcome).toEqual({ savedDevices: [], failedDevices: ['light_1'] })
    expect(mocks.setLightIntensity).not.toHaveBeenCalled()
    widget.update({ targetEditingEnabled: true })
    expect(screen.getByRole('button', { name: 'Save light targets' })).toBeEnabled()
    await act(async () => { await widget.ref.current!.savePendingChanges() })
    expect(mocks.setLightIntensity).toHaveBeenCalledTimes(1)
  })
})

it('keeps an unconfirmed active mode read-only and performs no work for a clean save', async () => {
  const widget = mount({ activeModeId: null, targetEditingEnabled: false })
  await settle()
  expect(screen.getByRole('spinbutton', { name: 'light_1 light target' })).toBeDisabled()
  let outcome: LightPendingSaveResult | undefined
  await act(async () => { outcome = await widget.ref.current!.savePendingChanges() })
  expect(outcome).toEqual({ savedDevices: [], failedDevices: [] })
  expect(mocks.setLightIntensity).not.toHaveBeenCalled()
  expect(mocks.getZoneLightsStatus).toHaveBeenCalledTimes(1)
  expect(widget.onPendingChange).toHaveBeenLastCalledWith({ modeId: null, devices: [] })
})
