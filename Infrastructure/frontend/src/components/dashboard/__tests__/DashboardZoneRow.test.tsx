import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import type { Device } from '../../../types/device'
import { buildTrendMetric, type RoomSensorStatus, type TrendData } from '../dashboardStatus'
import { DashboardZoneRow } from '../DashboardZoneRow'

const missingMainStatuses: Record<string, RoomSensorStatus> = {
  main: { quality: 'missing', newestAgeMs: null, source: null, cluster: 'main' },
}

const missingFlowerStatuses: Record<string, RoomSensorStatus> = {
  front: { quality: 'missing', newestAgeMs: null, source: null, cluster: 'front' },
  back: { quality: 'missing', newestAgeMs: null, source: null, cluster: 'back' },
}

const makeDevice = (overrides: Partial<Device> = {}): Device => ({
  device_name: 'light_1',
  location: 'Veg Room',
  cluster: 'main',
  channel: 1,
  state: 0,
  mode: 'auto',
  ...overrides,
})

function renderWithRouter(ui: React.ReactElement) {
  const result = render(<MemoryRouter>{ui}</MemoryRouter>)
  const wide = (index = 0) =>
    within(result.container.querySelectorAll<HTMLElement>('.dashboard-zone-row__wide')[index])
  return { ...result, wide }
}

describe('DashboardZoneRow', () => {
  it('renders room name and icon', () => {
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(wide().getByText('Vegetation Room')).toBeInTheDocument()
    expect(wide().getByText('🌱')).toBeInTheDocument()
  })

  it('renders climate mini section', () => {
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(wide().getByText('Climate')).toBeInTheDocument()
    expect(wide().getByText('Climate NO DATA · — · —')).toBeInTheDocument()
  })

  it('shows one freshness badge for an unsplit room', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      main: { quality: 'live', newestAgeMs: 2_000, source: 'websocket', cluster: 'main' },
    }
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{ 'Veg Room_main_dry_bulb': 21 }}
        icon="🌱"
        sensorStatuses={sensorStatuses}
      />
    )

    expect(wide().getByText('Climate LIVE · 2s · WS')).toBeInTheDocument()
    expect(wide().getAllByText(/Climate (?:LIVE|STALE|BAD VALUE|NO DATA)/)).toHaveLength(1)
  })

  it('renders setpoints section', () => {
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(wide().getByText('Setpoints')).toBeInTheDocument()
  })

  it('renders light devices when present', () => {
    const devices = [makeDevice({ device_name: 'light_1', state: 1 })]
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={devices}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(wide().getByText('Lights')).toBeInTheDocument()
  })

  it('renders non-light devices when present', () => {
    const devices = [makeDevice({ device_name: 'fan_1', state: 1 })]
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={devices}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(wide().getByText('Devices')).toBeInTheDocument()
    expect(wide().getByText('fan_1')).toBeInTheDocument()
  })
  it('counts registered device states without treating unknown states as OFF', () => {
    const devices = [
      makeDevice({ device_name: 'light_on', state: 1 }),
      makeDevice({ device_name: 'fan_off', state: 0 }),
      makeDevice({ device_name: 'pump_missing', state: undefined as unknown as number }),
      makeDevice({ device_name: 'relay_unrecognized', state: 2 }),
    ]
    renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={devices}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )

    expect(screen.getByText('1 ON · 1 OFF · 2 unknown')).toBeInTheDocument()
  })

  it('does not render recent on/off section', () => {
    renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(screen.queryByText('Recent on/off')).not.toBeInTheDocument()
  })

  it('renders as a link to zone page', () => {
    const { container } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    const link = container.querySelector('a')
    expect(link).toHaveAttribute('href', '/zone/Veg%20Room/main')
  })

  it('shows separate Front and Back freshness beside their own readings', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      front: { quality: 'stale', newestAgeMs: 46_000, source: 'poll', cluster: 'front' },
      back: { quality: 'live', newestAgeMs: 2_000, source: 'websocket', cluster: 'back' },
    }
    const { container, wide } = renderWithRouter(
      <DashboardZoneRow
        location="Flower Room"
        cluster="main"
        devices={[]}
        sensorData={{
          'Flower Room_front_dry_bulb_f': 20,
          'Flower Room_back_dry_bulb_b': 25,
        }}
        icon="🌸"
        sensorStatuses={sensorStatuses}
      />
    )

    const frontBadge = wide().getByText('Front STALE · 46s · POLL')
    const backBadge = wide().getByText('Back LIVE · 2s · WS')
    const frontClimate = frontBadge.closest('[title*="sensor readings"]')
    const backClimate = backBadge.closest('[title*="sensor readings"]')
    expect(frontClimate).toHaveTextContent('20.00°C')
    expect(frontClimate).not.toHaveTextContent('25.00°C')
    expect(backClimate).toHaveTextContent('25.00°C')
    expect(backClimate).not.toHaveTextContent('20.00°C')
    expect(frontBadge).toHaveAttribute(
      'title',
      expect.stringContaining(
        'Latest valid sample in front; not a claim that every metric is current.'
      )
    )
    expect(frontBadge).toHaveAttribute(
      'title',
      expect.stringContaining('Retained values are not treated as current')
    )
    expect(container.querySelector('[title="Sensor offline or missing"]')).toBeNull()
  })

  it('keeps a bad Front value separate from a live Back cluster', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      front: { quality: 'bad', newestAgeMs: null, source: null, cluster: 'front' },
      back: { quality: 'live', newestAgeMs: 2_000, source: 'websocket', cluster: 'back' },
    }
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Flower Room"
        cluster="main"
        devices={[]}
        sensorData={{ 'Flower Room_back_dry_bulb_b': 24 }}
        icon="🌸"
        sensorStatuses={sensorStatuses}
      />
    )

    expect(wide().getByText('Front BAD VALUE · — · —')).toHaveAttribute(
      'title',
      expect.stringContaining('none were valid finite numbers')
    )
    expect(wide().getByText('Back LIVE · 2s · WS')).toBeInTheDocument()
  })

  it('shows NO DATA for an empty Front without borrowing Back age or source', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      front: { quality: 'missing', newestAgeMs: null, source: null, cluster: 'front' },
      back: { quality: 'live', newestAgeMs: 2_000, source: 'websocket', cluster: 'back' },
    }
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Flower Room"
        cluster="main"
        devices={[]}
        sensorData={{ 'Flower Room_back_dry_bulb_b': 24 }}
        icon="🌸"
        sensorStatuses={sensorStatuses}
      />
    )

    const frontBadge = wide().getByText('Front NO DATA · — · —')
    expect(frontBadge).toHaveAttribute(
      'title',
      expect.stringContaining('NO DATA: no usable live samples are available for this cluster')
    )
    expect(frontBadge).not.toHaveTextContent('2s')
    expect(wide().getByText('Back LIVE · 2s · WS')).toBeInTheDocument()
    expect(screen.queryByTitle('Sensor offline or missing')).not.toBeInTheDocument()
  })

  it('uses database-backed grow mode names and submode labels', () => {
    const { wide } = renderWithRouter(
      <div>
        <DashboardZoneRow
          location="Flower Room"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🌸"
          sensorStatuses={missingFlowerStatuses}
          activeMode={{
            location: 'Flower Room',
            cluster: 'main',
            mode_name: 'sleep',
            submode_name: null,
          }}
        />
        <DashboardZoneRow
          location="Veg Room"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🌱"
          sensorStatuses={missingMainStatuses}
          activeMode={{
            location: 'Veg Room',
            cluster: 'main',
            mode_name: 'veg',
            submode_name: null,
          }}
        />
        <DashboardZoneRow
          location="Flower Room"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🌸"
          sensorStatuses={missingFlowerStatuses}
          activeMode={{
            location: 'Flower Room',
            cluster: 'main',
            mode_name: 'flower',
            submode_name: 'bulk',
          }}
        />
      </div>
    )

    expect(wide(0).getByText('Sleep')).toBeInTheDocument()
    expect(wide(1).getByText('Veg')).toBeInTheDocument()
    expect(wide(2).getByText('Flower / Bulk')).toBeInTheDocument()
    expect([0, 1, 2].flatMap(index => wide(index).queryAllByText(/Last known/))).toHaveLength(0)
    expect(wide(0).getByTitle('Database-backed active grow mode is Sleep.')).toBeInTheDocument()
  })

  it('retains grow-room mode fallbacks but omits grow-only content for Lab', () => {
    const { wide } = renderWithRouter(
      <div>
        <DashboardZoneRow
          location="Flower Room"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🌸"
          sensorStatuses={missingFlowerStatuses}
          activeMode={{
            location: 'Flower Room',
            cluster: 'main',
            mode_name: 'sleep',
            submode_name: null,
          }}
          modeError="request timed out"
        />
        <DashboardZoneRow
          location="Veg Room"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🌱"
          sensorStatuses={missingMainStatuses}
          modeError="invalid response"
        />
        <DashboardZoneRow
          location="Lab"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🧪"
          sensorStatuses={missingMainStatuses}
          activeMode={{
            location: 'Lab',
            cluster: 'main',
            mode_name: 'sleep',
            submode_name: null,
          }}
          modeError="unexpected Lab request"
          nextTransition={{
            at: new Date('2026-09-23T16:00:00Z'),
            label: 'Lab lights',
            kind: 'start',
            deviceName: 'light_1',
          }}
        />
      </div>
    )

    expect(wide(0).getByText('Last known Sleep')).toBeInTheDocument()
    expect(
      wide(0).getByTitle(/Database-backed grow mode refresh failed: request timed out/)
    ).toHaveTextContent('Mode: Last known Sleep')
    expect(
      wide(1).getByTitle(/Database-backed grow mode unavailable: invalid response/)
    ).toHaveTextContent('Mode: —')

    const lab = screen.getByRole('link', { name: /Lab/ })
    expect(lab).toHaveAttribute('href', '/zone/Lab/main')
    expect(lab).toHaveTextContent('0 ON · 0 OFF · 0 unknown')
    expect(lab).not.toHaveTextContent(
      /Mode:|No scheduled transition|TEMP IN BAND|AUTO|Setpoints|Lab lights/
    )
  })

  it('keeps live Lab readings, device states, freshness, and trend text', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      main: { quality: 'live', newestAgeMs: 3_000, source: 'poll', cluster: 'main' },
    }
    const nowMs = Date.parse('2026-09-23T16:00:00Z')
    const trendData: TrendData = {
      main: {
        temperature: buildTrendMetric('temperature', [
          { timestampMs: nowMs - 60 * 60_000, value: 20 },
          { timestampMs: nowMs, value: 21 },
        ]),
      },
    }
    renderWithRouter(
      <DashboardZoneRow
        location="Lab"
        cluster="main"
        devices={[makeDevice({ location: 'Lab', device_name: 'fan_1', state: 1 })]}
        sensorData={{
          Lab_main_dry_bulb: 21,
          Lab_main_relative_humidity: 55,
          Lab_main_co2: 900,
          Lab_main_vpd: 1.2,
        }}
        icon="🧪"
        sensorStatuses={sensorStatuses}
        trendData={trendData}
        now={new Date(nowMs)}
      />
    )

    const lab = screen.getByRole('link', { name: /Lab/ })
    expect(lab).toHaveAttribute('href', '/zone/Lab/main')
    expect(lab).toHaveTextContent('Climate LIVE · 3s · POLL')
    expect(lab).toHaveTextContent('21.00°C')
    expect(lab).toHaveTextContent('55.00%')
    expect(lab).toHaveTextContent('900 ppm')
    expect(lab).toHaveTextContent('1.20 kPa')
    expect(lab).toHaveTextContent('fan_1')
    expect(lab).toHaveTextContent('1 ON · 0 OFF · 0 unknown')
    expect(lab).toHaveTextContent('Temperature')
    expect(lab).toHaveTextContent('Δ10m +1.00 °C')
    expect(lab.querySelector('[title*="trailing 60-minute sensor history"]')).toBeInTheDocument()
    expect(lab).not.toHaveTextContent(/Mode:|Decision|AUTO|Setpoints/)
  })

  it('humanizes unknown active mode and submode names', () => {
    const { wide } = renderWithRouter(
      <DashboardZoneRow
        location="Flower Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌸"
        sensorStatuses={missingFlowerStatuses}
        activeMode={{
          location: 'Flower Room',
          cluster: 'main',
          mode_name: 'early_flower_cycle',
          submode_name: 'late_bulk_phase',
        }}
      />
    )

    expect(wide().getByText('Early flower cycle / Late bulk phase')).toBeInTheDocument()
  })

  it('explains freshness, decision, authority, schedules, and trends on hover', () => {
    const nowMs = Date.parse('2026-09-23T16:00:00Z')
    const trendData: TrendData = {
      main: {
        temperature: buildTrendMetric('temperature', [
          { timestampMs: nowMs - 60 * 60_000, value: 20 },
          { timestampMs: nowMs, value: 21 },
        ]),
      },
    }
    const { container, wide } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
        trendData={trendData}
        now={new Date(nowMs)}
      />
    )

    expect(wide().getByText('Climate NO DATA · — · —')).toHaveAttribute(
      'title',
      expect.stringContaining('no usable live samples')
    )
    expect(wide().getByText('Mode:').closest('[title]')).toHaveAttribute(
      'title',
      expect.stringContaining('Database-backed active grow mode')
    )
    expect(wide().getByText('Decision').parentElement).toHaveAttribute(
      'title',
      expect.stringContaining('VPD and CO₂ deltas are informational')
    )
    expect(wide().getByText('AUTO')).toHaveAttribute(
      'title',
      expect.stringContaining('automatic control')
    )
    expect(wide().getByText('No scheduled transition').parentElement).toHaveAttribute(
      'title',
      expect.stringContaining('No enabled room schedule')
    )
    expect(
      container.querySelector('[title*="trailing 60-minute sensor history"]')
    ).toBeInTheDocument()
  })
})
