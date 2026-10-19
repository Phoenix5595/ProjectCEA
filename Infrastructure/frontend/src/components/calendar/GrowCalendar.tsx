import { DayPicker } from '@daypicker/react'
import { fr } from '@daypicker/react/locale'
import { format } from 'date-fns'
import { fr as dateFnsFr } from 'date-fns/locale'
import { toZonedTime } from 'date-fns-tz'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { CalendarEventDto } from '../../types/calendar'
import { EVENT_TYPE_COLORS } from '../../types/calendar'
import { buildCalendarDayMarkers, calendarEventOccursOnDay, calendarDayKey } from '../../utils/calendarDayMarkers'
import { CALENDAR_TZ } from '../../utils/flowerGrowPlan'

import { CalendarDayButton } from './CalendarDayButton'
import CalendarDayDetail from './CalendarDayDetail'
import { CalendarMarkersContext } from './CalendarMarkersContext'


export interface GrowCalendarProps {
  variant: 'compact' | 'full'
  viewMode?: 'unified' | 'room'
  location?: string
  events: CalendarEventDto[]
  loading?: boolean
  onMonthChange?: (month: Date) => void
  onRefresh?: () => void
  showAddTask?: boolean
  onAddTask?: () => void
  /** Scale month grid to fill parent width (e.g. dashboard column up to 50%). */
  fillWidth?: boolean
  /** Notified on selection/clear with the date and all events active on it. */
  onDaySelect?: (date: Date | undefined, events: CalendarEventDto[]) => void
  /** Render the built-in inline day detail panel (default true). */
  showInlineDayDetail?: boolean
  /** Density preset; "dashboard" fits six weeks into a dense upper row. */
  density?: 'standard' | 'dashboard'
}

export default function GrowCalendar({
  variant,
  viewMode = 'unified',
  location,
  events,
  loading,
  onMonthChange,
  onRefresh,
  showAddTask,
  onAddTask,
  fillWidth = false,
  onDaySelect,
  showInlineDayDetail = true,
  density = 'standard',
}: GrowCalendarProps) {
  const [month, setMonth] = useState(() => toZonedTime(new Date(), CALENDAR_TZ))
  const [selected, setSelected] = useState<Date | undefined>()
  const calendarRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    if (viewMode === 'room' && location) {
      return events.filter(e => e.location === location)
    }
    return events
  }, [events, viewMode, location])

  const dayMarkers = useMemo(() => buildCalendarDayMarkers(filtered), [filtered])
  const legendTypes = useMemo(() => [...new Set(filtered.map(event => event.eventType))].sort(), [filtered])

  const dayEvents = selected ? filtered.filter(e => calendarEventOccursOnDay(e, selected)) : []

  const handleSelect = (date: Date | undefined) => {
    setSelected(date)
    onDaySelect?.(date, date ? filtered.filter(e => calendarEventOccursOnDay(e, date)) : [])
  }

  const closeDayDetail = useCallback(() => {
    const key = selected ? calendarDayKey(selected) : null
    setSelected(undefined)
    onDaySelect?.(undefined, [])
    if (key !== null) {
      requestAnimationFrame(() => {
        calendarRef.current?.querySelector<HTMLButtonElement>(
          `[data-day="${key}"] .grow-cal-day-btn`
        )?.focus()
      })
    }
  }, [onDaySelect, selected])

  useEffect(() => {
    if (!selected || !showInlineDayDetail) return
    const onEscape = (event: KeyboardEvent) => {
      const active = document.activeElement
      if (event.key === 'Escape' && !event.defaultPrevented &&
          (active === document.body || calendarRef.current?.contains(active))) {
        event.preventDefault()
        event.stopPropagation()
        closeDayDetail()
      }
    }
    document.addEventListener('keydown', onEscape)
    return () => document.removeEventListener('keydown', onEscape)
  }, [closeDayDetail, selected, showInlineDayDetail])

  const compact = variant === 'compact'
  const scaled = compact && fillWidth
  const dashboard = density === 'dashboard'
  const DayButton = useMemo(() => {
    const maxVisibleEvents = compact ? 1 : 3
    const showRoomLabels = viewMode === 'unified'
    return function StableCalendarDayButton(props: React.ComponentProps<typeof CalendarDayButton>) {
      return <CalendarDayButton {...props} maxVisibleEvents={maxVisibleEvents} showRoomLabels={showRoomLabels} />
    }
  }, [compact, viewMode])


  const weekdayFormat = compact ? 'EEE' : 'cccc'

  const calendarFormatters = useMemo(
    () => ({
      formatWeekdayName: (weekday: Date) => format(weekday, weekdayFormat, { locale: dateFnsFr }),
    }),
    [weekdayFormat]
  )

  return (
    <div
      ref={calendarRef}
      className={`grow-calendar flex flex-col gap-0 ${
        dashboard
          ? 'grow-calendar--dashboard flex-1 min-h-0'
          : scaled
            ? 'grow-calendar--fill w-full min-h-0'
            : compact
              ? 'max-h-155'
              : ''
      }`}
    >
      <div className="flex items-center justify-between gap-0 px-2 pt-2 pb-1 shrink-0">
        <h2
          className={`font-bold tracking-tight text-text-default ${scaled ? 'text-base' : compact ? 'text-sm' : 'text-lg'}`}
        >
          Grow calendar
        </h2>
        <div className="flex gap-0">
          {showAddTask && onAddTask && (
            <button
              type="button"
              onClick={onAddTask}
              className="grow-cal-toolbar-btn grow-cal-toolbar-btn--primary"
            >
              Add task
            </button>
          )}
          {onRefresh && (
            <button type="button" onClick={onRefresh} className="grow-cal-toolbar-btn">
              Refresh
            </button>
          )}
        </div>
      </div>

      <div className="grow-cal-legend" role="list" aria-label="Calendar legend">
        {legendTypes.map(eventType => (
          <span key={eventType} className="grow-cal-legend-item" role="listitem">
            <span className={`inline-block size-2 rounded-sm ${EVENT_TYPE_COLORS[eventType] ?? 'bg-slate-500'}`} aria-hidden="true" />
            {eventType.replaceAll('_', ' ')}
          </span>
        ))}
        {viewMode === 'unified' && <span className="grow-cal-legend-item" role="listitem">F / V / L · Flower Room / Veg Room / Lab</span>}
      </div>

      {loading ? (
        <p className="text-sm text-text-secondary py-8 text-center grow-cal-panel">
          Loading calendar…
        </p>
      ) : (
        <CalendarMarkersContext.Provider value={dayMarkers}>
          <div className="grow-cal-panel min-h-0 flex-1 flex flex-col">
            <DayPicker
              mode="single"
              locale={fr}
              weekStartsOn={0}
              showOutsideDays
              formatters={calendarFormatters}
              selected={selected}
              onSelect={handleSelect}
              month={month}
              onMonthChange={m => {
                setMonth(m)
                onMonthChange?.(m)
              }}
              components={{ DayButton }}
              className={`${scaled ? 'text-sm' : compact ? 'text-xs' : 'text-sm'} w-full grow-calendar-picker`}
              fixedWeeks={dashboard}
            />
          </div>
        </CalendarMarkersContext.Provider>
      )}

      {selected && showInlineDayDetail && (
        <CalendarDayDetail
          key={calendarDayKey(selected)}
          date={selected}
          events={dayEvents}
          onClose={closeDayDetail}
        />
      )}
    </div>
  )
}
