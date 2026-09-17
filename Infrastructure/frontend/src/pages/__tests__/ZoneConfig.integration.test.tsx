import { forwardRef } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ZoneConfig from '../ZoneConfig'
import { TimelineUnavailableError } from '../../features/climate-timeline/api/timelinePublicationPort'

const mocks = vi.hoisted(() => ({
  apiClient: {
    getRoomModeWithParams: vi.fn(),
    getClimatePeriods: vi.fn(),
    getSaved: vi.fn(),
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
vi.mock('../../components/ClimatePeriodsTable', () => ({
  default: ({ periods }: { periods: Array<{ period_name: string }> }) => (
    <table data-testid="climate-periods-table">
      <tbody><tr><td>{periods.map(period => period.period_name).join(', ')}</td></tr></tbody>
    </table>
  ),
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
  periods,
  photoperiod: {
    dayStartTime: '06:00',
    nightStartTime: '18:00',
    rampUpMinutes: 20,
    rampDownMinutes: 20,
  },
}

describe('ZoneConfig timeline classification integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.apiClient.getClimatePeriods.mockResolvedValue(periods)
    mocks.apiClient.getSaved.mockResolvedValue(saved)
  })

  it('renders the saved timeline editor despite is_constant=true', async () => {
    // Given: the API reports a Veg mode with a contradictory constant flag.
    mocks.apiClient.getRoomModeWithParams.mockResolvedValue(mode('veg', true))

    // When: the ZoneConfig control surface loads.
    const rendered = render(<ZoneConfig location="Veg Room" cluster="main" />)

    // Then: canonical Veg behavior uses the saved timeline editor path.
    expect(await screen.findByRole('region', { name: 'Climate control timeline' })).toBeInTheDocument()
    expect(screen.getByText('READ ONLY')).toBeInTheDocument()
    expect(await screen.findByTestId('climate-periods-table')).toBeInTheDocument()
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
})
