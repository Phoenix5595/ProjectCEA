import { memo } from 'react'

import {
  EVENT_BUCKET_ORDERS,
  type EventBucketCount,
  displayCategoryOf,
  categoryTheme,
} from '../presentation/categoryTheme'
import { getEventDisplay } from '../presentation/eventRegistry'
import { eventDetailLine } from '../presentation/eventSourceParts'
import { SEVERITY_LABELS, type SeverityLevel } from '../presentation/severity'
import { formatExactTime, formatLocalTime, formatRelativeTime } from '../presentation/timeFormat'
import type { EventLogEntry } from '../state/eventLogStore'

import { EventRoomIndicator, eventReasonTooltip } from './EventFragments'

/** Pinned trailing window for concurrent-entity summaries (deterministic tests). */
export const GROUPING_WINDOW_MS = 10 * 60 * 1000

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

export function withPreallocatedSlots(
  groups: CategoryGroup[],
  bucketCount: EventBucketCount
): CategoryGroup[] {
  const order = EVENT_BUCKET_ORDERS[bucketCount]
  const filled = new Map(groups.map(group => [group.category, group]))
  const slots = order.map<CategoryGroup>(category => {
    const group = filled.get(category)
    return group ?? { category, count: 0, latest: null, entities: [] }
  })
  const extra = groups.filter(group => !order.includes(group.category))
  return [...slots, ...extra]
}

export function buildGroups(
  orderedNewestFirst: readonly EventLogEntry[],
  bucketCount: EventBucketCount
): CategoryGroup[] {
  const bags = new Map<string, CategoryBag>()
  for (const entry of orderedNewestFirst) {
    const bucket = displayCategoryOf(entry, bucketCount)
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
}: {
  group: CategoryGroup
  now: Date
  onExpand: (category: string) => void
}) {
  const visual = categoryTheme(group.category)

  // Card bands: (1) chip left / count right; (2) room + latest label;
  // (3) one-line detail; (4) severity left / timestamp pair right.
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
  const detailLine = eventDetailLine(group.latest)
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
        <span className="min-w-0 flex-1 break-words text-left">{display.label}</span>
      </div>
      <span
        data-testid="event-group-detail"
        className="block min-w-0 truncate text-11 text-text-secondary"
        title={detailLine}
      >
        {detailLine}
      </span>
      <div className="flex min-w-0 items-end justify-between gap-2">
        <EventSeverityBadge severity={group.latest.severity} />
        <time
          dateTime={group.latest.occurredAt.toISOString()}
          title={formatExactTime(group.latest.occurredAt)}
          className="min-w-0 text-right text-10 text-text-secondary tabular-nums leading-tight break-words"
          aria-label={`${formatRelativeTime(group.latest.occurredAt, now)} — absolute time: ${formatLocalTime(group.latest.occurredAt, now)}`}
        >
          <span className="block text-11 text-text-default">
            {formatRelativeTime(group.latest.occurredAt, now)}
          </span>
          <span className="block">{formatLocalTime(group.latest.occurredAt, now)}</span>
        </time>
      </div>
    </button>
  )
})

export function EventGroupedView({
  groups,
  now,
  onExpand,
  bucketCount,
}: {
  groups: readonly CategoryGroup[]
  now: Date
  onExpand: (category: string) => void
  /** Height-adaptive bucket policy driving the slot order and row count. */
  bucketCount: EventBucketCount
}) {
  return (
    <div
      role="group"
      aria-label="Grouped alert console"
      className={`grid min-w-0 grow gap-1 bg-surface-base border border-border-subtle rounded-sm ${
        bucketCount === 8
          ? 'grid-rows-[repeat(8,minmax(min-content,1fr))]'
          : 'grid-rows-[repeat(6,minmax(min-content,1fr))]'
      }`}
    >
      {groups.map(group => (
        <EventCategoryRow key={group.category} group={group} now={now} onExpand={onExpand} />
      ))}
    </div>
  )
}
