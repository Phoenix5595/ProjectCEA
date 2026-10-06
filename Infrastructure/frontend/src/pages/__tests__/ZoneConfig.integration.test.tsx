import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { forwardRef } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ControlActions } from '../../contexts/ControlActionsContext'
import type { TimelineSavedRequest } from '../../features/climate-timeline/api/timeline'
import { TimelineConflictError, TimelineUnavailableError, type TimelinePreviewRequest } from '../../features/climate-timeline/api/timelinePublicationPort'
import type { TimelineSavedBaseline } from '../../features/climate-timeline/state/timelineDraft'
import ZoneConfig from '../ZoneConfig'

const mocks = vi.hoisted(() => ({
  apiClient: {
    getRoomModes: vi.fn(), getFlowerSubmodes: vi.fn(), getActiveRoomMode: vi.fn(),
    getClimatePeriods: vi.fn(), getSaved: vi.fn(), getConfiguration: vi.fn(),
    preview: vi.fn(), apply: vi.fn(), setRoomMode: vi.fn(),
  },
  actions: {} as ControlActions,
  profiles: new Map<string, TimelineSavedBaseline>(),
  active: new Map<string, { modeId: number; submodeId: number | null }>(),
  cursor: 37,
  mutations: [] as Array<{ kind: 'apply' | 'activate'; modeId: number; revision: string }>,
}))
vi.mock('react-router-dom', () => ({ useParams: () => ({}) }))
vi.mock('../../services/api', () => ({ apiClient: mocks.apiClient }))
vi.mock('../../contexts/ControlActionsContext', () => ({ useControlActions: () => ({ setActions: registerActions }) }))
function registerActions(actions: ControlActions) { mocks.actions = actions }
vi.mock('../../hooks/useControlSnapshot', () => ({ useControlSnapshot: () => ({
  snapshot: null, registry: [], mcpConnected: false, loading: false, registryVersion: null,
}) }))
vi.mock('../../components/LightIntensity', () => ({ default: forwardRef(function FixtureLight() { return <div /> }) }))
vi.mock('../../components/ManualLightControl', () => ({ default: () => <div data-testid="manual-safety-controls" /> }))
vi.mock('../../components/VerticalNotesBlock', () => ({ default: () => <div /> }))
vi.mock('../../components/devices/RelayChannelMatrix', () => ({ default: () => <div data-testid="relay-safety-controls" /> }))

const modes = [
  { id: 1, name: 'veg', is_constant: false, photoperiod_hours: 18 },
  { id: 2, name: 'flower', is_constant: false, photoperiod_hours: 12 },
  { id: 3, name: 'drying', is_constant: true, photoperiod_hours: 0 },
  { id: 4, name: 'sleep', is_constant: true, photoperiod_hours: 0 },
]
const submodes = [{ id: 10, name: 'stretch' }, { id: 11, name: 'bulk' }, { id: 12, name: 'ripen' }]
const periods = [
  { period_name: 'Day cycle', start_time: '06:00', end_time: '18:00', ramp_minutes: 30,
    heating_setpoint: 24, cooling_setpoint: 28, vpd_setpoint: 1.1, co2_setpoint: 900, details: 'saved day' },
  { period_name: 'Night cycle', start_time: '18:00', end_time: '06:00', ramp_minutes: 15,
    heating_setpoint: 19, cooling_setpoint: 24, vpd_setpoint: 0.8, co2_setpoint: 600, details: 'saved night' },
]
const photoperiod = { dayStartTime: '06:00', nightStartTime: '18:00', rampUpMinutes: 20, rampDownMinutes: 20 }
const key = (location: string, modeId: number, submodeId: number | null) => `${location}|${modeId}|${submodeId ?? 'none'}`
const revision = () => mocks.cursor.toString(16).padStart(7, '0')
function readProfile(request: TimelineSavedRequest): TimelineSavedBaseline {
  const stored = mocks.profiles.get(key(request.location, request.modeId, request.submodeId))
  return {
    ...stored,
    room: { location: request.location, cluster: request.cluster },
    modeId: request.modeId, submodeId: request.submodeId, baseConfigRevision: revision(),
    parametersConfigured: stored?.parametersConfigured ?? false,
    periods: structuredClone(stored?.periods ?? []),
    photoperiod: stored?.photoperiod ?? { dayStartTime: '00:00', nightStartTime: '00:00', rampUpMinutes: 15, rampDownMinutes: 15 },
    window: request.window,
  }
}
function seed(location: string, modeId: number, submodeId: number | null, heat: number) {
  mocks.profiles.set(key(location, modeId, submodeId), {
    room: { location, cluster: 'main' }, modeId, submodeId, baseConfigRevision: revision(), parametersConfigured: true,
    periods: modeId === 3 ? [{ ...periods[0]!, period_name: 'Drying', start_time: '00:00', end_time: '00:00', ramp_minutes: 0, heating_setpoint: heat }]
      : periods.map((period, index) => ({ ...period, heating_setpoint: index === 0 ? heat : period.heating_setpoint })),
    photoperiod: modeId === 3 ? { dayStartTime: '00:00', nightStartTime: '00:00', rampUpMinutes: 15, rampDownMinutes: 15 } : photoperiod,
  })
}
async function heatInput() {
  const table = screen.getByRole('table')
  return (await within(table).findAllByPlaceholderText('°C'))[0]!
}
async function runSave() { await act(async () => { await mocks.actions.onSave?.() }) }
async function inspect(mode: string, submode?: string) {
  await act(async () => { mocks.actions.onSelectProfile?.(mode, submode) })
  await waitFor(() => expect(mocks.actions.selectionLoading).toBe(false))
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.actions = {}
  mocks.profiles.clear()
  mocks.cursor = 37
  mocks.mutations = []
  mocks.active = new Map([
    ['Veg Room', { modeId: 1, submodeId: null }],
    ['Flower Room', { modeId: 2, submodeId: 11 }],
  ])
  seed('Veg Room', 1, null, 24)
  seed('Flower Room', 2, null, 21)
  seed('Flower Room', 2, 10, 20)
  seed('Flower Room', 2, 11, 22)
  seed('Flower Room', 3, null, 18)
  mocks.apiClient.getRoomModes.mockResolvedValue(modes)
  mocks.apiClient.getFlowerSubmodes.mockResolvedValue(submodes)
  mocks.apiClient.getActiveRoomMode.mockImplementation(async (location: string, cluster: string) => {
    const active = mocks.active.get(location)!
    return { location, cluster, mode_id: active.modeId, submode_id: active.submodeId,
      mode_name: modes.find(mode => mode.id === active.modeId)!.name,
      submode_name: submodes.find(mode => mode.id === active.submodeId)?.name ?? null }
  })
  mocks.apiClient.getSaved.mockImplementation(async (request: TimelineSavedRequest) => readProfile(request))
  mocks.apiClient.getConfiguration.mockImplementation(async (request: TimelineSavedRequest) => readProfile(request))
  mocks.apiClient.getClimatePeriods.mockResolvedValue(periods)
  mocks.apiClient.preview.mockImplementation(async (request: TimelinePreviewRequest) => {
    if (request.expectedConfigRevision !== revision()) throw new TimelineConflictError()
    return { requestId: request.requestId, expectedConfigRevision: request.expectedConfigRevision,
      draftRevision: request.draftRevision, modeId: request.modeId, submodeId: request.submodeId,
      window: request.window, trajectory: null }
  })
  mocks.apiClient.apply.mockImplementation(async (request: TimelinePreviewRequest) => {
    if (request.expectedConfigRevision !== revision()) throw new TimelineConflictError()
    mocks.cursor++
    const baseline: TimelineSavedBaseline = { ...structuredClone(request.values), room: request.room,
      modeId: request.modeId, submodeId: request.submodeId, baseConfigRevision: revision(), parametersConfigured: true, window: request.window }
    mocks.profiles.set(key(request.room.location, request.modeId, request.submodeId), baseline)
    mocks.mutations.push({ kind: 'apply', modeId: request.modeId, revision: revision() })
    return { baseline, warning: null }
  })
  mocks.apiClient.setRoomMode.mockImplementation(async (location: string, cluster: string, request: {
    mode_name: string; submode_name?: string; expected_config_revision: string
  }) => {
    if (request.expected_config_revision !== revision()) throw new TimelineConflictError()
    const modeId = modes.find(mode => mode.name === request.mode_name)!.id
    const submodeId = submodes.find(mode => mode.name === request.submode_name)?.id ?? null
    mocks.cursor++
    mocks.active.set(location, { modeId, submodeId })
    mocks.mutations.push({ kind: 'activate', modeId, revision: revision() })
    const photo = mocks.profiles.get(key(location, modeId, submodeId))!.photoperiod
    return { location, cluster, mode_id: modeId, submode_id: submodeId, mode_name: request.mode_name,
      submode_name: request.submode_name ?? null, is_constant: modeId >= 3, parameters: {
        day_start_time: photo.dayStartTime, night_start_time: photo.nightStartTime,
        light_ramp_up_minutes: photo.rampUpMinutes, light_ramp_down_minutes: photo.rampDownMinutes,
        main_light_intensity: 100, supplemental_light_intensity: 0,
      }, config_revision: revision(), runtime_ready: true }
  })
})

describe('ZoneConfig profile preparation consumers', () => {
  it('returns to the configured Flower submode instead of opening a NULL profile', async () => {
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    expect(await heatInput()).toHaveValue(22)
    await inspect('drying')
    expect(await heatInput()).toHaveValue(18)
    await inspect('flower')
    expect(await heatInput()).toHaveValue(22)
    expect(mocks.active.get('Flower Room')).toEqual({ modeId: 2, submodeId: 11 })
    expect(mocks.profiles.get(key('Flower Room', 2, null))?.periods[0]?.heating_setpoint).toBe(21)
    expect(mocks.mutations).toEqual([])
  })

  it('keeps the inspected Flower submode and draft when its parent chip is selected', async () => {
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    await heatInput()
    await inspect('flower', 'stretch')
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '23' } })
    await inspect('flower')
    expect(heat).toHaveValue(23)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(mocks.active.get('Flower Room')).toEqual({ modeId: 2, submodeId: 11 })
  })

  it('selects the first catalogue Flower submode when no Flower profile is configured', async () => {
    mocks.active.set('Flower Room', { modeId: 4, submodeId: null })
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    await heatInput()
    await inspect('flower')
    expect(await heatInput()).toHaveValue(20)
    expect(mocks.active.get('Flower Room')).toEqual({ modeId: 4, submodeId: null })
    expect(mocks.mutations).toEqual([])
  })

  it('keeps canonical Veg time editing despite a contradictory constant flag', async () => {
    mocks.apiClient.getRoomModes.mockResolvedValue(modes.map(mode => mode.id === 1 ? { ...mode, is_constant: true } : mode))
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const heat = await heatInput()
    expect(heat).toHaveValue(24)
    expect(within(screen.getByRole('table')).getAllByPlaceholderText('HH:MM')[0]).toBeEnabled()
    fireEvent.change(heat, { target: { value: '23.5' } })
    await runSave()
    expect(mocks.profiles.get(key('Veg Room', 1, null))?.periods[0]?.heating_setpoint).toBe(23.5)
  })

  it('initializes unconfigured canonical Sleep without read-side persistence and saves its value-only all-day row', async () => {
    mocks.active.set('Flower Room', { modeId: 4, submodeId: null })
    mocks.apiClient.getRoomModes.mockResolvedValue(modes.map(mode => mode.id === 4 ? { ...mode, is_constant: false } : mode))
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    const heat = await heatInput()
    expect(heat).toHaveValue(null)
    expect(mocks.profiles.has(key('Flower Room', 4, null))).toBe(false)
    for (const clock of within(screen.getByRole('table')).getAllByPlaceholderText('HH:MM')) expect(clock).toBeDisabled()
    fireEvent.change(heat, { target: { value: '21' } })
    await runSave()
    const stored = mocks.profiles.get(key('Flower Room', 4, null))!
    expect(stored.parametersConfigured).toBe(true)
    expect(stored.periods.map(period => [period.start_time, period.end_time, period.heating_setpoint])).toEqual([['00:00', '00:00', 21]])
  })

  it('can save through the configuration-only path when the selected trajectory is unavailable', async () => {
    mocks.apiClient.getSaved.mockRejectedValue(new TimelineUnavailableError('projection unavailable'))
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '23.5' } })
    await runSave()
    expect(mocks.profiles.get(key('Veg Room', 1, null))?.periods[0]?.heating_setpoint).toBe(23.5)
  })

  it('keeps legacy editing and safety controls usable but forbids persistence without profile metadata', async () => {
    mocks.apiClient.getSaved.mockRejectedValue(new Error('metadata unavailable'))
    mocks.apiClient.getConfiguration.mockRejectedValue(new Error('metadata unavailable'))
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '20' } })
    expect(heat).toHaveValue(20)
    expect(screen.getByTestId('relay-safety-controls')).toBeInTheDocument()
    await runSave()
    expect(mocks.mutations).toEqual([])
    expect(mocks.profiles.get(key('Veg Room', 1, null))?.periods[0]?.heating_setpoint).toBe(24)
  })

  it('does not commit when edits revert to the persisted values', async () => {
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '23' } })
    fireEvent.change(heat, { target: { value: '24' } })
    await runSave()
    expect(mocks.cursor).toBe(37)
    expect(mocks.mutations).toEqual([])
  })

  it('preserves a conflicting draft while the persisted profile and active identity remain unchanged', async () => {
    mocks.apiClient.apply.mockRejectedValue(new TimelineConflictError())
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '23.5' } })
    await runSave()
    expect(heat).toHaveValue(23.5)
    expect(mocks.actions.canSave).toBe(false)
    expect(mocks.profiles.get(key('Veg Room', 1, null))?.periods[0]?.heating_setpoint).toBe(24)
    expect(mocks.active.get('Veg Room')).toEqual({ modeId: 1, submodeId: null })
  })

  it('saves an inactive slice without activation, then activates only after its next dirty save commits', async () => {
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    await heatInput()
    await inspect('drying')
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '19' } })
    await runSave()
    expect(mocks.profiles.get(key('Flower Room', 3, null))?.periods[0]?.heating_setpoint).toBe(19)
    expect(mocks.profiles.get(key('Flower Room', 2, 11))?.periods[0]?.heating_setpoint).toBe(22)
    expect(mocks.active.get('Flower Room')).toEqual({ modeId: 2, submodeId: 11 })
    fireEvent.change(heat, { target: { value: '20' } })
    await act(async () => { await mocks.actions.onActivateSelected?.() })
    expect(mocks.mutations.map(mutation => mutation.kind)).toEqual(['apply', 'apply', 'activate'])
    expect(mocks.profiles.get(key('Flower Room', 3, null))?.periods[0]?.heating_setpoint).toBe(20)
    expect(mocks.active.get('Flower Room')).toEqual({ modeId: 3, submodeId: null })
  })

  it('never activates after a failed review and retains the inactive preparation', async () => {
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    await heatInput()
    await inspect('drying')
    const heat = await heatInput()
    fireEvent.change(heat, { target: { value: '19' } })
    mocks.apiClient.preview.mockRejectedValue(new TimelineConflictError())
    await act(async () => { await mocks.actions.onActivateSelected?.() })
    expect(heat).toHaveValue(19)
    expect(mocks.mutations).toEqual([])
    expect(mocks.active.get('Flower Room')).toEqual({ modeId: 2, submodeId: 11 })
  })
})
