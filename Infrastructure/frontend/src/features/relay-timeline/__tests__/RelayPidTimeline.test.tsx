import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ControlSnapshotResponse } from '../../../services/api/devices'
import type { DeviceRegistryEntry } from '../../../types/device'
import { RelayTimelineResponse } from '../contracts'
import { RelayPidTimeline } from '../RelayPidTimeline'
import type { RelayTimelineChartHandle, RelayTimelineChartProps } from '../RelayTimelineChart'

const mocks = vi.hoisted(() => ({ relayTimeline: vi.fn() }))

vi.mock('../../monitoring/api', () => ({
  MonitoringApi: class {
    relayTimeline = mocks.relayTimeline
  },
  monitoringRequestContextFromSearchParams: () => undefined,
}))

// Vitest hoists mock factories before static imports; load React here to create a ref-capable canvas mock without mounting uPlot.
vi.mock('../RelayTimelineChart', async () => {
  const React = await import('react')
  return {
    RelayTimelineChart: React.forwardRef<RelayTimelineChartHandle, RelayTimelineChartProps>(
      function MockRelayTimelineChart(props) {
        return (
          <div data-testid="relay-timeline-chart">
            {props.lanes.map(lane => (
              <div key={lane.key} data-testid={`lane-${lane.deviceType}`}>
                <span>{lane.label}</span>
                <span>
                  {lane.preview ? 'example only; no hardware' : `relay ${lane.physicalRelay}`}
                </span>
                <span>
                  {lane.requestedOutput.map(segment => segment.requestedPercent).join(',')}
                </span>
                <span>
                  {lane.summary.onTransitions} ON changes / {lane.summary.offTransitions} OFF
                  changes
                </span>
              </div>
            ))}
          </div>
        )
      }
    ),
  }
})

const SESSION = 'b8a8be48-8ee7-4f10-9d5c-7a63f9f0a001'
const heater: DeviceRegistryEntry = {
  device_id: 101,
  device_type: 'heating',
  device_name: 'veg_heater',
  display_name: 'Veg Heater',
  location: 'Veg Room',
  cluster: 'main',
  channel: 0,
  pid_enabled: false,
}
const light: DeviceRegistryEntry = {
  device_id: 102,
  device_type: 'light',
  device_name: 'veg_light',
  display_name: 'Grow Light',
  location: 'Veg Room',
  cluster: 'main',
  relay_channel: 1,
}

function controlSnapshot(sampledAt: string): ControlSnapshotResponse {
  return {
    generated_at: sampledAt,
    sampled_at: sampledAt,
    freshness: 'FRESH',
    registry_version: 1,
    stale_since: null,
    dfr_boards: [],
    failsafes: [],
    relays: Array.from({ length: 16 }, (_, channel) => ({
      alarm: null,
      assignment:
        channel === 0
          ? {
              device_id: 101,
              device_name: 'veg_heater',
              device_type: 'heating',
              display_name: 'Veg Heater',
              location: 'Veg Room',
              cluster: 'main',
              inherited_schedule_count: 0,
              inherited_schedule_summary: null,
            }
          : channel === 1
            ? {
                device_id: 102,
                device_name: 'veg_light',
                device_type: 'light',
                display_name: 'Grow Light',
                location: 'Veg Room',
                cluster: 'main',
                inherited_schedule_count: 0,
                inherited_schedule_summary: null,
              }
            : null,
      changed_at: sampledAt,
      channel,
      command_expires_at: null,
      command_mode: 'scheduled',
      desired_state: null,
      last_command_succeeded: null,
      observed_state: channel === 0,
      physical_relay: channel === 0 ? 8 : channel + 1,
      interlock_blocked: false,
      interlock_reason: null,
      pin_label: `GPA${channel}`,
      prior_command_mode: null,
      recovery_pending: false,
      stale: false,
      syncing: false,
    })),
    hardware_alarms: [],
  } as ControlSnapshotResponse
}

function timelinePage(start: string, end: string, watermark = 42) {
  const startMs = Date.parse(start)
  const endMs = Date.parse(end)
  return RelayTimelineResponse.parse({
    range: { start, end },
    transitions: [
      {
        observation_id: 3,
        observed_at: new Date(endMs - 5_000).toISOString(),
        channel: null,
        observed_state: null,
        reason: 'heartbeat',
        session_id: SESSION,
        registry_version: 1,
        device_id: null,
        device_name: null,
        device_type: null,
        location: null,
        cluster: null,
      },
    ],
    anchors: [
      {
        observation_id: 1,
        observed_at: new Date(startMs - 10_000).toISOString(),
        channel: 0,
        observed_state: true,
        reason: 'initial',
        session_id: SESSION,
        registry_version: 1,
        device_id: 101,
        device_name: 'veg_heater',
        device_type: 'heating',
        location: 'Veg Room',
        cluster: 'main',
      },
      {
        observation_id: 2,
        observed_at: new Date(startMs - 5_000).toISOString(),
        channel: null,
        observed_state: null,
        reason: 'heartbeat',
        session_id: SESSION,
        registry_version: 1,
        device_id: null,
        device_name: null,
        device_type: null,
        location: null,
        cluster: null,
      },
    ],
    load: [
      {
        device_id: 101,
        device_name: 'veg_heater',
        timestamp: new Date(startMs + 20_000).toISOString(),
        requested_percent: 50,
        aggregated: false,
        interval_seconds: 60,
      },
    ],
    coverage_complete: true,
    last_heartbeat_at: new Date(endMs - 5_000).toISOString(),
    watermark,
    has_more: false,
    next_cursor: null,
  })
}

function renderTimeline(
  snapshot: ControlSnapshotResponse | null,
  registry: DeviceRegistryEntry[] = [heater, light],
  snapshotLoading = false
) {
  return render(
    <MemoryRouter
      initialEntries={[
        '/vegetation/automation?scenario=relay-pid-timeline&fixtureSession=timeline-test',
      ]}
    >
      <RelayPidTimeline
        location="Veg Room"
        cluster="main"
        registry={registry}
        snapshot={snapshot}
        snapshotLoading={snapshotLoading}
      />
    </MemoryRouter>
  )
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(accept => {
    resolve = accept
  })
  return { promise, resolve }
}

describe('RelayPidTimeline integration', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.relayTimeline.mockImplementation(async (_location: string, start: string, end: string) =>
      timelinePage(start, end)
    )
  })

  it('renders only matching assigned non-light lanes and preserves the backend physical relay label', async () => {
    const sampledAt = new Date().toISOString()
    renderTimeline(controlSnapshot(sampledAt))

    const chart = await screen.findByTestId('relay-timeline-chart')
    expect(within(chart).getByTestId('lane-heating')).toBeInTheDocument()
    expect(within(chart).queryByTestId('lane-light')).not.toBeInTheDocument()
    expect(within(chart).getByText('Veg Heater')).toBeInTheDocument()
    expect(within(chart).getByText('relay 8')).toBeInTheDocument()
    expect(within(chart).getByText('50')).toBeInTheDocument()
    const latestOutput = screen.getByRole('group', { name: 'Latest requested PID output' })
    expect(within(latestOutput).getByText('50%')).toBeInTheDocument()
    expect(within(latestOutput).getByText(/Stale/)).toBeInTheDocument()
    expect(within(chart).queryByText('Grow Light')).not.toBeInTheDocument()
    expect(mocks.relayTimeline).toHaveBeenCalledWith(
      'Veg Room',
      expect.any(String),
      expect.any(String),
      2_000,
      null,
      expect.objectContaining({ signal: expect.any(AbortSignal) })
    )
  })

  it('shows the freshest requested PID percent as a quick-read value', async () => {
    mocks.relayTimeline.mockImplementationOnce(
      async (_location: string, start: string, end: string) => ({
        ...timelinePage(start, end),
        load: [
          {
            device_id: 101,
            device_name: 'veg_heater',
            timestamp: new Date(Date.parse(end) - 1_000),
            requested_percent: 42,
            aggregated: false,
            interval_seconds: 5,
          },
        ],
      })
    )
    renderTimeline(controlSnapshot(new Date().toISOString()))

    const latestOutput = await screen.findByRole('group', { name: 'Latest requested PID output' })
    expect(within(latestOutput).getByText('42%')).toBeInTheDocument()
    expect(within(latestOutput).getByText(/Fresh/)).toBeInTheDocument()
    expect(within(latestOutput).getByText(/raw, 5.0 s interval/)).toBeInTheDocument()
  })

  it('keeps the assigned lane but draws no waveform when requested PID output is absent', async () => {
    mocks.relayTimeline.mockImplementationOnce(
      async (_location: string, start: string, end: string) => ({
        ...timelinePage(start, end),
        load: [],
      })
    )
    renderTimeline(controlSnapshot(new Date().toISOString()))

    expect(
      await screen.findByText(/No requested PID output samples are recorded/)
    ).toBeInTheDocument()
    expect(
      within(screen.getByTestId('relay-timeline-chart')).queryByText('50')
    ).not.toBeInTheDocument()
  })
  it('does not reuse a prior PID percent after a null sample', async () => {
    mocks.relayTimeline.mockImplementationOnce(
      async (_location: string, start: string, end: string) => ({
        ...timelinePage(start, end),
        load: [
          {
            device_id: 101,
            device_name: 'veg_heater',
            timestamp: new Date(Date.parse(end) - 2_000),
            requested_percent: 75,
            aggregated: false,
            interval_seconds: 1,
          },
          {
            device_id: 101,
            device_name: 'veg_heater',
            timestamp: new Date(Date.parse(end) - 500),
            requested_percent: null,
            aggregated: false,
            interval_seconds: 1,
          },
        ],
      })
    )
    renderTimeline(controlSnapshot(new Date().toISOString()))

    const latestOutput = await screen.findByRole('group', { name: 'Latest requested PID output' })
    expect(within(latestOutput).getByText('Unavailable')).toBeInTheDocument()
    expect(within(latestOutput).queryByText('75%')).not.toBeInTheDocument()
  })

  it('shows an explicitly synthetic example lane when assignments are absent', async () => {
    renderTimeline(controlSnapshot(new Date().toISOString()), [])

    expect(
      await screen.findByText(/No assigned non-light relays are available/)
    ).toBeInTheDocument()
    const disableButton = screen.getByRole('button', { name: 'Disable beta synthetic example' })
    expect(disableButton).toHaveAttribute('aria-pressed', 'true')
    const preview = screen.getByRole('group', { name: 'Illustrative relay timeline preview' })
    const previewChart = within(preview).getByTestId('relay-timeline-chart')
    expect(within(previewChart).getByTestId('lane-heating')).toHaveTextContent('Heating relay')
    expect(within(previewChart).getByText('example only; no hardware')).toBeInTheDocument()
    expect(within(previewChart).getByText('25,50,75,35')).toBeInTheDocument()
    expect(
      within(preview).getByText(/Example requested PID output at the range end/)
    ).toHaveTextContent('35%')
    expect(
      within(preview).getByText(
        /not assigned hardware, a relay observation, or recorded PID output/
      )
    ).toBeInTheDocument()

    fireEvent.click(disableButton)
    expect(
      screen.queryByRole('group', { name: 'Illustrative relay timeline preview' })
    ).not.toBeInTheDocument()
    const enableButton = screen.getByRole('button', { name: 'Enable beta synthetic example' })
    expect(enableButton).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(enableButton)
    expect(
      screen.getByRole('group', { name: 'Illustrative relay timeline preview' })
    ).toBeInTheDocument()
  })

  it('does not show the example lane while assignments are still loading', () => {
    renderTimeline(null, [], true)

    expect(screen.getByText(/Loading current relay assignments/)).toBeInTheDocument()
    expect(
      screen.queryByRole('group', { name: 'Illustrative relay timeline preview' })
    ).not.toBeInTheDocument()
  })

  it('refreshes the requested live preset range and ignores a stale response after range change', async () => {
    const firstRequest = deferred<RelayTimelineResponse>()
    mocks.relayTimeline
      .mockImplementationOnce(() => firstRequest.promise)
      .mockImplementationOnce(async (_location: string, start: string, end: string) =>
        timelinePage(start, end, 222)
      )
    const sampledAt = new Date().toISOString()
    renderTimeline(controlSnapshot(sampledAt))

    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(1))
    fireEvent.click(screen.getByRole('button', { name: '3h' }))
    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(2))

    const secondRequest = mocks.relayTimeline.mock.calls[1]
    const secondStart = Date.parse(String(secondRequest?.[1]))
    const secondEnd = Date.parse(String(secondRequest?.[2]))
    expect(secondEnd - secondStart).toBe(3 * 60 * 60 * 1_000)
    expect(await screen.findByText(/Observation watermark 222/)).toBeInTheDocument()

    const firstCall = mocks.relayTimeline.mock.calls[0]
    await act(async () => {
      firstRequest.resolve(timelinePage(String(firstCall?.[1]), String(firstCall?.[2]), 111))
      await firstRequest.promise
    })
    expect(screen.getByText(/Observation watermark 222/)).toBeInTheDocument()
    expect(screen.queryByText(/Observation watermark 111/)).not.toBeInTheDocument()
  })

  it('marks the first recorded baseline as a history boundary', async () => {
    mocks.relayTimeline.mockImplementationOnce(
      async (_location: string, start: string, end: string) => {
        const page = timelinePage(start, end)
        const initial = {
          observation_id: 4,
          observed_at: new Date(Date.parse(start) + 20_000),
          channel: 0,
          observed_state: true,
          reason: 'initial' as const,
          session_id: SESSION,
          registry_version: 1,
          device_id: 101,
          device_name: 'veg_heater',
          device_type: 'heating',
          location: 'Veg Room',
          cluster: 'main',
        }
        return {
          ...page,
          transitions: [...page.transitions, initial].sort(
            (left, right) => left.observed_at.getTime() - right.observed_at.getTime()
          ),
          anchors: page.anchors.filter(anchor => anchor.reason === 'heartbeat'),
          coverage_complete: false,
        }
      }
    )
    renderTimeline(controlSnapshot(new Date().toISOString()))

    expect(await screen.findByText(/History begins here/)).toBeInTheDocument()
  })

  it('pauses and resumes live requests without losing the selected live range', async () => {
    renderTimeline(controlSnapshot(new Date().toISOString()))
    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(1))

    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(screen.getByText('PAUSED')).toBeInTheDocument()
    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(2))

    fireEvent.click(screen.getByRole('button', { name: 'Resume' }))
    expect(screen.getByText('LIVE')).toBeInTheDocument()
    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(3))
  })

  it('applies a custom Toronto wall-time range as a fixed query window', async () => {
    renderTimeline(controlSnapshot(new Date().toISOString()))
    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(1))

    fireEvent.change(screen.getByLabelText('Range start'), {
      target: { value: '2026-09-24T12:00' },
    })
    fireEvent.change(screen.getByLabelText('Range end'), {
      target: { value: '2026-09-24T12:10' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Apply fixed range' }))

    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(2))
    const request = mocks.relayTimeline.mock.calls[1]
    expect(Date.parse(String(request?.[2])) - Date.parse(String(request?.[1]))).toBe(10 * 60_000)
    expect(screen.getByText('FIXED')).toBeInTheDocument()
  })

  it('follows transition cursors and combines later pages without replacing first-page context', async () => {
    mocks.relayTimeline
      .mockImplementationOnce(async (_location: string, start: string, end: string) => {
        const page = timelinePage(start, end)
        return {
          ...page,
          transitions: [
            {
              observation_id: 5,
              observed_at: new Date(Date.parse(start) + 30_000),
              channel: 0,
              observed_state: false,
              reason: 'state_changed',
              session_id: SESSION,
              registry_version: 1,
              device_id: 101,
              device_name: 'veg_heater',
              device_type: 'heating',
              location: 'Veg Room',
              cluster: 'main',
            },
          ],
          has_more: true,
          next_cursor: 'fixture-page-2',
        }
      })
      .mockImplementationOnce(async (_location: string, start: string, end: string) => {
        const page = timelinePage(start, end)
        return {
          ...page,
          transitions: [
            {
              observation_id: 6,
              observed_at: new Date(Date.parse(start) + 60_000),
              channel: 0,
              observed_state: true,
              reason: 'state_changed',
              session_id: SESSION,
              registry_version: 1,
              device_id: 101,
              device_name: 'veg_heater',
              device_type: 'heating',
              location: 'Veg Room',
              cluster: 'main',
            },
            { ...page.transitions[0], observation_id: 7 },
          ],
          anchors: [],
          load: [],
          has_more: false,
          next_cursor: null,
        }
      })
    renderTimeline(controlSnapshot(new Date().toISOString()))

    await waitFor(() => expect(mocks.relayTimeline).toHaveBeenCalledTimes(2))
    expect(mocks.relayTimeline.mock.calls[1]?.[4]).toBe('fixture-page-2')
    expect(await screen.findByText(/1 ON changes \/ 1 OFF changes/)).toBeInTheDocument()
    expect(screen.getByText(/Observation watermark 42/)).toBeInTheDocument()
  })
})
