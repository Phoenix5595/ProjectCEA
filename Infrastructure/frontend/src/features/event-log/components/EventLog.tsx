import { useState, useMemo, useCallback } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { classifySeverity } from '../presentation/severity'
import { EventFilters, type FilterState } from './EventFilters'
import { EventRow } from './EventRow'
import { useEventLogPagination } from '../state/useEventLog'

interface EventLogProps {
  entries: readonly EventLogEntry[]
  now: Date
}

export function EventLog({ entries, now }: EventLogProps) {
  const { paging, loadOlder } = useEventLogPagination()
  const [filters, setFilters] = useState<FilterState>({
    severity: 'all',
    search: '',
    rooms: [],
    categories: [],
    types: [],
  })

  const handleFilterChange = useCallback((next: FilterState) => setFilters(next), [])

  const rooms = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.payload.room).filter((room): room is string => typeof room === 'string'))).sort(),
    [entries],
  )
  const categories = useMemo(() => Array.from(new Set(entries.map((entry) => entry.category))).sort(), [entries])
  const types = useMemo(() => Array.from(new Set(entries.map((entry) => entry.type))).sort(), [entries])

  const filtered = useMemo(() => {
    return entries.filter((entry) => {
      if (filters.severity !== 'all' && classifySeverity(entry.type) !== filters.severity) return false
      if (filters.rooms.length > 0 && typeof entry.payload.room === 'string' && !filters.rooms.includes(entry.payload.room)) return false
      if (filters.categories.length > 0 && !filters.categories.includes(entry.category)) return false
      if (filters.types.length > 0 && !filters.types.includes(entry.type)) return false
      if (filters.search) {
        const needle = filters.search.toLowerCase()
        const haystack = `${entry.type} ${entry.category} ${entry.eventId} ${JSON.stringify(entry.payload)}`.toLowerCase()
        if (!haystack.includes(needle)) return false
      }
      return true
    })
  }, [entries, filters])

  return (
    <section aria-labelledby="event-log-heading" className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 id="event-log-heading" className="text-sm font-bold uppercase tracking-wider text-text-default">
          Event Log
        </h2>
        <span className="text-[11px] text-text-default tabular-nums">
          {filtered.length} event{filtered.length === 1 ? '' : 's'}
        </span>
      </div>
      <EventFilters filters={filters} onChange={handleFilterChange} rooms={rooms} categories={categories} types={types} />
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
      ) : (
        <ul role="list" className="flex flex-col gap-px bg-border-subtle border border-border-subtle overflow-auto max-h-[600px]">
          {filtered.map((entry) => (
            <EventRow key={entry.eventId} entry={entry} now={now} />
          ))}
        </ul>
      )}
    </section>
  )
}
