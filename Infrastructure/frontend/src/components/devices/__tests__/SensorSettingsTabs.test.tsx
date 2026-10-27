import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import SensorSettingsTabs from '../SensorSettingsTabs'

vi.mock('../CanBusSection', () => ({
  default: ({ initialStatus }: { initialStatus?: string }) => (
    <div data-testid="can-page">CAN page {initialStatus ?? 'all'}</div>
  ),
}))

vi.mock('../Rs485PlacementSection', () => ({
  default: () => <div data-testid="rs485-page">RS485 page</div>,
}))

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.search}</output>
}

function renderAt(search: string, initialStatus?: 'all' | 'unassigned') {
  render(
    <MemoryRouter initialEntries={[`/devices${search}`]}>
      <SensorSettingsTabs initialStatus={initialStatus} />
      <LocationProbe />
    </MemoryRouter>
  )
}

describe('SensorSettingsTabs', () => {
  it.each(['?tab=sensors', '?tab=sensors&settings_tab=bogus'])(
    'lands on the CAN bus page for %s',
    search => {
      renderAt(search)

      expect(screen.getByRole('tab', { name: 'CAN bus' })).toHaveAttribute('aria-selected', 'true')
      expect(screen.getByTestId('can-page')).toBeInTheDocument()
      expect(screen.queryByTestId('rs485-page')).not.toBeInTheDocument()
    }
  )

  it('mounts only the RS485 page for settings_tab=rs485', () => {
    renderAt('?tab=sensors&settings_tab=rs485')

    expect(screen.getByRole('tab', { name: 'RS485' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAttribute(
      'aria-labelledby',
      'sensor-settings-rs485-tab'
    )
    expect(screen.getByTestId('rs485-page')).toBeInTheDocument()
    expect(screen.queryByTestId('can-page')).not.toBeInTheDocument()
  })

  it('switching tabs keeps every other query parameter', () => {
    renderAt('?tab=sensors&status=unassigned&fixtureSession=abc')

    fireEvent.click(screen.getByRole('tab', { name: 'RS485' }))

    const params = new URLSearchParams(screen.getByTestId('location').textContent ?? '')
    expect(params.get('settings_tab')).toBe('rs485')
    expect(params.get('tab')).toBe('sensors')
    expect(params.get('status')).toBe('unassigned')
    expect(params.get('fixtureSession')).toBe('abc')
    expect(screen.getByTestId('rs485-page')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'CAN bus' }))

    expect(screen.getByTestId('can-page')).toBeInTheDocument()
    expect(
      new URLSearchParams(screen.getByTestId('location').textContent ?? '').get('status')
    ).toBe('unassigned')
  })

  it('passes the unassigned filter to the CAN page only', () => {
    renderAt('?tab=sensors', 'unassigned')

    expect(screen.getByTestId('can-page')).toHaveTextContent('CAN page unassigned')
  })
})
