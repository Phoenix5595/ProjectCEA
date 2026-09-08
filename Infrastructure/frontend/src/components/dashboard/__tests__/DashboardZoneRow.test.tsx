import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { DashboardZoneRow } from '../DashboardZoneRow'
import type { Device } from '../../../types/device'

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
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
      />
    )
    expect(screen.getByText('Vegetation Room')).toBeInTheDocument()
    expect(screen.getByText('🌱')).toBeInTheDocument()
  })

  it('renders climate mini section', () => {
    renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
      />
    )
    expect(screen.getByText('Climate')).toBeInTheDocument()
  })

  it('renders setpoints section', () => {
    renderWithRouter(
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
      />
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
      <DashboardZoneRow
        location="Veg Room"
        cluster="main"
        devices={[]}
        sensorData={{}}
        icon="🌱"
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
      />
    )
    const link = container.querySelector('a')
    expect(link).toHaveAttribute('href', '/zone/Veg%20Room/main')
  })
})
