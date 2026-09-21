import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import GrowCalendar from '../GrowCalendar';
import type { CalendarEventDto } from '../../../types/calendar';
import { calendarDayKey } from '../../../utils/calendarDayMarkers';

function todayIso(): string {
  return calendarDayKey(new Date());
}

function makeEvent(overrides: Partial<CalendarEventDto> = {}): CalendarEventDto {
  return {
    id: 'ev-1',
    source: 'manual',
    eventType: 'planned_task',
    title: 'Check domes',
    start: todayIso(),
    location: 'Flower Room',
    editable: true,
    colorKey: 'planned_task',
    numericId: 42,
    ...overrides,
  };
}

describe('GrowCalendar selection', () => {
  it('notifies onDaySelect with the date and its events and shows the inline detail', async () => {
    const user = userEvent.setup();
    const onDaySelect = vi.fn();
    const events = [makeEvent()];
    render(<GrowCalendar variant="compact" fillWidth events={events} onDaySelect={onDaySelect} />);

    const todayButton = document.querySelector('.grow-cal-day-btn--today');
    expect(todayButton).not.toBeNull();
    await user.click(todayButton as HTMLElement);

    expect(onDaySelect).toHaveBeenCalledOnce();
    const [date, dayEvents] = onDaySelect.mock.calls[0];
    expect(calendarDayKey(date as Date)).toBe(todayIso());
    expect(dayEvents).toHaveLength(1);
    expect((dayEvents as CalendarEventDto[])[0].id).toBe('ev-1');

    // Default inline day detail still renders, listing the day's events.
    expect(screen.getByRole('button', { name: 'Close day detail' })).toBeInTheDocument();
    expect(screen.getAllByText('Check domes').length).toBeGreaterThanOrEqual(1);
  });

  it('skips the inline detail when showInlineDayDetail is false but still notifies', async () => {
    const user = userEvent.setup();
    const onDaySelect = vi.fn();
    const events = [makeEvent()];
    render(
      <GrowCalendar
        variant="compact"
        fillWidth
        events={events}
        onDaySelect={onDaySelect}
        showInlineDayDetail={false}
      />
    );

    const todayButton = document.querySelector('.grow-cal-day-btn--today');
    await user.click(todayButton as HTMLElement);

    expect(onDaySelect).toHaveBeenCalledOnce();
    expect(screen.queryByText('No events on this day')).not.toBeInTheDocument();
  });

  it('applies the dashboard density class only on demand', () => {
    const { container, rerender } = render(
      <GrowCalendar variant="compact" fillWidth events={[]} />
    );
    expect(container.querySelector('.grow-calendar--dashboard')).toBeNull();

    rerender(
      <GrowCalendar variant="compact" fillWidth events={[]} density="dashboard" />
    );
    expect(container.querySelector('.grow-calendar--dashboard')).not.toBeNull();
  });

  it('renders the current month caption in the picker', () => {
    render(<GrowCalendar variant="compact" fillWidth events={[]} />);
    expect(screen.getByText(/septembre|september/i)).toBeInTheDocument();
  });
});
