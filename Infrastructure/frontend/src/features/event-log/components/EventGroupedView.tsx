import { memo } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { getEventDisplay } from '../presentation/eventRegistry'
import { displayCategoryOf, categoryTheme } from '../presentation/categoryTheme'
import { sourcePartsFor } from '../presentation/eventSourceParts'
import { formatExactTime, formatLocalTime, formatRelativeTime } from '../presentation/timeFormat'
import { EventRoomIndicator, EventSourceLine, EventTimestamps } from './EventFragments'

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

/** Fixed 2x4 grid: every bucket always occupies its slot so the layout never reflows as events arrive. Row-major pairs: (relay, sensor), (ramp, control), (manual_override, mutation), (alarm, system). */
export const DISPLAY_BUCKET_ORDER: readonly string[] = [
  'relay',
  'sensor',
  'ramp',
  'control',
  'manual_override',
  'mutation',
  'alarm',
  'system',
]

export function withPreallocatedSlots(groups: CategoryGroup[]): CategoryGroup[] {
  const filled = new Map(groups.map((group) => [group.category, group]))
  const known = new Set(DISPLAY_BUCKET_ORDER)
  const slots = DISPLAY_BUCKET_ORDER.map<CategoryGroup>((category) => {
    const group = filled.get(category)
    return group ?? { category, count: 0, latest: null, entities: [] }
  })
  const extra = groups.filter((group) => !known.has(group.category))
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

const EventCategoryRow = memo(function EventCategoryRow({
  group,
  now,
  onExpand,
  compact = false,
}: {
  group: CategoryGroup
  now: Date
  onExpand: (category: string) => void
  /** Sidebar mode: header + latest title only, so every category fits. */
  compact?: boolean
}) {
  const visual = categoryTheme(group.category)

  if (compact) {
    // Sidebar row: chip + count on the left, compact visible time right,
    // latest title below. Every category stays one glanceable unit.
    if (group.latest === null || group.count === 0) {
      return (
        <div
          data-testid={`event-group-${group.category}`}
          aria-disabled="true"
          className={`flex min-h-0 flex-col gap-0.5 overflow-hidden px-2 py-1.5 text-left border border-border-subtle rounded-sm bg-surface-secondary opacity-60 ${visual.border}`}
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <EventCategoryChip category={group.category} />
            <span className="text-11 font-bold text-text-default tabular-nums">0</span>
          </div>
          <div className="text-xs text-text-default font-semibold truncate">No recent events</div>
        </div>
      )
    }
    const display = getEventDisplay(group.latest.type)
    return (
      <button
        type="button"
        data-testid={`event-group-${group.category}`}
        aria-expanded={false}
        onClick={() => onExpand(group.category)}
        className={`flex min-h-0 flex-col gap-0.5 overflow-hidden px-2 py-1.5 text-left border border-border-subtle rounded-sm bg-surface-secondary hover:bg-surface-tertiary transition-colors ${visual.border}`}
      >
        <div className="flex items-center justify-between gap-1.5 min-w-0">
          <div className="flex items-center gap-1.5 min-w-0">
            <EventCategoryChip category={group.category} />
            <span className="text-11 font-bold text-text-default tabular-nums shrink-0">
              {group.count}
            </span>
          </div>
          <time
            dateTime={group.latest.occurredAt.toISOString()}
            title={formatExactTime(group.latest.occurredAt)}
            className="shrink-0 text-10 text-text-secondary tabular-nums truncate"
            aria-label={`${formatRelativeTime(group.latest.occurredAt, now)} — absolute time: ${formatLocalTime(group.latest.occurredAt, now)}`}
          >
            {formatRelativeTime(group.latest.occurredAt, now)}
            {' · '}
            {formatLocalTime(group.latest.occurredAt, now)}
          </time>
        </div>
        <div className="flex items-center gap-1.5 min-w-0">
          <EventRoomIndicator room={group.latest.payload.room} />
          <div className="text-xs text-text-default font-semibold truncate">{display.label}</div>
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
        className={`flex min-h-0 flex-col gap-1 overflow-hidden px-3 py-2 text-left border border-border-subtle rounded-sm bg-surface-secondary opacity-50 ${visual.border}`}
      >
        <div className="flex items-center gap-2 min-w-0">
          <EventCategoryChip category={group.category} />
          <EventCountBadge label={visual.label} count={0} />
        </div>
        <div className="text-sm text-text-default font-semibold truncate">No recent events</div>
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
      className={`flex min-h-0 flex-col gap-1 overflow-hidden px-3 py-2 text-left border border-border-subtle rounded-sm bg-surface-secondary hover:bg-surface-tertiary transition-colors ${visual.border}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <EventCategoryChip category={group.category} />
          <EventCountBadge label={visual.label} count={group.count} />
        </div>
        <EventTimestamps occurredAt={group.latest.occurredAt} now={now} />
      </div>
      <div className="text-sm text-text-default font-semibold truncate">
        {display.label}
      </div>
      {!compact && (
        <>
          {sourceParts.length > 0 && <EventSourceLine parts={sourceParts} />}
          {group.latest.reasonText !== null && (
            <div className="text-11 text-text-default italic truncate">
              {group.latest.reasonText}
            </div>
          )}
          {entityCount > 1 && (
            <div className="text-11 text-text-secondary truncate">
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
  /** Sidebar mode: one compact row per category, no height cap, no reflow. */
  compact?: boolean
}) {
  return (
    <div
      role="group"
      aria-label="Grouped alert console"
      className={`grid min-h-0 gap-1 bg-surface-base border border-border-subtle rounded-sm ${
        compact ? 'flex-1 grid-rows-[repeat(8,minmax(0,1fr))] overflow-hidden' : 'max-h-150 overflow-auto'
      } ${compact ? '' : groupedGridClass()}`}
    >
      {groups.map((group) => (
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
