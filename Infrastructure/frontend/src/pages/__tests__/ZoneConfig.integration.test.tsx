import { forwardRef } from 'react'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZoneConfig from '../ZoneConfig'
import {
  TimelineConflictError,
  TimelineUnavailableError,
} from '../../features/climate-timeline/api/timelinePublicationPort'
import { RichTrajectoryEnvelope } from '../../features/climate-timeline/api/contracts'

const mocks = vi.hoisted(() => ({
  apiClient: {
    getRoomModeWithParams: vi.fn(),
    getClimatePeriods: vi.fn(),
    getSaved: vi.fn(),
    updateRoomParameters: vi.fn(),
    saveRoomSchedule: vi.fn(),
    saveClimatePeriods: vi.fn(),
    preview: vi.fn(),
    apply: vi.fn(),
  },
  setActions: vi.fn(),
}))

vi.mock('react-router-dom', () => ({ useParams: () => ({}) }))
vi.mock('../../services/api', () => ({ apiClient: mocks.apiClient }))
vi.mock('../../contexts/ControlActionsContext', () => ({
  useControlActions: () => ({ setActions: mocks.setActions }),
}))
vi.mock('../../hooks/useControlSnapshot', () => ({
  useControlSnapshot: () => ({ snapshot: null, mcpConnected: false }),
}))
vi.mock('../../components/LightIntensity', () => ({
  default: forwardRef(() => <div data-testid="light-intensity" />),
}))
vi.mock('../../components/ClimatePeriodTimeline', () => ({
  default: ({ className }: { className?: string }) => (
    <div data-testid="legacy-climate-period-timeline" className={className} />
  ),
}))
vi.mock('../../components/ManualLightControl', () => ({
  default: () => <div data-testid="manual-light-control" />,
}))
vi.mock('../../components/VerticalPIDBlock', () => ({
  default: () => <div data-testid="pid-block" />,
}))
vi.mock('../../components/VerticalNotesBlock', () => ({
  default: () => <div data-testid="notes-block" />,
}))
vi.mock('../../components/devices/RelayChannelMatrix', () => ({
  default: () => <div data-testid="relay-matrix" />,
}))
const mode = (modeName: string, isConstant: boolean) => ({
  location: 'Veg Room',
  cluster: 'main',
  mode_name: modeName,
  mode_id: 1,
  submode_id: null,
  is_constant: isConstant,
  parameters: {
    day_start_time: '06:00',
    night_start_time: '18:00',
    light_ramp_up_minutes: 20,
    light_ramp_down_minutes: 20,
    main_light_intensity: 80,
    supplemental_light_intensity: 10,
  },
})

const periods = [
  {
    period_name: 'Day cycle',
    start_time: '06:00',
    end_time: '18:00',
    ramp_minutes: 30,
    heating_setpoint: 24,
    cooling_setpoint: 28,
    vpd_setpoint: 1.1,
    co2_setpoint: 900,
    details: 'Fixture schedule',
  },
]

const saved = {
  room: { location: 'Veg Room', cluster: 'main' },
  baseConfigRevision: 'config-1',
  modeId: 1,
  submodeId: null,
  periods,
  photoperiod: {
    dayStartTime: '06:00',
    nightStartTime: '18:00',
    rampUpMinutes: 20,
    rampDownMinutes: 20,
  },
  window: {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    timezone: 'America/Toronto',
  },
}

const draftEnvelope = (draftRevision: number) => RichTrajectoryEnvelope.parse({
  contract_version: 1,
  room: 'Veg Room',
  generated_at: '2026-01-01T00:00:00.000Z',
  window: {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    timezone: 'America/Toronto',
  },
  revision_scope: 'draft',
  base_config_revision: 'config-1',
  draft_revision: `draft-${draftRevision}`,
  segments: [{
    shape: 'step',
    value: 22,
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-02T00:00:00.000Z',
    metric: 'temperature',
    unit: 'celsius',
    trajectory_kind: 'scheduled',
    quality: 'exact',
    source: {
      mode: 'veg',
      submode: null,
      period: { period_id: 'day', label: 'Day cycle' },
      config_revision: 'config-1',
      draft_revision: `draft-${draftRevision}`,
    },
  }],
  assumptions: [],
  warnings: [],
})

describe('ZoneConfig timeline classification integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.apiClient.getClimatePeriods.mockResolvedValue(periods)
    mocks.apiClient.getSaved.mockResolvedValue(saved)
    mocks.apiClient.updateRoomParameters.mockImplementation(async () => mode('veg', true))
    mocks.apiClient.preview.mockImplementation(async (request: { draftRevision: number }) => draftEnvelope(request.draftRevision))
    mocks.apiClient.apply.mockImplementation(async (request: {
      room: { location: string; cluster: string }
      values: { periods: typeof periods; photoperiod: unknown }
    }) => ({
      room: request.room,
      baseConfigRevision: 'config-2',
      periods: request.values.periods,
      photoperiod: request.values.photoperiod,
    }))
  })

  it('renders the saved timeline editor despite is_constant=true', async () => {
    // Given: the API reports a Veg mode with a contradictory constant flag.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('veg', true))

    // When: the ZoneConfig control surface loads.
    const rendered = render(<ZoneConfig location="Veg Room" cluster="main" />)

    // Then: canonical Veg behavior uses the saved timeline editor path.
    expect(await screen.findByRole('region', { name: 'Climate control timeline' })).toBeInTheDocument()
    expect(screen.getByText('READ ONLY')).toBeInTheDocument()
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.getByTestId('light-intensity')).toBeInTheDocument()
    expect(screen.getByTestId('relay-matrix')).toBeInTheDocument()
    expect(rendered.container.querySelector('[data-testid="legacy-climate-period-timeline"]')).not.toBeInTheDocument()
    expect(mocks.apiClient.getSaved).toHaveBeenCalledOnce()
  })

  it('renders the saved timeline editor for constant Sleep too', async () => {
    // Given: the API reports Sleep with a contradictory scheduled flag and no periods.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('sleep', false))
    mocks.apiClient.getClimatePeriods.mockResolvedValue([])

    // When: the ZoneConfig control surface loads.
    render(<ZoneConfig location="Veg Room" cluster="main" />)

    // Then: the saved timeline still loads and renders for the 24h constant mode.
    expect(await screen.findByRole('region', { name: 'Climate control timeline' })).toBeInTheDocument()
    expect(screen.getByText('READ ONLY')).toBeInTheDocument()
    expect(mocks.apiClient.getSaved).toHaveBeenCalledOnce()
    expect(screen.queryByText('Constant mode - no timeline')).not.toBeInTheDocument()
  })

  it('surfaces timeline_unavailable while retaining the fallback periods UI', async () => {
    // Given: room mode and legacy periods load, but the saved timeline is unavailable.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('flower', false))
    mocks.apiClient.getSaved.mockRejectedValue(
      new TimelineUnavailableError('saved schedule authority is unavailable')
    )

    // When: the ZoneConfig control surface loads.
    render(<ZoneConfig location="Flower Room" cluster="main" />)

    // Then: the fallback table remains usable and the unavailable authority is exposed.
    expect(await screen.findByRole('table')).toBeInTheDocument()
    expect(screen.queryByTestId('legacy-climate-period-timeline')).not.toBeInTheDocument()
    await waitFor(() =>
      expect(mocks.setActions.mock.calls.at(-1)?.[0]).toMatchObject({
        saveError: 'saved schedule authority is unavailable',
      })
    )
  })

  it('surfaces non-recoverable saved timeline failures', async () => {
    // Given: the saved timeline request fails with an ordinary server error.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('flower', false))
    mocks.apiClient.getSaved.mockRejectedValue(new Error('HTTP 500'))

    // When: the ZoneConfig control surface loads.
    render(<ZoneConfig location="Flower Room" cluster="main" />)

    // Then: the normal UI remains rendered and the failure is exposed to actions.
    await screen.findByRole('table')
    await waitFor(() =>
      expect(mocks.setActions.mock.calls.at(-1)?.[0]).toMatchObject({
        saveError: 'HTTP 500',
      })
    )
  })

  it('persists a dirty table edit through review and apply when the header Save runs', async () => {
    // Given: a timeline-backed room and its saved baseline.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('veg', true))
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const table = await screen.findByRole('table')

    // When: the operator edits the heating setpoint in the table, then invokes the header Save.
    const heatInput = within(table).getAllByPlaceholderText('°C')[0]
    fireEvent.change(heatInput, { target: { value: '23.5' } })
    const actions = mocks.setActions.mock.calls.at(-1)?.[0] as { onSave: () => Promise<void> }
    await actSave(actions.onSave)

    // Then: room parameters are saved and the draft reaches the apply endpoint.
    expect(mocks.apiClient.updateRoomParameters).toHaveBeenCalledOnce()
    expect(mocks.apiClient.preview).toHaveBeenCalledOnce()
    expect(mocks.apiClient.apply).toHaveBeenCalledOnce()
    const applyRequest = mocks.apiClient.apply.mock.calls[0]?.[0] as {
      values: { periods: Array<{ heating_setpoint: number | null }> }
    }
    expect(applyRequest.values.periods[0]?.heating_setpoint).toBe(23.5)
    expect(mocks.setActions.mock.calls.at(-1)?.[0]).toMatchObject({ saveError: null })

    // And: the applied baseline flows back into the table cell (draft → table direction).
    await waitFor(() =>
      expect(within(screen.getByRole('table')).getAllByPlaceholderText('°C')[0]).toHaveValue(23.5)
    )
  })

  it('keeps the draft and surfaces the conflict when header Save apply receives a 409', async () => {
    // Given: a timeline-backed room whose apply endpoint answers with a revision conflict.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('veg', true))
    mocks.apiClient.apply.mockRejectedValue(new TimelineConflictError())
    render(<ZoneConfig location="Veg Room" cluster="main" />)
    const table = await screen.findByRole('table')

    // When: the operator edits a setpoint and saves through the header.
    fireEvent.change(within(table).getAllByPlaceholderText('°C')[0], { target: { value: '23.5' } })
    const actions = mocks.setActions.mock.calls.at(-1)?.[0] as { onSave: () => Promise<void> }
    await actSave(actions.onSave)

    // Then: the table edit is preserved for another review/apply cycle and the failure is visible.
    expect(within(screen.getByRole('table')).getAllByPlaceholderText('°C')[0]).toHaveValue(23.5)
    expect(mocks.apiClient.preview).toHaveBeenCalledOnce()
    expect(mocks.setActions.mock.calls.at(-1)?.[0]).toMatchObject({
      saveError: expect.stringMatching(/conflict/i),
    })
  })

  it('keeps the legacy direct save when the timeline is unavailable', async () => {
    // Given: the saved timeline is unavailable so the page falls back to legacy table state.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('flower', false))
    mocks.apiClient.getSaved.mockRejectedValue(
      new TimelineUnavailableError('saved schedule authority is unavailable')
    )
    render(<ZoneConfig location="Flower Room" cluster="main" />)
    const table = await screen.findByRole('table')

    // When: the operator edits the table and saves through the header.
    fireEvent.change(within(table).getAllByPlaceholderText('°C')[0], { target: { value: '23.5' } })
    const actions = mocks.setActions.mock.calls.at(-1)?.[0] as { onSave: () => Promise<void> }
    await actSave(actions.onSave)

    // Then: the legacy periods endpoint carries the edit without any preview/apply traffic.
    expect(mocks.apiClient.saveClimatePeriods).toHaveBeenCalledOnce()
    const savedPeriods = mocks.apiClient.saveClimatePeriods.mock.calls[0]?.[2] as Array<{ heating_setpoint: number | null }>
    expect(savedPeriods[0]?.heating_setpoint).toBe(23.5)
    expect(mocks.apiClient.preview).not.toHaveBeenCalled()
    expect(mocks.apiClient.apply).not.toHaveBeenCalled()
  })
})

async function actSave(onSave: () => Promise<void>): Promise<void> {
  await waitFor(async () => {
    await onSave()
  })
}
