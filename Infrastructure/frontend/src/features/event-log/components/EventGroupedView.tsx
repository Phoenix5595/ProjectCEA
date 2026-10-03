import { memo } from 'react'

import { displayCategoryOf, categoryTheme } from '../presentation/categoryTheme'
import { getEventDisplay } from '../presentation/eventRegistry'
import { sourcePartsFor } from '../presentation/eventSourceParts'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'
import { formatExactTime, formatLocalTime, formatRelativeTime } from '../presentation/timeFormat'
import type { EventLogEntry } from '../state/eventLogStore'

import {
  EventRoomIndicator,
  EventSourceLine,
  EventTimestamps,
  eventReasonTooltip,
} from './EventFragments'

/** Pinned trailing window for concurrent-entity summaries (deterministic tests). */
export const GROUPING_WINDOW_MS = 10 * 60 * 1000

export type EventLogView = 'grouped' | 'flat'

interface CategoryGroup {
  category: string
  count: number
  latest: EventLogEntry | null
  entities: string[]
}

interface CategoryBag {
  category: string
  count: number
  latest: EventLogEntry
  samples: Array<{ happenedAt: number; entity: string }>
}

/** Six presentation buckets: relay, sensor, control (merged ramp), manual_override, alarm, system (merged mutation). Every bucket occupies its slot so the layout never reflows as events arrive. */
export const DISPLAY_BUCKET_ORDER: readonly string[] = [
  'relay',
  'sensor',
  'control',
  'manual_override',
  'alarm',
  'system',
]

export function withPreallocatedSlots(groups: CategoryGroup[]): CategoryGroup[] {
  const filled = new Map(groups.map(group => [group.category, group]))
  const slots = DISPLAY_BUCKET_ORDER.map<CategoryGroup>(category => {
    const group = filled.get(category)
    return group ?? { category, count: 0, latest: null, entities: [] }
  })
  const extra = groups.filter(group => !DISPLAY_BUCKET_ORDER.includes(group.category))
  return [...slots, ...extra]
}

export function buildGroups(orderedNewestFirst: readonly EventLogEntry[]): CategoryGroup[] {
  const bags = new Map<string, CategoryBag>()
  for (const entry of orderedNewestFirst) {
    const bucket = displayCategoryOf(entry)
    const entity = entry.entity?.entityId ?? entry.type
    let bag = bags.get(bucket)
    if (bag === undefined) {
      bag = { category: bucket, count: 0, latest: entry, samples: [] }
      bags.set(bucket, bag)
    }
    bag.count += 1
    bag.samples.push({ happenedAt: entry.occurredAt.getTime(), entity })
  }

  const groups: CategoryGroup[] = []
  for (const bag of bags.values()) {
    const cutoff = bag.latest.occurredAt.getTime() - GROUPING_WINDOW_MS
    const entities: string[] = []
    for (let index = bag.samples.length - 1; index >= 0; index -= 1) {
      const sample = bag.samples[index]
      if (sample.happenedAt >= cutoff && !entities.includes(sample.entity)) {
        entities.push(sample.entity)
      }
    }
    groups.push({ category: bag.category, count: bag.count, latest: bag.latest, entities })
  }
  return groups
}

export function groupedGridClass(): string {
  // Two columns only when the log CONTAINER is wide (zone pages); the narrow
  // dashboard column stays single-column regardless of viewport width.
  return '@2xl:grid-cols-2'
}

function EventCategoryChip({ category }: { category: string }) {
  const visual = categoryTheme(category)
  return (
    <span
      title={visual.label}
      className={`shrink-0 inline-flex items-center px-2 py-0.5 text-10 font-bold uppercase tracking-wider border ${visual.chip}`}
    >
      {visual.label}
    </span>
  )
}

function EventCountBadge({ label, count }: { label: string; count: number }) {
  return (
    <span
      aria-label={`${label} event count`}
      className="shrink-0 text-11 font-bold text-text-default tabular-nums border border-border-subtle px-1.5"
    >
      {count === 0 ? '0 events' : count > 1 ? `${count} events` : '1 event'}
    </span>
  )
}
const SEVERITY_BADGE: Record<SeverityLevel, string> = {
  critical: 'bg-status-danger-vivid text-status-danger-text border-status-danger-vivid font-bold',
  warning: 'bg-status-warning-bg text-status-warning-text border-status-warning-dim',
  error: 'bg-surface-secondary text-status-danger-text border-status-danger-border',
  info: 'bg-surface-tertiary text-text-default border-border-default',
}

function EventSeverityBadge({ severity }: { severity: SeverityLevel }) {
  return (
    <span
      aria-label={`Severity: ${SEVERITY_LABELS[severity]}`}
      className={`shrink-0 inline-flex items-center px-1 py-0.5 text-10 font-bold uppercase tracking-wider border ${SEVERITY_BADGE[severity]}`}
    >
      {SEVERITY_LABELS[severity]}
    </span>
  )
}

const EventCategoryRow = memo(function EventCategoryRow({
  group,
  now,
  onExpand,
  compact = false,
}: {
  group: CategoryGroup
  now: Date
  onExpand: (category: string) => void
  /** Sidebar mode: three content-sized bands (header, latest event, metadata). */
  compact?: boolean
}) {
  const visual = categoryTheme(group.category)

  if (compact) {
    // Compact card bands: (1) chip left / count right; (2) room + full latest
    // label; (3) severity left / timestamp pair right. Nothing truncates.
    if (group.latest === null || group.count === 0) {
      return (
        <div
          data-testid={`event-group-${group.category}`}
          aria-disabled="true"
          className={`flex min-h-[88px] min-w-0 flex-col gap-1 px-2 py-1.5 text-left border border-border-subtle rounded-sm bg-surface-secondary opacity-60 ${visual.border}`}
        >
          <div className="flex min-w-0 items-center justify-between gap-2">
            <EventCategoryChip category={group.category} />
            <span className="shrink-0 text-11 font-bold text-text-default tabular-nums">0</span>
          </div>
          <div className="min-w-0 break-words text-xs text-text-default font-semibold">
            No recent events
          </div>
        </div>
      )
    }
    const display = getEventDisplay(group.latest.type)
    const reasonTooltip = eventReasonTooltip(group.latest.reasonText, 'latest')
    const rowTitle = reasonTooltip ? `${display.label} — ${reasonTooltip}` : display.label
    return (
      <button
        type="button"
        data-testid={`event-group-${group.category}`}
        aria-expanded={false}
        onClick={() => onExpand(group.category)}
        title={rowTitle}
        className={`flex min-h-[88px] min-w-0 flex-col gap-1 px-2 py-1.5 text-left border border-border-subtle rounded-sm bg-surface-secondary hover:bg-surface-tertiary transition-colors ${visual.border}`}
      >
        <div className="flex min-w-0 items-center justify-between gap-2">
          <EventCategoryChip category={group.category} />
          <span className="shrink-0 text-11 font-bold text-text-default tabular-nums">
            {group.count}
          </span>
        </div>
        <div className="flex min-w-0 items-start gap-1.5 text-xs text-text-default font-semibold">
          <EventRoomIndicator room={group.latest.payload.room} />
          <span className="min-w-0 flex-1 break-words text-left">
            {display.label}
          </span>
        </div>
        <div className="flex min-w-0 items-end justify-between gap-2">
          <EventSeverityBadge severity={group.latest.severity} />
          <time
            dateTime={group.latest.occurredAt.toISOString()}
            title={formatExactTime(group.latest.occurredAt)}
            className="min-w-0 text-right text-10 text-text-secondary tabular-nums leading-tight break-words"
            aria-label={`${formatRelativeTime(group.latest.occurredAt, now)} — absolute time: ${formatLocalTime(group.latest.occurredAt, now)}`}
          >
            <span className="block text-11 text-text-default">{formatRelativeTime(group.latest.occurredAt, now)}</span>
            <span className="block">{formatLocalTime(group.latest.occurredAt, now)}</span>
          </time>
        </div>
      </button>
    )
  }

  if (group.latest === null || group.count === 0) {
    // Pre-allocated empty slot: reserved space, non-interactive placeholder.
    return (
      <div
        data-testid={`event-group-${group.category}`}
        aria-disabled="true"
        className={`flex min-w-0 flex-col gap-1 px-3 py-2 text-left border border-border-subtle rounded-sm bg-surface-secondary opacity-50 ${visual.border}`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <EventCategoryChip category={group.category} />
          <EventCountBadge label={visual.label} count={0} />
        </div>
        <div className="min-w-0 break-words text-sm text-text-default font-semibold">No recent events</div>
      </div>
    )
  }

  const display = getEventDisplay(group.latest.type)
  const sourceParts = sourcePartsFor(group.latest)
  const entityCount = group.entities.length

  return (
    <button
      type="button"
      data-testid={`event-group-${group.category}`}
      aria-expanded={false}
      onClick={() => onExpand(group.category)}
      className={`flex min-w-0 flex-col gap-1 px-3 py-2 text-left border border-border-subtle rounded-sm bg-surface-secondary hover:bg-surface-tertiary transition-colors ${visual.border}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <EventCategoryChip category={group.category} />
          <EventCountBadge label={visual.label} count={group.count} />
        </div>
        <EventTimestamps occurredAt={group.latest.occurredAt} now={now} />
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm text-text-default font-semibold">
        {display.label}
        <EventSeverityBadge severity={group.latest.severity} />
      </div>
      {!compact && (
        <>
          {sourceParts.length > 0 && <EventSourceLine parts={sourceParts} />}
          {group.latest.reasonText !== null && (
            <div
              className="min-w-0 break-words text-11 text-text-default italic"
              title={eventReasonTooltip(group.latest.reasonText, 'latest')}
            >
              {group.latest.reasonText}
            </div>
          )}
          {entityCount > 1 && (
            <div className="min-w-0 break-words text-11 text-text-secondary">
              {entityCount} devices in the last 10 minutes: {group.entities.join(', ')}
            </div>
          )}
        </>
      )}
    </button>
  )
})

export function EventGroupedView({
  groups,
  now,
  onExpand,
  compact = false,
}: {
  groups: readonly CategoryGroup[]
  now: Date
  onExpand: (category: string) => void
  /** Sidebar mode: three content-sized bands (header, latest event, metadata). */
  compact?: boolean
}) {
  const visibleGroups =
    compact && groups.length > DISPLAY_BUCKET_ORDER.length
      ? groups.slice(0, DISPLAY_BUCKET_ORDER.length)
      : groups

  return (
    <div
      role="group"
      aria-label="Grouped alert console"
      className={`grid min-w-0 gap-1 bg-surface-base border border-border-subtle rounded-sm ${
        compact ? 'grid-rows-[repeat(6,minmax(min-content,1fr))]' : ''
      } ${compact ? '' : groupedGridClass()}`}
    >
      {visibleGroups.map(group => (
        <EventCategoryRow
          key={group.category}
          group={group}
          now={now}
          onExpand={onExpand}
          compact={compact}
        />
      ))}
    </div>
  )
}
