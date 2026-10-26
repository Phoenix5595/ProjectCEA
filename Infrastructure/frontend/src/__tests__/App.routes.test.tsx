import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '../App'
import TopRibbon from '../components/TopRibbon'

vi.mock('../pages/LaboratoryOverview', () => ({
  default: function LaboratoryOverviewFixture() {
    return <div>Laboratory overview fixture</div>
  },
}))

vi.mock('../pages/FlowerOverview', () => ({
  default: function FlowerOverviewFixture() {
    return <div>Flower overview fixture</div>
  },
}))

vi.mock('../pages/FlowerSoil', () => ({
  default: function FlowerSoilFixture() {
    return <div>Flower soil fixture</div>
  },
}))

vi.mock('../pages/FlowerMonitoring', () => ({
  default: function FlowerMonitoringFixture() {
    return <div>Flower monitoring fixture</div>
  },
}))

vi.mock('../pages/VegetationMonitoring', () => ({
  default: function VegetationMonitoringFixture() {
    return <div>Vegetation monitoring fixture</div>
  },
}))

const LEGACY_REDIRECTS: ReadonlyArray<{
  from: string
  to: string
  destinationText?: string
}> = [
  { from: '/laboratory/climate', to: '/laboratory' },
  { from: '/laboratory/water', to: '/laboratory' },
  { from: '/laboratory/infrastructure', to: '/laboratory' },
  {
    from: '/zone/Lab/main',
    to: '/laboratory',
    destinationText: 'Laboratory overview fixture',
  },
]

beforeEach(() => {
  window.history.replaceState({}, '', '/')
})

describe('legacy frontend routes', () => {
  for (const redirect of LEGACY_REDIRECTS) {
    it(`redirects ${redirect.from} to ${redirect.to}`, async () => {
      window.history.replaceState({}, '', redirect.from)

      render(<App />)

      await waitFor(() => {
        expect(window.location.pathname).toBe(redirect.to)
      })
      if (redirect.destinationText) {
        expect(await screen.findByText(redirect.destinationText)).toBeInTheDocument()
      }
    })
  }
})

describe('historical entry', () => {
  it('keeps a short fixed Historical range on Historical', async () => {
    window.history.replaceState(
      {},
      '',
      '/flower/historical?start=2026-07-20T14%3A00%3A00.000Z&end=2026-07-20T16%3A00%3A00.000Z'
    )

    render(<App />)

    expect(await screen.findByText('Flower monitoring fixture')).toBeInTheDocument()
    expect(window.location.pathname).toBe('/flower/historical')
    const params = new URLSearchParams(window.location.search)
    expect(params.get('start')).toBe('2026-07-20T14:00:00.000Z')
    expect(params.get('end')).toBe('2026-07-20T16:00:00.000Z')
  })

  it('replaces a live Historical entry with the 31-day fixed default', async () => {
    window.history.replaceState({}, '', '/flower/historical?range=live-1h')

    render(<App />)

    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.get('range')).toBeNull()
      expect(params.get('start')).not.toBeNull()
      expect(params.get('end')).not.toBeNull()
    })
    const params = new URLSearchParams(window.location.search)
    const start = new Date(params.get('start') ?? '')
    const end = new Date(params.get('end') ?? '')
    expect(end.getTime() - start.getTime()).toBe(31 * 24 * 3600_000)
    expect(window.location.pathname).toBe('/flower/historical')
  })

  it('replaces a bare Historical entry with the 31-day fixed default', async () => {
    window.history.replaceState({}, '', '/vegetation/historical')

    render(<App />)

    expect(await screen.findByText('Vegetation monitoring fixture')).toBeInTheDocument()
    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.get('start')).not.toBeNull()
      expect(params.get('end')).not.toBeNull()
    })
    const params = new URLSearchParams(window.location.search)
    const start = new Date(params.get('start') ?? '')
    const end = new Date(params.get('end') ?? '')
    expect(end.getTime() - start.getTime()).toBe(31 * 24 * 3600_000)
    expect(window.location.pathname).toBe('/vegetation/historical')
  })
})

describe('sector navigation', () => {
  it('renders only the sector root link for Laboratory', () => {
    render(
      <MemoryRouter initialEntries={['/laboratory']}>
        <TopRibbon sector="laboratory" />
      </MemoryRouter>
    )

    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAccessibleName('Laboratory')
    expect(links[0]).toHaveAttribute('href', '/laboratory')
    expect(links[0]).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('link', { name: 'Overview' })).not.toBeInTheDocument()
  })

  it('renders only the sector root link for Devices', () => {
    render(
      <MemoryRouter initialEntries={['/devices']}>
        <TopRibbon sector="devices" />
      </MemoryRouter>
    )

    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAccessibleName('Device Configuration')
    expect(links[0]).toHaveAttribute('href', '/devices')
    expect(links[0]).toHaveAttribute('aria-current', 'page')
  })

  it('renders the Flower Soil page at /flower/soil with a Soil ribbon link', async () => {
    window.history.replaceState({}, '', '/flower/soil')

    render(<App />)

    await waitFor(() => {
      expect(screen.getByText('Flower soil fixture')).toBeInTheDocument()
    })
    expect(window.location.pathname).toBe('/flower/soil')
  })

  it('orders the Flower ribbon links and marks the Soil tab as current', () => {
    render(
      <MemoryRouter initialEntries={['/flower/soil']}>
        <TopRibbon sector="flower" />
      </MemoryRouter>
    )

    const links = screen.getAllByRole('link')
    expect(links.map(link => link.textContent)).toEqual([
      'Flower Room',
      'Monitoring',
      'Control',
      'Automation',
      'Soil',
      'Historical',
    ])

    const soilLink = screen.getByRole('link', { name: 'Soil' })
    expect(soilLink).toHaveAttribute('href', '/flower/soil')
    expect(soilLink).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: 'Flower Room' })).not.toHaveAttribute('aria-current')
  })
})
