import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import Sidebar from '../Sidebar'

vi.mock('../../../package.json', () => ({
  default: { version: '1.1.17' },
}))

describe('Sidebar version footer', () => {
  it('shows the dynamic package version when expanded', () => {
    render(
      <MemoryRouter>
        <Sidebar collapsed={false} onToggle={() => {}} />
      </MemoryRouter>
    )

    expect(screen.getByText('v1.1.17')).toBeInTheDocument()
  })

  it('hides the package version when collapsed', () => {
    render(
      <MemoryRouter>
        <Sidebar collapsed={true} onToggle={() => {}} />
      </MemoryRouter>
    )

    expect(screen.queryByText('v1.1.17')).not.toBeInTheDocument()
  })
})
