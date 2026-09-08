import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EventFilters, type FilterState } from '../components/EventFilters'

describe('EventFilters', () => {
  const defaultFilters: FilterState = { severity: 'all', search: '', rooms: [], categories: [], types: [] }

  it('renders severity filter buttons', () => {
    render(<EventFilters filters={defaultFilters} onChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Critical' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Warning' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Info' })).toBeInTheDocument()
  })

  it('marks the active severity filter as pressed', () => {
    render(
      <EventFilters filters={{ ...defaultFilters, severity: 'critical' }} onChange={() => {}} rooms={[]} categories={[]} types={[]} />,
    )
    expect(screen.getByRole('button', { name: 'Critical' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('renders a search input with an accessible label', () => {
    render(<EventFilters filters={defaultFilters} onChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('searchbox', { name: /filter events/i })).toBeInTheDocument()
  })

  it('shows the current search value', () => {
    render(<EventFilters filters={{ ...defaultFilters, search: 'relay' }} onChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('searchbox', { name: /filter events/i })).toHaveValue('relay')
  })

  it('toggles room chips and reports changes', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={defaultFilters}
        onChange={onChange}
        rooms={['Flower Room', 'Veg Room']}
        categories={[]}
        types={[]}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Flower Room', pressed: false }))
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters, rooms: ['Flower Room'] })
  })

  it('toggles category chips and reports changes', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={defaultFilters}
        onChange={onChange}
        rooms={[]}
        categories={['relay', 'mutation']}
        types={[]}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'mutation', pressed: false }))
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters, categories: ['mutation'] })
  })

  it('toggles type chips and reports changes', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={defaultFilters}
        onChange={onChange}
        rooms={[]}
        categories={[]}
        types={['relay.state_changed', 'config.updated']}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'config.updated', pressed: false }))
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters, types: ['config.updated'] })
  })
})
