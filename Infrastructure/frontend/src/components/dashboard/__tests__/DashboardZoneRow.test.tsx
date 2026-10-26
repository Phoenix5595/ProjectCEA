import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import type { Device } from '../../../types/device'
import { buildTrendMetric, type RoomSensorStatus, type TrendData } from '../dashboardStatus'
import { DashboardZoneRow } from '../DashboardZoneRow'
import {
  DASHBOARD_DEVICES,
  DASHBOARD_LIGHT_INTENSITIES,
} from '../../../../tests/monitoring/dashboardDeviceFixtures'

const missingMainStatuses: Record<string, RoomSensorStatus> = {
  main: {
    quality: 'missing',
    newestAgeMs: null,
    newestObservedAtMs: null,
    source: null,
    cluster: 'main',
  },
}

const missingFlowerStatuses: Record<string, RoomSensorStatus> = {
  front: {
    quality: 'missing',
    newestAgeMs: null,
    newestObservedAtMs: null,
    source: null,
    cluster: 'front',
  },
  back: {
    quality: 'missing',
    newestAgeMs: null,
    newestObservedAtMs: null,
    source: null,
    cluster: 'back',
  },
}

/** A fixed Toronto wall clock instant: 2026-09-23T16:00:00Z is 12:00 EDT. */
const OBSERVED_AT_MS = Date.parse('2026-09-23T16:00:00.000Z')

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
  const narrow = (index = 0) =>
    within(result.container.querySelectorAll<HTMLElement>('.dashboard-zone-row__narrow')[index])
  return { ...result, wide, narrow }
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

  it('renders climate mini section with a missing alert theme', () => {
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
    const climate = wide().getByTestId('climate-cluster-main')
    expect(climate).toHaveAttribute('data-alert', 'true')
    expect(climate).toHaveAttribute('title', 'No reading received')
    expect(climate).toHaveClass('border-status-danger', 'bg-status-danger/10')
  })

  it('shows one fresh, non-alerting climate cluster for an unsplit room', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      main: {
        quality: 'live',
        newestAgeMs: 2_000,
        newestObservedAtMs: OBSERVED_AT_MS,
        source: 'websocket',
        cluster: 'main',
      },
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

    const climate = wide().getByTestId('climate-cluster-main')
    expect(climate).toHaveAttribute('data-alert', 'false')
    expect(climate).toHaveAttribute(
      'title',
      'Last good reading 12:00:00 (Toronto) · 2s ago · WebSocket'
    )
    expect(wide().getAllByTestId(/^climate-cluster-/)).toHaveLength(1)
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
  it('keeps named light outputs and distinguishes unknown state from OFF in compact rows', () => {
    const devices = DASHBOARD_DEVICES.filter(device => device.location === 'Veg Room')
      .map((device, index) => ({
        ...device,
        state: index === 2 ? undefined as unknown as number : index === 1 ? 0 : 1,
      }))
    const { narrow } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={devices}
        sensorData={{ 'Veg Room_main_light_v_1_intensity': 80, 'Veg Room_main_light_v_2_intensity': 0 }}
        icon="🌱"
        sensorStatuses={missingMainStatuses}
      />
    )
    expect(narrow().getByText('Eyefinity Top')).toBeInTheDocument()
    expect(narrow().getByText('80%')).toBeInTheDocument()
    expect(narrow().getByText('Ridgetop Bottom Right').parentElement).toHaveTextContent('0%')
    expect(narrow().getByText('Ridgetop Bottom Left').parentElement).toHaveTextContent('--')
    expect(narrow().getByTitle('Reported light state: unknown.')).toHaveTextContent('?')
  })


  it('renders as a link to the canonical room page', () => {
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
    expect(link).toHaveAttribute('href', '/vegetation/control')
  })

  it('shows separate Front and Back freshness beside their own readings', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      front: {
        quality: 'stale',
        newestAgeMs: 46_000,
        newestObservedAtMs: OBSERVED_AT_MS - 46_000,
        source: 'poll',
        cluster: 'front',
      },
      back: {
        quality: 'live',
        newestAgeMs: 2_000,
        newestObservedAtMs: OBSERVED_AT_MS - 2_000,
        source: 'websocket',
        cluster: 'back',
      },
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

    const frontClimate = wide().getByTestId('climate-cluster-front')
    const backClimate = wide().getByTestId('climate-cluster-back')
    expect(frontClimate).toHaveTextContent('20.00°C')
    expect(frontClimate).not.toHaveTextContent('25.00°C')
    expect(backClimate).toHaveTextContent('25.00°C')
    expect(backClimate).not.toHaveTextContent('20.00°C')
    expect(frontClimate).toHaveAttribute('data-alert', 'true')
    expect(frontClimate).toHaveAttribute(
      'title',
      'Last good reading 11:59:14 (Toronto) · 46s ago · polling'
    )
    expect(backClimate).toHaveAttribute('data-alert', 'false')
    expect(backClimate).toHaveAttribute(
      'title',
      'Last good reading 11:59:58 (Toronto) · 2s ago · WebSocket'
    )
    expect(container.querySelector('[title="Sensor offline or missing"]')).toBeNull()
  })

  it('keeps a bad Front value separate from a live Back cluster', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      front: {
        quality: 'bad',
        newestAgeMs: null,
        newestObservedAtMs: null,
        source: null,
        cluster: 'front',
      },
      back: {
        quality: 'live',
        newestAgeMs: 2_000,
        newestObservedAtMs: OBSERVED_AT_MS - 2_000,
        source: 'websocket',
        cluster: 'back',
      },
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

    const frontClimate = wide().getByTestId('climate-cluster-front')
    expect(frontClimate).toHaveAttribute('data-alert', 'false')
    expect(frontClimate).toHaveAttribute('title', 'No valid reading received')
    expect(frontClimate).toHaveTextContent('⚠')
    expect(wide().getByTestId('climate-cluster-back')).toHaveAttribute('data-alert', 'false')
  })

  it('shows the missing alert for an empty Front without borrowing Back age or source', () => {
    const sensorStatuses: Record<string, RoomSensorStatus> = {
      front: {
        quality: 'missing',
        newestAgeMs: null,
        newestObservedAtMs: null,
        source: null,
        cluster: 'front',
      },
      back: {
        quality: 'live',
        newestAgeMs: 2_000,
        newestObservedAtMs: OBSERVED_AT_MS - 2_000,
        source: 'websocket',
        cluster: 'back',
      },
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

    const frontClimate = wide().getByTestId('climate-cluster-front')
    expect(frontClimate).toHaveAttribute('data-alert', 'true')
    expect(frontClimate).toHaveAttribute('title', 'No reading received')
    expect(frontClimate).not.toHaveTextContent('2s')
    expect(wide().getByTestId('climate-cluster-back')).toHaveAttribute('data-alert', 'false')
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
    expect(lab).toHaveAttribute('href', '/laboratory')
    expect(lab).not.toHaveTextContent(
      /Mode:|No scheduled transition|TEMP IN BAND|AUTO|Setpoints|Lab lights/
    )
  })

  it('keeps Lab limited to passive temperature readings and freshness', () => {
    renderWithRouter(
      <DashboardZoneRow
        location="Lab"
        cluster="main"
        devices={DASHBOARD_DEVICES}
        sensorData={{ Lab_main_lab_temp: 24.6, Lab_main_water_temperature: 19.5 }}
        icon="🧪"
        sensorStatuses={{
          main: {
            quality: 'live',
            newestAgeMs: 3_000,
            newestObservedAtMs: OBSERVED_AT_MS,
            source: 'poll',
            cluster: 'main',
          },
        }}
        trendData={{
          main: {
            temperature: buildTrendMetric('temperature', [
              { timestampMs: 0, value: 23 },
              { timestampMs: 60_000, value: 24.6 },
            ]),
          },
        }}
      />
    )
    const lab = screen.getByRole('link', { name: /Lab/ })
    for (const readings of within(lab).getAllByRole('group', { name: 'Lab sensor readings' })) {
      expect(readings).toHaveTextContent('T 24.60°C')
      expect(readings).toHaveTextContent('Water 19.50°C')
      expect(readings).toHaveAttribute('data-alert', 'false')
      expect(readings).toHaveAttribute(
        'title',
        'Last good reading 12:00:00 (Toronto) · 3s ago · polling'
      )
    }
    expect(lab).not.toHaveTextContent(/Mode:|Decision|AUTO|Setpoints|Lights|CO₂|VPD|RH|Δ10m/)
    expect(lab.querySelector('[title*="trailing 60-minute sensor history"]')).not.toBeInTheDocument()
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

    expect(wide().getByTestId('climate-cluster-main')).toHaveAttribute(
      'title',
      'No reading received'
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

  it('retains all six real grow-room light names and their percentages in both row variants', () => {
    const { wide, narrow } = renderWithRouter(
      <>
        <DashboardZoneRow location="Flower Room" cluster="main" devices={DASHBOARD_DEVICES}
          sensorData={DASHBOARD_LIGHT_INTENSITIES} icon="🌸"
          sensorStatuses={missingFlowerStatuses} />
        <DashboardZoneRow location="Veg Room" cluster="main" devices={DASHBOARD_DEVICES}
          sensorData={DASHBOARD_LIGHT_INTENSITIES} icon="🌱"
          sensorStatuses={missingMainStatuses} />
      </>
    )
    for (const variant of [wide, narrow]) {
      for (const name of ['Chilled Front', 'Apache', 'Chilled Back']) {
        expect(variant(0).getByText(name).parentElement).toHaveTextContent('0%')
      }
      expect(variant(1).getByText('Eyefinity Top').parentElement).toHaveTextContent('80%')
      expect(variant(1).getByText('Ridgetop Bottom Right').parentElement).toHaveTextContent('40%')
      expect(variant(1).getByText('Ridgetop Bottom Left').parentElement).toHaveTextContent('40%')
    }
  })

  it('uses the newer status intensity instead of a retained bulk snapshot, including zero', () => {
    const { wide, narrow } = renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[DASHBOARD_DEVICES[3]]}
        sensorData={{ 'Veg Room_main_light_v_1_intensity': 80 }}
        statusDevices={{ 'Veg Room': { main: { light_v_1: { intensity: 0 } } } }}
        icon="🌱" sensorStatuses={missingMainStatuses} />
    )
    expect(wide().getByText('Eyefinity Top').parentElement).toHaveTextContent('0%')
    expect(narrow().getByText('Eyefinity Top').parentElement).toHaveTextContent('0%')
  })

  it('displays the configured Veg canopy sensor names rather than empty climate fields', () => {
    const { wide } = renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[]}
        sensorData={{ 'Veg Room_main_dry_bulb_v': 27.4,
          'Veg Room_main_rh_v': 70, 'Veg Room_main_co2_v': 1100, 'Veg Room_main_vpd_v': 1.3 }}
        icon="🌱" sensorStatuses={missingMainStatuses} />
    )
    expect(wide().getByText('27.40°C')).toBeInTheDocument()
    expect(wide().getByText('70.00%')).toBeInTheDocument()
    expect(wide().getByText('1100 ppm')).toBeInTheDocument()
    expect(wide().getByText('1.30 kPa')).toBeInTheDocument()
  })

  it('keeps the Lights section visible in both grow-room rows while device data is absent', () => {
    const { wide, narrow } = renderWithRouter(
      <>
        <DashboardZoneRow location="Flower Room" cluster="main" devices={[]} sensorData={{}} icon="🌸" sensorStatuses={missingFlowerStatuses} />
        <DashboardZoneRow location="Veg Room" cluster="main" devices={[]} sensorData={{}} icon="🌱" sensorStatuses={missingMainStatuses} />
      </>
    )
    for (const variant of [wide, narrow]) {
      expect(variant(0).getByText('Lights')).toBeInTheDocument()
      expect(variant(0).getByRole('status')).toBeInTheDocument()
      expect(variant(1).getByText('Lights')).toBeInTheDocument()
      expect(variant(1).getByRole('status')).toBeInTheDocument()
    }
  })

  it('appends the room-tied expected harvest to both mode lines', () => {
    const { wide, narrow } = renderWithRouter(
      <>
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
          expectedHarvestDate="2027-01-03"
        />
        <DashboardZoneRow
          location="Veg Room"
          cluster="main"
          devices={[]}
          sensorData={{}}
          icon="🌱"
          sensorStatuses={missingMainStatuses}
        />
      </>
    )

    expect(wide(0).getByText(/Harvest ~Jan 3/)).toHaveTextContent('Mode: Flower / Bulk')
    expect(narrow(0).getByText(/Harvest ~Jan 3/)).toHaveTextContent('Mode: Flower / Bulk')
    expect(screen.getAllByText(/Harvest ~Jan 3/)).toHaveLength(2)
    expect(wide(1).queryByText(/Harvest ~/)).not.toBeInTheDocument()
    expect(narrow(1).queryByText(/Harvest ~/)).not.toBeInTheDocument()
  })
})
