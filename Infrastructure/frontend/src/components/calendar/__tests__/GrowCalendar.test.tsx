import { addDays } from 'date-fns'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, vi } from 'vitest'

import type { CalendarEventDto } from '../../../types/calendar'
import { buildCalendarDayMarkers, calendarDayKey } from '../../../utils/calendarDayMarkers'
import GrowCalendar from '../GrowCalendar'

function todayIso(): string {
  return calendarDayKey(new Date())
}

function makeEvent(overrides: Partial<CalendarEventDto> = {}): CalendarEventDto {
  return {
    id: 'ev-1', source: 'manual', eventType: 'planned_task', title: 'Check domes',
    start: todayIso(), location: 'Flower Room', editable: true, colorKey: 'planned_task',
    numericId: 42, ...overrides,
  }
}

describe('GrowCalendar', () => {
  it('notifies with the selected date and all events, and shows full detail', async () => {
    const user = userEvent.setup()
    const onDaySelect = vi.fn()
    render(<GrowCalendar variant="compact" fillWidth events={[makeEvent()]} onDaySelect={onDaySelect} />)
    const todayButton = document.querySelector('.grow-cal-day-btn--today')
    expect(todayButton).not.toBeNull()
    await user.click(todayButton as HTMLElement)
    expect(onDaySelect).toHaveBeenCalledOnce()
    const [date, dayEvents] = onDaySelect.mock.calls[0]
    expect(calendarDayKey(date as Date)).toBe(todayIso())
    expect(dayEvents).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Close day detail' })).toBeInTheDocument()
    expect(screen.getAllByText('Check domes').length).toBeGreaterThanOrEqual(1)
  })

  it('keeps dashboard months at six complete weeks', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-15T12:00:00.000Z'))
    try {
      const { container } = render(<GrowCalendar variant="compact" fillWidth events={[]} density="dashboard" />)
      expect(container.querySelectorAll('.rdp-day_button')).toHaveLength(42)
      fireEvent.click(container.querySelector('.rdp-button_previous') as HTMLButtonElement)
      expect(container.querySelector('.rdp-month_caption')).toHaveTextContent(/ao[uû]t 2026/i)
      expect(container.querySelectorAll('.rdp-day_button')).toHaveLength(42)
    } finally {
      vi.useRealTimers()
    }
  })

  it('indexes inclusive days once and keeps a noted event in one visible group', () => {
    const start = todayIso()
    const end = calendarDayKey(addDays(new Date(`${start}T12:00:00`), 2))
    const phase = makeEvent({ id: 'phase', eventType: 'flower_stretch', start, end })
    const index = buildCalendarDayMarkers([
      makeEvent({ id: 'b', start, end: start, location: 'Veg Room' }),
      makeEvent({ id: 'a', start, end: start, notes: 'one event with notes' }),
      phase,
    ])
    expect(index.get(start)?.events.map(event => event.id)).toEqual(['a', 'phase', 'b'])
    expect(index.get(end)?.events.map(event => event.id)).toEqual(['phase'])
    expect(index.get(start)?.notes.map(event => event.id)).toEqual(['a'])
  })

  it('shows compact unique overflow and reveals every event via keyboard selection', async () => {
    const user = userEvent.setup()
    const date = todayIso()
    const events = [
      makeEvent({ id: 'one', title: 'Long readable title', start: date, notes: 'Full note here' }),
      makeEvent({ id: 'two', title: 'Second event', start: date }),
      makeEvent({ id: 'three', title: 'Third event', start: date }),
      makeEvent({ id: 'four', title: 'Fourth event', start: date }),
    ]
    const onDaySelect = vi.fn()
    render(<GrowCalendar variant="compact" fillWidth events={events} onDaySelect={onDaySelect} />)
    const dateButton = screen.getByRole('button', { name: /Long readable title/ })
    expect(dateButton.querySelectorAll('.grow-cal-event-chip')).toHaveLength(1)
    expect(dateButton.querySelector('.grow-cal-more')).toHaveTextContent('+3')
    dateButton.focus()
    await user.keyboard('{Enter}')
    expect(onDaySelect.mock.calls.at(-1)?.[1]).toHaveLength(4)
    expect(screen.getByText('Full note here')).toBeInTheDocument()
  })

  it('closes paginated inline detail with Escape and restores its day focus', async () => {
    const user = userEvent.setup()
    render(<GrowCalendar variant="full" events={[
      makeEvent({ id: 'one', title: 'First event' }),
      makeEvent({ id: 'two', title: 'Second event' }),
      makeEvent({ id: 'three', title: 'Third event' }),
      makeEvent({ id: 'four', title: 'Fourth event' }),
    ]} />)
    const day = screen.getByRole('button', { name: /First event/ })
    day.focus()
    await user.keyboard('{Enter}')
    const detail = screen.getByRole('button', { name: 'Close day detail' })
      .closest('.grow-cal-day-detail')
    if (!(detail instanceof HTMLElement)) throw new Error('day detail unavailable')
    await user.click(within(detail).getByRole('button', { name: 'Next' }))
    expect(within(detail).getByText('Fourth event')).toBeVisible()
    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Close day detail' })).not.toBeInTheDocument()
      expect(day).toHaveFocus()
    })
    expect(day).toHaveAccessibleName(/First event/)
  })
})
