import { useState, useMemo, useCallback, useEffect, useRef, type KeyboardEvent } from 'react'

import {
  categoryTheme,
  displayCategoryOf,
  type EventBucketCount,
} from '../presentation/categoryTheme'
import { getEventDisplay } from '../presentation/eventRegistry'
import { sourcePartsFor } from '../presentation/eventSourceParts'
import { SEVERITY_LABELS } from '../presentation/severity'
import type { EventLogEntry } from '../state/eventLogStore'

import { EventDetails } from './EventDetails'
import { EventTimestamps, EventRoomIndicator } from './EventFragments'
import { EventGroupedView, buildGroups, withPreallocatedSlots } from './EventGroupedView'
import { EventRow } from './EventRow'

const COMPACT_EVENT_PAGE_SIZE = 5

interface EventLogProps {
  entries: readonly EventLogEntry[]
  now: Date
  /** Dashboard mode: category cards that open a paged list. */
  compact?: boolean
  /** Height-adaptive dashboard bucket policy; defaults to the standing six buckets. */
  bucketCount?: EventBucketCount
}

export function EventLog({ entries, now, compact = false, bucketCount = 6 }: EventLogProps) {
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [detailEntry, setDetailEntry] = useState<EventLogEntry | null>(null)
  const detailOpenerRef = useRef<HTMLElement | null>(null)
  const detailHeadingRef = useRef<HTMLHeadingElement>(null)

  const handleExpand = useCallback((category: string) => {
    setExpandedCategory(current => (current === category ? null : category))
    setPage(1)
  }, [])

  const closeDetail = useCallback(() => {
    setDetailEntry(null)
    const opener = detailOpenerRef.current
    if (opener?.isConnected) opener.focus()
    detailOpenerRef.current = null
  }, [])

  const handleOpenDetail = useCallback((entry: EventLogEntry) => {
    detailOpenerRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    setDetailEntry(entry)
  }, [])

  // A stale detail must not survive a context change that removes its row.
  useEffect(() => {
    setDetailEntry(null)
    detailOpenerRef.current = null
  }, [compact, expandedCategory, entries, bucketCount])

  // A bucket-count transition redefines category meaning: the open category,
  // paging position and inline detail must not represent the old policy.
  // A same-policy re-render is a no-op.
  useEffect(() => {
    setExpandedCategory(null)
    setPage(1)
    setDetailEntry(null)
    detailOpenerRef.current = null
  }, [bucketCount])

  // Newly selected details move focus to their inline heading.
  useEffect(() => {
    if (detailEntry !== null) detailHeadingRef.current?.focus()
  }, [detailEntry])

  const handleDetailKeyDown = useCallback(
    (event: KeyboardEvent<Element>) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        closeDetail()
      }
    },
    [closeDetail]
  )

  const ordered = useMemo(() => [...entries].reverse(), [entries])
  const groups = useMemo(
    () =>
      compact && expandedCategory === null
        ? withPreallocatedSlots(buildGroups(ordered, bucketCount), bucketCount)
        : [],
    [compact, expandedCategory, ordered, bucketCount]
  )
  const expandedListView = useMemo(
    () =>
      expandedCategory === null
        ? []
        : ordered.filter(entry => displayCategoryOf(entry, bucketCount) === expandedCategory),
    [ordered, expandedCategory, bucketCount]
  )

  const showFlatList = !compact || expandedCategory !== null
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
  const detailCategoryTheme = detailEntry === null ? null : categoryTheme(detailEntry.category)

  return (
    <section
      aria-labelledby="event-log-heading"
      className="flex min-h-0 flex-1 flex-col gap-2 @container"
    >
      <h2
        id="event-log-heading"
        className="text-sm font-bold uppercase tracking-wider text-text-default"
      >
        Event Log
      </h2>
      <div
        data-testid="event-log-body"
        className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto"
      >
        {entries.length === 0 ? (
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
              className="flex flex-col gap-px bg-border-subtle border border-border-subtle"
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
        ) : (
          <div className="flex min-w-0 grow shrink-0 flex-col gap-2">
            <EventGroupedView
              groups={groups}
              now={now}
              onExpand={handleExpand}
              bucketCount={bucketCount}
            />
          </div>
        )}
        {compact &&
          detailEntry !== null &&
          detailDisplay !== null &&
          detailCategoryTheme !== null && (
            <section
              aria-label="Selected event details"
              data-testid="event-inline-detail"
              onKeyDown={handleDetailKeyDown}
              className="mt-2 flex flex-col gap-2 border border-border-subtle rounded-sm bg-surface-secondary p-3"
            >
              <div className="flex min-w-0 items-start justify-between gap-2">
                <h3
                  ref={detailHeadingRef}
                  tabIndex={-1}
                  className="min-w-0 break-words text-sm font-bold uppercase tracking-wider text-text-default outline-none"
                >
                  Event details — {detailDisplay.label}
                </h3>
                <button
                  type="button"
                  data-testid="event-detail-close"
                  onClick={closeDetail}
                  className="shrink-0 px-2 py-1 text-xs font-semibold border bg-surface-secondary border-border-subtle text-text-default hover:border-border-default"
                >
                  Close event details
                </button>
              </div>
              <p className="min-w-0 break-words font-mono text-11 text-text-default">
                {detailEntry.type}
              </p>
              <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm font-semibold">
                    <EventRoomIndicator room={detailEntry.payload.room} />
                    <span className="min-w-0 break-words">{detailDisplay.label}</span>
                  </div>
                  <p className="text-11 font-semibold text-text-secondary">
                    Category: {detailCategoryTheme.label} ({detailEntry.category})
                  </p>
                  <p className="text-11 font-semibold text-text-secondary">
                    Severity: {SEVERITY_LABELS[detailEntry.severity]}
                  </p>
                </div>
                <EventTimestamps occurredAt={detailEntry.occurredAt} now={now} />
              </div>
              {detailSourceParts.length > 0 && (
                <p className="min-w-0 break-words text-11 text-text-default">
                  {detailSourceParts.map((part, index) => (
                    <span
                      key={`${part.text}-${index}`}
                      className={part.className}
                      title={part.title}
                    >
                      {index > 0 && ' · '}
                      {part.text}
                    </span>
                  ))}
                </p>
              )}
              {detailEntry.reasonText !== null && (
                <div className="border-t border-border-subtle pt-2">
                  <p className="text-11 font-semibold text-text-secondary">Recorded reason</p>
                  <p className="min-w-0 whitespace-pre-wrap break-words text-sm text-text-default">
                    {detailEntry.reasonText}
                  </p>
                </div>
              )}
              <div className="pl-0 [&>div]:mt-0 [&>div]:pl-0">
                <EventDetails payload={detailEntry.payload} expanded />
              </div>
            </section>
          )}
      </div>
    </section>
  )
}
