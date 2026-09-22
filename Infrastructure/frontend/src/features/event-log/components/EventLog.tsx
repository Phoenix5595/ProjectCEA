import { useState, useMemo, useCallback } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { EventFilters, CompactFiltersTrigger, type FilterState } from './EventFilters'
import { EventRow } from './EventRow'
import { EventGroupedView, buildGroups, withPreallocatedSlots, type EventLogView } from './EventGroupedView'
import { useEventLogPagination } from '../state/useEventLog'
import { categoryTheme, displayCategoryOf } from '../presentation/categoryTheme'

interface EventLogProps {
  entries: readonly EventLogEntry[]
  now: Date
  /** Sidebar mode: compact filter row (rooms + Filters submenu) and compact groups. */
  compact?: boolean
  /** Rooms always rendered as chips in compact mode. */
  primaryRooms?: readonly string[]
}

export function EventLog({ entries, now, compact = false, primaryRooms }: EventLogProps) {
  const { paging, loadOlder } = useEventLogPagination()
  const [view, setView] = useState<EventLogView>('grouped')
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null)
  const [filters, setFilters] = useState<FilterState>({
    severity: 'all',
    search: '',
    rooms: [],
    categories: [],
    types: [],
  })

  const handleFilterChange = useCallback((next: FilterState) => setFilters(next), [])

  const handleExpand = useCallback(
    (category: string) => setExpandedCategory((current) => (current === category ? null : category)),
    [],
  )

  const rooms = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.payload.room).filter((room): room is string => typeof room === 'string'))).sort(),
    [entries],
  )
  const categories = useMemo(() => Array.from(new Set(entries.map((entry) => entry.category))).sort(), [entries])
  const types = useMemo(() => Array.from(new Set(entries.map((entry) => entry.type))).sort(), [entries])

  const searchHaystacks = useMemo(() => {
    const map = new Map<string, string>()
    for (const entry of entries) {
      map.set(
        entry.eventId,
        `${entry.type} ${entry.category} ${entry.eventId} ${JSON.stringify(entry.payload)}`.toLowerCase(),
      )
    }
    return map
  }, [entries])

  const filtered = useMemo(() => {
    return entries.filter((entry) => {
      if (filters.severity !== 'all' && entry.severity !== filters.severity) return false
      if (filters.rooms.length > 0 && typeof entry.payload.room === 'string' && !filters.rooms.includes(entry.payload.room)) return false
      if (filters.categories.length > 0 && !filters.categories.includes(entry.category)) return false
      if (filters.types.length > 0 && !filters.types.includes(entry.type)) return false
      if (filters.search && !(searchHaystacks.get(entry.eventId) ?? '').includes(filters.search.toLowerCase())) {
        return false
      }
      return true
    })
  }, [entries, filters, searchHaystacks])

  const ordered = useMemo(() => [...filtered].reverse(), [filtered])
  const groups = useMemo(
    () => (view === 'grouped' && expandedCategory === null ? withPreallocatedSlots(buildGroups(ordered)) : []),
    [view, expandedCategory, ordered],
  )
  const expandedListView = useMemo(
    () => (expandedCategory === null ? [] : ordered.filter((entry) => displayCategoryOf(entry) === expandedCategory)),
    [ordered, expandedCategory],
  )

  const handleViewChange = useCallback((next: EventLogView) => {
    setView(next)
    setExpandedCategory(null)
  }, [])

  const showFlatList = view === 'flat' || expandedCategory !== null

  return (
    <section
      aria-labelledby="event-log-heading"
      className={`flex flex-col gap-2 @container ${compact ? 'h-full min-h-0' : ''}`}
    >
      <div className="flex items-center justify-between gap-2">
        <h2 id="event-log-heading" className="text-sm font-bold uppercase tracking-wider text-text-default">
          Event Log
        </h2>
        {compact ? (
          <CompactFiltersTrigger
            filters={filters}
            onChange={handleFilterChange}
            rooms={rooms}
            categories={categories}
            types={types}
            view={view}
            onViewChange={handleViewChange}
            primaryRooms={primaryRooms}
          />
        ) : (
          <span className="text-11 text-text-default tabular-nums">
            {filtered.length} event{filtered.length === 1 ? '' : 's'}
          </span>
        )}
      </div>
      <EventFilters
        filters={filters}
        onChange={handleFilterChange}
        rooms={rooms}
        categories={categories}
        types={types}
        view={view}
        onViewChange={handleViewChange}
        compact={compact}
        primaryRooms={primaryRooms}
        hideCompactTrigger={compact}
      />
      {(paging.hasMore || paging.loadingOlder) && (
        <button
          type="button"
          className="self-center text-xs text-text-default underline disabled:cursor-wait disabled:opacity-60"
          disabled={paging.loadingOlder}
          onClick={() => void loadOlder()}
        >
          {paging.loadingOlder ? 'Loading older events...' : 'Load older'}
        </button>
      )}
      {filtered.length === 0 ? (
        <p className="text-xs text-text-default italic px-3 py-4 text-center">No events yet</p>
      ) : showFlatList ? (
        <div className="flex flex-col gap-2">
          {expandedCategory !== null && (
            <button
              type="button"
              data-testid="event-group-collapse"
              aria-pressed={false}
              onClick={() => setExpandedCategory(null)}
              className="self-start px-2 py-1 text-xs font-semibold border bg-surface-secondary border-border-emphasis text-text-default"
            >
              {'\u2190'} {categoryTheme(expandedCategory).label} / All categories
            </button>
          )}
          <ul role="list" aria-label="Event list" className="flex flex-col gap-px bg-border-subtle border border-border-subtle overflow-auto max-h-150">
            {(expandedCategory !== null ? expandedListView : ordered).map((entry) => (
              <EventRow key={entry.eventId} entry={entry} now={now} />
            ))}
          </ul>
        </div>
      ) : (
        <EventGroupedView groups={groups} now={now} onExpand={handleExpand} compact={compact} />
      )}
    </section>
  )
}
