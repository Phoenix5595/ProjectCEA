import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import Sidebar from '../Sidebar'

describe('Sidebar', () => {
  it('renders primary navigation with accessible named links', () => {
    render(
      <MemoryRouter initialEntries={['/vegetation/monitoring']}>
        <Sidebar />
      </MemoryRouter>
    )

    const navigation = screen.getByRole('navigation', { name: 'Primary navigation' })
    const routes = [
      ['Laboratory', '/laboratory'],
      ['Vegetation', '/vegetation'],
      ['Flower', '/flower'],
      ['Devices', '/devices'],
    ]

    for (const [label, path] of routes) {
      const link = within(navigation).getByRole('link', { name: label })
      expect(link).toHaveAttribute('href', path)
      expect(link).toHaveAttribute('title', label)
    }
  })
})
