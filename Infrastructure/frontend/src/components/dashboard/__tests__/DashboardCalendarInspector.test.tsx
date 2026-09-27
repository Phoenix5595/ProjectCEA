import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import DashboardCalendarInspector from '../DashboardCalendarInspector'
import type { CalendarEventDto } from '../../../types/calendar'
import * as calendarUtils from '../../../utils/calendarDayMarkers'

vi.mock('../../../services/api', () => ({
  apiClient: {
    createCalendarEvent: vi.fn(),
    updateCalendarEvent: vi.fn(),
  },
}))

import { apiClient } from '../../../services/api'

const NOW = new Date('2026-09-21T16:00:00Z') // noon in America/Toronto

function makeEvent(overrides: Partial<CalendarEventDto> = {}): CalendarEventDto {
  return {
    id: 'ev-1',
    source: 'manual',
    eventType: 'planned_task',
    title: 'Check domes',
    start: '2026-09-21',
    location: 'Flower Room',
    editable: true,
    colorKey: 'planned_task',
    numericId: 42,
    ...overrides,
  }
}

function renderInspector(props: Partial<Parameters<typeof DashboardCalendarInspector>[0]> = {}) {
  const onRefresh = vi.fn()
  const onOpenGrowPlan = vi.fn()
  render(
    <DashboardCalendarInspector
      events={[]}
      now={NOW}
      selectedDate={undefined}
      selectedDateEvents={[]}
      onRefresh={onRefresh}
      onOpenGrowPlan={onOpenGrowPlan}
      {...props}
    />
  )
  return { onRefresh, onOpenGrowPlan }
}

const TODAY_KEY = calendarUtils.calendarDayKey(NOW)

describe('DashboardCalendarInspector overview', () => {
  it('shows today tasks, active phase and next phase with relative day labels', () => {
    renderInspector({
      events: [
        makeEvent({ title: 'Check domes', start: TODAY_KEY }),
        makeEvent({
          id: 'phase-active',
          title: 'Flower bulk',
          eventType: 'flower_bulk',
          start: '2026-09-10',
          end: '2026-09-30',
          metadata: { grow_plan_id: 'gp1' },
        }),
        makeEvent({
          id: 'phase-next',
          title: 'Flower ripen',
          eventType: 'flower_ripen',
          start: '2026-10-05',
          end: '2026-10-20',
          metadata: { grow_plan_id: 'gp1' },
        }),
      ],
    })

    expect(screen.getByText('Check domes')).toBeInTheDocument()
    expect(screen.getByText('Flower bulk')).toBeInTheDocument()
    expect(screen.getByText('Flower ripen')).toBeInTheDocument()
    expect(screen.getByText(/in 14 days/)).toBeInTheDocument()
  })

  it('renders explicit empty states when nothing is scheduled', () => {
    renderInspector({ events: [] })
    expect(screen.getByText('No tasks today')).toBeInTheDocument()
    expect(screen.getByText('No active phase')).toBeInTheDocument()
    expect(screen.getByText('No upcoming phase in loaded range')).toBeInTheDocument()
  })
  it('caps Today task summaries while keeping every task reachable', async () => {
    const user = userEvent.setup()
    const tasks = Array.from({ length: 4 }, (_, index) =>
      makeEvent({
        id: `today-${index + 1}`,
        title: `Today task ${index + 1}`,
        start: TODAY_KEY,
      })
    )
    renderInspector({ events: tasks })

    const inspector = screen.getByRole('complementary', { name: 'Calendar inspector' })
    expect(within(inspector).getAllByRole('button', { name: /Today task/ })).toHaveLength(3)
    expect(
      within(inspector).queryByRole('button', { name: 'Today task 4' })
    ).not.toBeInTheDocument()
    await user.click(within(inspector).getByRole('button', { name: 'All 4 events' }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: /Today task 4/ })).toBeInTheDocument()
  })

  it('offers New event and Create flower grow plan actions', async () => {
    const user = userEvent.setup()
    const { onOpenGrowPlan } = renderInspector({ events: [] })
    await user.click(screen.getByRole('button', { name: 'Create flower grow plan' }))
    expect(onOpenGrowPlan).toHaveBeenCalledOnce()
  })
})

describe('DashboardCalendarInspector selection flow', () => {
  it('lists every event on the selected date as buttons and opens detail on click', async () => {
    const user = userEvent.setup()
    const selectedDate = new Date('2026-09-22T16:00:00Z')
    const events = [
      makeEvent({ id: 'a', title: 'Top dress', start: '2026-09-22' }),
      makeEvent({
        id: 'b',
        title: 'Veg phase',
        eventType: 'bed_veg',
        source: 'mode_transition',
        editable: false,
        start: '2026-09-20',
        end: '2026-09-24',
      }),
    ]
    renderInspector({
      events,
      selectedDate,
      selectedDateEvents: events.filter(ev =>
        calendarUtils.calendarEventOccursOnDay(ev, selectedDate)
      ),
    })

    const dayButtons = screen.getAllByRole('button', { name: /Top dress|Veg phase/ })
    expect(dayButtons).toHaveLength(2)

    await user.click(screen.getByRole('button', { name: /Top dress/ }))
    expect(screen.getByRole('heading', { name: 'Top dress' })).toBeInTheDocument()
    expect(screen.getByText('planned_task')).toBeInTheDocument()
    expect(screen.getByText('manual')).toBeInTheDocument()
    expect(screen.getByText('Flower Room')).toBeInTheDocument()
  })

  it('hides Edit for generated events', async () => {
    const user = userEvent.setup()
    const selectedDate = new Date('2026-09-22T16:00:00Z')
    const events = [
      makeEvent({
        id: 'gen',
        title: 'Mode transition',
        source: 'mode_transition',
        editable: false,
        numericId: undefined,
        start: '2026-09-22',
      }),
    ]
    renderInspector({
      events,
      selectedDate,
      selectedDateEvents: events,
    })

    await user.click(screen.getByRole('button', { name: /Mode transition/ }))
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()
  })

  it('resets to the matching date view when selectedDate changes', () => {
    const events = [makeEvent({ title: 'Top dress', start: '2026-09-22' })]
    const { rerender } = render(
      <DashboardCalendarInspector
        events={events}
        now={NOW}
        selectedDate={undefined}
        selectedDateEvents={[]}
        onRefresh={vi.fn()}
        onOpenGrowPlan={vi.fn()}
      />
    )
    expect(screen.getByText('No tasks today')).toBeInTheDocument()

    const selectedDate = new Date('2026-09-22T16:00:00Z')
    rerender(
      <DashboardCalendarInspector
        events={events}
        now={NOW}
        selectedDate={selectedDate}
        selectedDateEvents={events}
        onRefresh={vi.fn()}
        onOpenGrowPlan={vi.fn()}
      />
    )
    expect(screen.getByRole('button', { name: /Top dress/ })).toBeInTheDocument()

    rerender(
      <DashboardCalendarInspector
        events={events}
        now={NOW}
        selectedDate={undefined}
        selectedDateEvents={[]}
        onRefresh={vi.fn()}
        onOpenGrowPlan={vi.fn()}
      />
    )
    expect(screen.getByText('No tasks today')).toBeInTheDocument()
  })
  it('keeps the day card bounded while exposing the fourth event and its long note in the dialog', async () => {
    const user = userEvent.setup()
    const selectedDate = new Date('2026-09-22T16:00:00Z')
    const longNote =
      'This note contains the complete fourth event details and must remain reachable without growing the dashboard card.'
    const events = Array.from({ length: 4 }, (_, index) =>
      makeEvent({
        id: `day-${index + 1}`,
        title: `Day task ${index + 1}`,
        start: '2026-09-22',
        notes: index === 3 ? longNote : undefined,
      })
    )
    renderInspector({ events, selectedDate, selectedDateEvents: events })

    const inspector = screen.getByRole('complementary', { name: 'Calendar inspector' })
    expect(within(inspector).getAllByRole('button', { name: /Day task/ })).toHaveLength(3)
    expect(within(inspector).queryByText(longNote)).not.toBeInTheDocument()
    const allEvents = within(inspector).getByRole('button', { name: 'All 4 events' })
    await user.click(allEvents)

    let dialog = screen.getByRole('dialog')
    expect(dialog).toHaveClass('max-h-[calc(100dvh-2rem)]', 'overflow-y-auto')
    await user.click(within(dialog).getByRole('button', { name: /Day task 4/ }))
    dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText(longNote)).toBeInTheDocument()
    expect(inspector.contains(within(dialog).getByText(longNote))).toBe(false)
    expect(
      inspector.querySelector('.dashboard-inspector__event-summary')?.querySelectorAll('button')
    ).toHaveLength(3)

    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: /Day task 4/ })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(allEvents).toHaveFocus()
    expect(
      within(inspector).getByRole('button', { name: 'New event on this date' })
    ).toBeInTheDocument()
  })
})

describe('DashboardCalendarInspector create', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('emits the exact POST body for a new manual event', async () => {
    const user = userEvent.setup()
    const selectedDate = new Date('2026-09-22T16:00:00Z')
    vi.mocked(apiClient.createCalendarEvent).mockResolvedValue(
      makeEvent({ id: '7', numericId: 7, title: 'Reseed trays' })
    )
    renderInspector({
      events: [],
      selectedDate,
      selectedDateEvents: [],
    })

    await user.click(screen.getByRole('button', { name: 'New event on this date' }))
    await user.type(screen.getByLabelText('Title'), 'Reseed trays')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(apiClient.createCalendarEvent).toHaveBeenCalledOnce()
    })
    expect(apiClient.createCalendarEvent).toHaveBeenCalledWith({
      title: 'Reseed trays',
      event_type: 'planned_task',
      location: 'Flower Room',
      start_date: '2026-09-22',
      end_date: null,
      notes: null,
      cluster: 'main',
      all_day: true,
    })
  })

  it('never calls the API when end date precedes start date', async () => {
    const user = userEvent.setup()
    renderInspector({ events: [] })

    await user.click(screen.getByRole('button', { name: 'New event' }))
    await user.type(screen.getByLabelText('Title'), 'Too early')
    fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2026-09-21' } })
    fireEvent.change(screen.getByLabelText('End date (optional)'), {
      target: { value: '2026-09-20' },
    })
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(apiClient.createCalendarEvent).not.toHaveBeenCalled()
    expect(screen.getByText('End date must be on or after the start date.')).toBeInTheDocument()
  })

  it('shows the API error and preserves input on failed writes', async () => {
    const user = userEvent.setup()
    vi.mocked(apiClient.createCalendarEvent).mockRejectedValueOnce(new Error('network down'))
    renderInspector({ events: [] })

    await user.click(screen.getByRole('button', { name: 'New event' }))
    await user.type(screen.getByLabelText('Title'), 'Fragile task')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('network down')).toBeInTheDocument()
    expect(screen.getByLabelText('Title')).toHaveValue('Fragile task')
  })

  it('displays the normalized saved event after a successful create', async () => {
    const user = userEvent.setup()
    const { onRefresh } = renderInspector({ events: [] })
    vi.mocked(apiClient.createCalendarEvent).mockResolvedValue(
      makeEvent({
        id: '9',
        numericId: 9,
        title: 'Reseed trays',
        notes: 'check domes',
      }) as CalendarEventDto
    )

    await user.click(screen.getByRole('button', { name: 'New event' }))
    await user.type(screen.getByLabelText('Title'), 'Reseed trays')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByRole('heading', { name: 'Reseed trays' })).toBeInTheDocument()
    expect(onRefresh).toHaveBeenCalledOnce()
  })
  it('does not submit when the create form is canceled or closed and restores its trigger focus', async () => {
    const user = userEvent.setup()
    renderInspector({ events: [] })
    const trigger = screen.getByRole('button', { name: 'New event' })

    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(apiClient.createCalendarEvent).not.toHaveBeenCalled()
    expect(apiClient.updateCalendarEvent).not.toHaveBeenCalled()
    expect(trigger).toHaveFocus()

    await user.click(trigger)
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(apiClient.createCalendarEvent).not.toHaveBeenCalled()
    expect(apiClient.updateCalendarEvent).not.toHaveBeenCalled()
    expect(trigger).toHaveFocus()
  })
})

describe('DashboardCalendarInspector edit', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('submits PATCH with the numeric id and preserves an unchanged cluster', async () => {
    const user = userEvent.setup()
    const selectedDate = new Date('2026-09-22T16:00:00Z')
    const events = [makeEvent({ title: 'Check domes', cluster: 'side', start: '2026-09-22' })]
    vi.mocked(apiClient.updateCalendarEvent).mockResolvedValue(
      makeEvent({ title: 'Check domes twice', cluster: 'side' })
    )
    renderInspector({ events, selectedDate, selectedDateEvents: events })

    await user.click(screen.getByRole('button', { name: /Check domes/ }))
    await user.click(screen.getByRole('button', { name: 'Edit' }))
    const title = screen.getByLabelText('Title')
    await user.clear(title)
    await user.type(title, 'Check domes twice')
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(apiClient.updateCalendarEvent).toHaveBeenCalledOnce()
    })
    expect(apiClient.updateCalendarEvent).toHaveBeenCalledWith(42, {
      title: 'Check domes twice',
      event_type: 'planned_task',
      location: 'Flower Room',
      start_date: '2026-09-22',
      end_date: null,
      notes: null,
      cluster: 'side',
    })
  })
  it('returns canceled edit forms to event details without submitting or losing focus', async () => {
    const user = userEvent.setup()
    const selectedDate = new Date('2026-09-22T16:00:00Z')
    const events = [makeEvent({ title: 'Check domes', start: '2026-09-22' })]
    renderInspector({ events, selectedDate, selectedDateEvents: events })

    await user.click(screen.getByRole('button', { name: /Check domes/ }))
    const editButton = screen.getByRole('button', { name: 'Edit' })
    await user.click(editButton)
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByRole('heading', { name: 'Check domes' })).toBeInTheDocument()
    const restoredEditButton = screen.getByRole('button', { name: 'Edit' })
    expect(restoredEditButton).toHaveFocus()
    expect(apiClient.createCalendarEvent).not.toHaveBeenCalled()
    expect(apiClient.updateCalendarEvent).not.toHaveBeenCalled()
  })
})
