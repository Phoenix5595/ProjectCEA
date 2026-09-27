import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import Sidebar from '../Sidebar'

describe('Sidebar', () => {
  it('renders a permanent icon rail with accessible links and active-route styling', () => {
    render(
      <MemoryRouter initialEntries={['/vegetation/monitoring']}>
        <Sidebar />
      </MemoryRouter>
    )

    const navigation = screen.getByRole('navigation', { name: 'Primary navigation' })
    const rail = navigation.parentElement
    expect(rail).toHaveClass('fixed', 'w-7.5')
    expect(rail).not.toHaveClass('w-52')

    const routes = [
      ['Laboratory', '/laboratory'],
      ['Vegetation', '/vegetation'],
      ['Flower', '/flower'],
      ['Devices', '/devices'],
    ]

    for (const [label, path] of routes) {
      const link = screen.getByRole('link', { name: label })
      expect(link).toHaveAttribute('href', path)
      expect(link).toHaveAttribute('title', label)
      expect(link.textContent).toBe('')
    }

    expect(screen.getByRole('link', { name: 'Vegetation' })).toHaveClass(
      'bg-accent-vivid',
      'text-surface-base',
      'font-medium'
    )
  })
})
