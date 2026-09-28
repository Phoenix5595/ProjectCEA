import { useState, useMemo, useCallback, useEffect, useRef } from 'react'

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'

import { categoryTheme, displayCategoryOf } from '../presentation/categoryTheme'
import { getEventDisplay } from '../presentation/eventRegistry'
import { sourcePartsFor } from '../presentation/eventSourceParts'
import { SEVERITY_LABELS } from '../presentation/severity'
import type { EventLogEntry } from '../state/eventLogStore'

import { EventDetails } from './EventDetails'
import { EventFilters, CompactFiltersTrigger, type FilterState } from './EventFilters'
import { EventTimestamps, EventRoomIndicator } from './EventFragments'
import {
  EventGroupedView,
  buildGroups,
  withPreallocatedSlots,
  type EventLogView,
} from './EventGroupedView'
import { EventRow } from './EventRow'

const COMPACT_EVENT_PAGE_SIZE = 5

interface EventLogProps {
  entries: readonly EventLogEntry[]
  now: Date
  /** Sidebar mode: compact filter row (rooms + Filters submenu) and compact groups. */
  compact?: boolean
  /** Initial presentation mode; full-width logs default to flat, dense logs to grouped. */
  initialView?: EventLogView
  /** Rooms always rendered as chips in compact mode. */
  primaryRooms?: readonly string[]
}

export function EventLog({
  entries,
  now,
  compact = false,
  initialView,
  primaryRooms,
}: EventLogProps) {
  // The dense dashboard keeps its compact grouped console; full-width room logs open as a readable list.
  const [view, setView] = useState<EventLogView>(initialView ?? (compact ? 'grouped' : 'flat'))
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [detailEntry, setDetailEntry] = useState<EventLogEntry | null>(null)
  const detailOpenerRef = useRef<HTMLElement | null>(null)
  const [filters, setFilters] = useState<FilterState>({
    severity: 'all',
    search: '',
    rooms: [],
    categories: [],
    types: [],
  })

  const handleFilterChange = useCallback((next: FilterState) => {
    setFilters(next)
    setPage(1)
  }, [])

  const handleExpand = useCallback((category: string) => {
    setExpandedCategory(current => (current === category ? null : category))
    setPage(1)
  }, [])

  const handleOpenDetail = useCallback((entry: EventLogEntry) => {
    detailOpenerRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    setDetailEntry(entry)
  }, [])

  const rooms = useMemo(
    () =>
      Array.from(
        new Set(
          entries
            .map(entry => entry.payload.room)
            .filter((room): room is string => typeof room === 'string')
        )
      ).sort(),
    [entries]
  )
  const categories = useMemo(
    () => Array.from(new Set(entries.map(entry => entry.category))).sort(),
    [entries]
  )
  const types = useMemo(
    () => Array.from(new Set(entries.map(entry => entry.type))).sort(),
    [entries]
  )

  const searchHaystacks = useMemo(() => {
    const map = new Map<string, string>()
    for (const entry of entries) {
      map.set(
        entry.eventId,
        `${entry.type} ${entry.category} ${entry.eventId} ${JSON.stringify(entry.payload)}`.toLowerCase()
      )
    }
    return map
  }, [entries])

  const filtered = useMemo(() => {
    return entries.filter(entry => {
      if (filters.severity !== 'all' && entry.severity !== filters.severity) return false
      if (
        filters.rooms.length > 0 &&
        typeof entry.payload.room === 'string' &&
        !filters.rooms.includes(entry.payload.room)
      )
        return false
      if (filters.categories.length > 0 && !filters.categories.includes(entry.category))
        return false
      if (filters.types.length > 0 && !filters.types.includes(entry.type)) return false
      if (
        filters.search &&
        !(searchHaystacks.get(entry.eventId) ?? '').includes(filters.search.toLowerCase())
      ) {
        return false
      }
      return true
    })
  }, [entries, filters, searchHaystacks])

  const ordered = useMemo(() => [...filtered].reverse(), [filtered])
  const groups = useMemo(
    () =>
      view === 'grouped' && expandedCategory === null
        ? withPreallocatedSlots(buildGroups(ordered))
        : [],
    [view, expandedCategory, ordered]
  )
  const expandedListView = useMemo(
    () =>
      expandedCategory === null
        ? []
        : ordered.filter(entry => displayCategoryOf(entry) === expandedCategory),
    [ordered, expandedCategory]
  )

  const handleViewChange = useCallback((next: EventLogView) => {
    setView(next)
    setExpandedCategory(null)
    setPage(1)
  }, [])

  const showFlatList = view === 'flat' || expandedCategory !== null
  const listEntries = expandedCategory !== null ? expandedListView : ordered
  const listPageCount = Math.max(1, Math.ceil(listEntries.length / COMPACT_EVENT_PAGE_SIZE))
  const activePageCount = compact && showFlatList ? listPageCount : 1
  const currentPage = Math.min(page, activePageCount)

  useEffect(() => {
    if (compact && page > activePageCount) setPage(activePageCount)
  }, [activePageCount, compact, page])

  const visibleEntries = compact
    ? listEntries.slice(
        (currentPage - 1) * COMPACT_EVENT_PAGE_SIZE,
        currentPage * COMPACT_EVENT_PAGE_SIZE
      )
    : listEntries
  const detailSourceParts = detailEntry === null ? [] : sourcePartsFor(detailEntry)
  const detailDisplay = detailEntry === null ? null : getEventDisplay(detailEntry.type)

  return (
    <section aria-labelledby="event-log-heading" className="flex flex-col gap-2 @container">
      <div className="flex items-center justify-between gap-2">
        <h2
          id="event-log-heading"
          className="text-sm font-bold uppercase tracking-wider text-text-default"
        >
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
      {filtered.length === 0 ? (
        <p className="text-xs text-text-default italic px-3 py-4 text-center">No events yet</p>
      ) : showFlatList ? (
        <div className="flex flex-col gap-2">
          {expandedCategory !== null && (
            <button
              type="button"
              data-testid="event-group-collapse"
              aria-pressed={false}
              onClick={() => {
                setExpandedCategory(null)
                setPage(1)
              }}
              className="self-start px-2 py-1 text-xs font-semibold border bg-surface-secondary border-border-emphasis text-text-default"
            >
              {'\u2190'} {categoryTheme(expandedCategory).label} / All categories
            </button>
          )}
          <ul
            role="list"
            aria-label="Event list"
            className={`flex flex-col gap-px bg-border-subtle border border-border-subtle ${compact ? '' : 'overflow-auto max-h-150'}`}
          >
            {visibleEntries.map(entry => (
              <EventRow
                key={entry.eventId}
                entry={entry}
                now={now}
                onOpenDetail={compact ? handleOpenDetail : undefined}
              />
            ))}
          </ul>
          {compact && (
            <div className="flex items-center justify-between gap-2 text-11 text-text-default">
              <p
                role="status"
                data-testid="event-events-page-status"
                aria-live="polite"
                className="tabular-nums"
              >
                Page {currentPage} of {listPageCount} · {listEntries.length} event
                {listEntries.length === 1 ? '' : 's'}
              </p>
              <div className="flex shrink-0 gap-1">
                <button
                  type="button"
                  aria-label="Previous events page"
                  data-testid="event-events-previous-page"
                  disabled={currentPage === 1}
                  onClick={() => setPage(currentPage - 1)}
                  className="border border-border-subtle px-2 py-1 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Previous
                </button>
                <button
                  type="button"
                  aria-label="Next events page"
                  data-testid="event-events-next-page"
                  disabled={currentPage === listPageCount}
                  onClick={() => setPage(currentPage + 1)}
                  className="border border-border-subtle px-2 py-1 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Next
                </button>
              </div>
            </div>
          )}
        </div>
      ) : compact ? (
        <div className="flex min-h-0 flex-col gap-2">
          <EventGroupedView groups={groups} now={now} onExpand={handleExpand} compact />
        </div>
      ) : (
        <EventGroupedView groups={groups} now={now} onExpand={handleExpand} />
      )}
      <Dialog
        open={compact && detailEntry !== null}
        onOpenChange={open => {
          if (!open) setDetailEntry(null)
        }}
      >
        {detailEntry !== null && detailDisplay !== null && compact && (
          <DialogContent
            className="w-[calc(100vw-2rem)] max-w-2xl max-h-[80vh] overflow-y-auto overscroll-contain bg-surface-primary"
            onCloseAutoFocus={event => {
              const opener = detailOpenerRef.current
              if (opener?.isConnected) {
                event.preventDefault()
                opener.focus()
              }
              detailOpenerRef.current = null
            }}
          >
            <DialogTitle>Event details — {detailDisplay.label}</DialogTitle>
            <DialogDescription className="mb-3">{detailEntry.type}</DialogDescription>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm font-semibold">
                  <EventRoomIndicator room={detailEntry.payload.room} />
                  <span className="break-words">{detailDisplay.label}</span>
                </div>
                <p className="break-words font-mono text-11 text-text-default">
                  {detailEntry.type}
                </p>
                <p className="text-11 font-semibold text-text-secondary">
                  Severity: {SEVERITY_LABELS[detailEntry.severity]}
                </p>
              </div>
              <EventTimestamps occurredAt={detailEntry.occurredAt} now={now} />
            </div>
            {detailSourceParts.length > 0 && (
              <p className="mt-3 break-words text-11 text-text-default">
                {detailSourceParts.map((part, index) => (
                  <span key={`${part.text}-${index}`} className={part.className} title={part.title}>
                    {index > 0 && ' · '}
                    {part.text}
                  </span>
                ))}
              </p>
            )}
            {detailEntry.reasonText !== null && (
              <div className="mt-3 border-t border-border-subtle pt-2">
                <p className="text-11 font-semibold text-text-secondary">Recorded reason</p>
                <p className="whitespace-pre-wrap break-words text-sm text-text-default">
                  {detailEntry.reasonText}
                </p>
              </div>
            )}
            <EventDetails payload={detailEntry.payload} expanded />
          </DialogContent>
        )}
      </Dialog>
    </section>
  )
}
