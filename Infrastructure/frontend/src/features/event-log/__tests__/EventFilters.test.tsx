import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { EventFilters, type FilterState } from '../components/EventFilters'

describe('EventFilters', () => {
  const defaultFilters: FilterState = { severity: 'all', search: '', rooms: [], categories: [], types: [] }

  it('renders severity filter buttons', () => {
    render(<EventFilters filters={defaultFilters} onChange={() => {}} view="grouped" onViewChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('button', { name: 'All' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Critical' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Warning' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Error' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Info' })).toBeInTheDocument()
  })

  it('marks the active severity filter as pressed', () => {
    render(<EventFilters filters={{ ...defaultFilters, severity: 'critical' }} onChange={() => {}} view="grouped" onViewChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('button', { name: 'Critical' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('renders a search input with an accessible label', () => {
    render(<EventFilters filters={defaultFilters} onChange={() => {}} view="grouped" onViewChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('searchbox', { name: /filter events/i })).toBeInTheDocument()
  })

  it('shows the current search value', () => {
    render(<EventFilters filters={{ ...defaultFilters, search: 'relay' }} onChange={() => {}} view="grouped" onViewChange={() => {}} rooms={[]} categories={[]} types={[]} />)
    expect(screen.getByRole('searchbox', { name: /filter events/i })).toHaveValue('relay')
  })

  it('toggles room chips and reports changes', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={defaultFilters}
        onChange={onChange}
        view="grouped"
        onViewChange={() => {}}
        rooms={['Flower Room', 'Veg Room']}
        categories={[]}
        types={[]}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Flower Room', pressed: false }))
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters, rooms: ['Flower Room'] })
  })

  it('toggles category checkboxes from the dropdown and reports changes', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={defaultFilters}
        onChange={onChange}
        view="grouped"
        onViewChange={() => {}}
        rooms={[]}
        categories={['relay', 'mutation']}
        types={[]}
      />,
    )

    // No inline category chips anymore — they live behind the dropdown trigger.
    expect(screen.queryByRole('button', { name: 'mutation' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Categories & types/ }))
    const box = screen.getByRole('checkbox', { name: 'mutation' })
    expect(box).not.toBeChecked()
    await user.click(box)
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters, categories: ['mutation'] })
  })

  it('toggles type checkboxes from the dropdown and reports changes', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={defaultFilters}
        onChange={onChange}
        view="grouped"
        onViewChange={() => {}}
        rooms={[]}
        categories={[]}
        types={['relay.state_changed', 'config.updated']}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Categories & types/ }))
    await user.click(screen.getByRole('checkbox', { name: 'config.updated' }))
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters, types: ['config.updated'] })
  })

  it('marks selected dimensions in the dropdown and clears them', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={{ ...defaultFilters, types: ['config.updated'] }}
        onChange={onChange}
        view="grouped"
        onViewChange={() => {}}
        rooms={[]}
        categories={['relay']}
        types={['config.updated']}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Categories & types (1)' }))
    expect(screen.getByRole('checkbox', { name: 'config.updated' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'relay' })).not.toBeChecked()

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(onChange).toHaveBeenCalledWith({ ...defaultFilters })
  })
})

describe('EventFilters compact sidebar mode', () => {
  const defaultFilters: FilterState = { severity: 'all', search: '', rooms: [], categories: [], types: [] }

  function renderCompact(overrides: { filters?: FilterState; rooms?: string[] } = {}) {
    return render(
      <EventFilters
        filters={overrides.filters ?? defaultFilters}
        onChange={() => {}}
        view="grouped"
        onViewChange={() => {}}
        rooms={overrides.rooms ?? ['Flower Room', 'Veg Room', 'Lab']}
        categories={['relay', 'mutation']}
        types={['relay.state_changed']}
        compact
        primaryRooms={['Flower Room', 'Veg Room']}
      />,
    )
  }

  it('shows only primary room chips and a Filters trigger', () => {
    renderCompact()
    expect(screen.getByRole('button', { name: 'Flower Room', pressed: false })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Veg Room', pressed: false })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Filters' })).toBeInTheDocument()
    // Everything else folds into the submenu.
    expect(screen.queryByRole('button', { name: 'All' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Critical' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Alerts' })).not.toBeInTheDocument()
    expect(screen.queryByRole('searchbox', { name: /filter events/i })).not.toBeInTheDocument()
  })

  it('parks extra rooms inside the Filters submenu', async () => {
    const user = userEvent.setup()
    renderCompact()
    expect(screen.queryByRole('button', { name: 'Lab', pressed: false })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Filters' }))
    expect(screen.getByRole('group', { name: 'Room filter' })).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Lab' })).not.toBeChecked()
    expect(screen.getByRole('group', { name: 'Severity filter' })).toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Event log view' })).toBeInTheDocument()
    expect(screen.getByRole('searchbox', { name: /filter events/i })).toBeInTheDocument()
  })

  it('counts every active dimension on the trigger', () => {
    renderCompact({
      filters: { ...defaultFilters, severity: 'error', search: 'relay', types: ['config.updated'] },
    })
    // severity(1) + search(1) + types(1) = 3
    expect(screen.getByRole('button', { name: 'Filters (3)' })).toBeInTheDocument()
  })

  it('offers Clear all filters in compact mode', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(
      <EventFilters
        filters={{ ...defaultFilters, categories: ['relay'] }}
        onChange={onChange}
        view="grouped"
        onViewChange={() => {}}
        rooms={['Flower Room', 'Veg Room']}
        categories={[]}
        types={[]}
        compact
        primaryRooms={['Flower Room', 'Veg Room']}
      />,
    )
    expect(screen.getByRole('button', { name: 'Filters (1)' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Clear all filters/ }))
    expect(onChange).toHaveBeenCalledWith({ severity: 'all', search: '', rooms: [], categories: [], types: [] })
  })
})
