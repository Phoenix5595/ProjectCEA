import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { DashboardZoneRow } from '../DashboardZoneRow'
import type { Device } from '../../../types/device'
import { buildTrendMetric, type TrendData } from '../dashboardStatus'

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
  return render(<MemoryRouter>{ui}</MemoryRouter>)
}

describe('DashboardZoneRow', () => {
  it('renders room name and icon', () => {
    renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[]} sensorData={{}} icon="🌱" />
    )
    expect(screen.getByText('Vegetation Room')).toBeInTheDocument()
    expect(screen.getByText('🌱')).toBeInTheDocument()
  })

  it('renders climate mini section', () => {
    renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[]} sensorData={{}} icon="🌱" />
    )
    expect(screen.getByText('Climate')).toBeInTheDocument()
  })

  it('renders setpoints section', () => {
    renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[]} sensorData={{}} icon="🌱" />
    )
    expect(screen.getByText('Setpoints')).toBeInTheDocument()
  })

  it('renders light devices when present', () => {
    const devices = [makeDevice({ device_name: 'light_1', state: 1 })]
    renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={devices}
        sensorData={{}}
        icon="🌱"
      />
    )
    expect(screen.getByText('Lights')).toBeInTheDocument()
  })

  it('renders non-light devices when present', () => {
    const devices = [makeDevice({ device_name: 'fan_1', state: 1 })]
    renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={devices}
        sensorData={{}}
        icon="🌱"
      />
    )
    expect(screen.getByText('Devices')).toBeInTheDocument()
    expect(screen.getByText('fan_1')).toBeInTheDocument()
  })

  it('does not render recent on/off section', () => {
    renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[]} sensorData={{}} icon="🌱" />
    )
    expect(screen.queryByText('Recent on/off')).not.toBeInTheDocument()
  })

  it('renders as a link to zone page', () => {
    const { container } = renderWithRouter(
      <DashboardZoneRow location="Veg Room" cluster="main" devices={[]} sensorData={{}} icon="🌱" />
    )
    const link = container.querySelector('a')
    expect(link).toHaveAttribute('href', '/zone/Veg%20Room/main')
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
    const { container } = renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
        trendData={trendData}
        now={new Date(nowMs)}
      />
    )

    expect(screen.getByText('NO DATA').parentElement).toHaveAttribute(
      'title',
      expect.stringContaining('no usable live samples')
    )
    expect(screen.getByText('Mode:').closest('[title]')).toHaveAttribute(
      'title',
      expect.stringContaining('grow mode')
    )
    expect(screen.getByText('Decision').parentElement).toHaveAttribute(
      'title',
      expect.stringContaining('VPD and CO₂ deltas are informational')
    )
    expect(screen.getByText('AUTO')).toHaveAttribute(
      'title',
      expect.stringContaining('automatic control')
    )
    expect(screen.getByText('No scheduled transition').parentElement).toHaveAttribute(
      'title',
      expect.stringContaining('No enabled room schedule')
    )
    expect(
      container.querySelector('[title*="trailing 60-minute sensor history"]')
    ).toBeInTheDocument()
  })
})
