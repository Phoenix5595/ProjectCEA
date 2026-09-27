import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import Sidebar from '../Sidebar'

vi.mock('../../../package.json', () => ({
  default: { version: '1.1.17' },
}))

describe('Sidebar variants', () => {
  it('keeps the desktop rail compact, labelled, and without an expansion control', () => {
    render(
      <MemoryRouter>
        <Sidebar variant="rail" />
      </MemoryRouter>
    )

    const navigation = screen.getByRole('navigation', { name: 'Primary navigation' })
    const laboratoryLink = screen.getByRole('link', { name: 'Laboratory' })

    expect(navigation.parentElement).toHaveClass('w-7.5')
    expect(laboratoryLink).toHaveAttribute('title', 'Laboratory')
    expect(laboratoryLink.querySelector('span')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sidebar/i })).not.toBeInTheDocument()
    expect(screen.queryByText('v1.1.17')).not.toBeInTheDocument()
  })

  it('shows labelled links and the version footer in the phone drawer', () => {
    render(
      <MemoryRouter>
        <Sidebar variant="drawer" />
      </MemoryRouter>
    )

    expect(
      screen.getByRole('navigation', { name: 'Primary navigation' }).parentElement
    ).toHaveClass('w-52')
    expect(screen.getByRole('link', { name: 'Laboratory' })).toHaveTextContent('Laboratory')
    expect(screen.getByText('v1.1.17')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /sidebar/i })).not.toBeInTheDocument()
  })
})
