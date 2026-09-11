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

const LEGACY_REDIRECTS = [
  { from: '/laboratory/climate', to: '/laboratory' },
  { from: '/laboratory/water', to: '/laboratory' },
  { from: '/laboratory/infrastructure', to: '/laboratory' },
  { from: '/flower/soil', to: '/flower' },
] as const

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
    })
  }
})

describe('sector navigation', () => {
  it('does not expose obsolete Laboratory tabs', () => {
    render(
      <MemoryRouter initialEntries={['/laboratory']}>
        <TopRibbon sector="laboratory" activeTab="overview" onTabChange={() => {}} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Climate' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Water' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Infrastructure' })).not.toBeInTheDocument()
  })

  it('does not expose obsolete Flower Soil navigation', () => {
    render(
      <MemoryRouter initialEntries={['/flower']}>
        <TopRibbon sector="flower" activeTab="overview" onTabChange={() => {}} />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Monitoring' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Soil' })).not.toBeInTheDocument()
  })
})
