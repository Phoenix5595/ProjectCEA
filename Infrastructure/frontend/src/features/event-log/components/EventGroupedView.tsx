import { memo } from 'react'
import type { EventLogEntry } from '../state/eventLogStore'
import { getEventDisplay } from '../presentation/eventRegistry'
import { categoryTheme } from '../presentation/categoryTheme'
import { formatRelativeTime, formatLocalTime } from '../presentation/timeFormat'
import { sourcePartsFor } from './EventRow'

/** Pinned trailing window for concurrent-entity summaries (deterministic tests). */
export const GROUPING_WINDOW_MS = 10 * 60 * 1000

export type EventLogView = 'grouped' | 'flat'

interface CategoryGroup {
  category: string
  count: number
  latest: EventLogEntry
  entities: string[]
}

export function buildGroups(orderedNewestFirst: readonly EventLogEntry[]): CategoryGroup[] {
  const byCategory = new Map<
    string,
    { count: number; latest: EventLogEntry; entities: string[] }
  >()
  for (const entry of orderedNewestFirst) {
    const entity = entry.entity?.entityId ?? entry.type
    const existing = byCategory.get(entry.category)
    if (existing) {
      existing.count += 1
      if (!existing.entities.includes(entity)) existing.entities.push(entity)
    } else {
      byCategory.set(entry.category, { count: 1, latest: entry, entities: [entity] })
    }
  }

  const groups: CategoryGroup[] = []
  for (const [category, group] of byCategory) {
    const cutoff = group.latest.occurredAt.getTime() - GROUPING_WINDOW_MS
    const concurrentIds = [...orderedNewestFirst]
      .reverse()
      .filter(
        (entry) =>
          entry.category === category &&
          entry.occurredAt.getTime() >= cutoff &&
          entry.occurredAt.getTime() <= group.latest.occurredAt.getTime(),
      )
      .map((entry) => entry.entity?.entityId ?? entry.type)
      .filter((value, index, all) => all.indexOf(value) === index)
    groups.push({
      category,
      count: group.count,
      latest: group.latest,
      entities: concurrentIds,
    })
  }
  return groups.sort((a, b) => b.latest.occurredAt.getTime() - a.latest.occurredAt.getTime())
}

const GR_COLUMNS_WIDE_THRESHOLD = 4

export function groupedGridClass(groupCount: number): string {
  return groupCount > GR_COLUMNS_WIDE_THRESHOLD ? 'lg:grid-cols-2' : 'grid-cols-1'
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
  const display = getEventDisplay(group.latest.type)
  const sourceParts = sourcePartsFor(group.latest)
  const relative = formatRelativeTime(group.latest.occurredAt, now)
  const absolute = formatLocalTime(group.latest.occurredAt, now)
  const entityCount = group.entities.length

  return (
    <button
      type="button"
      data-testid={`event-group-${group.category}`}
      aria-expanded={false}
      onClick={() => onExpand(group.category)}
      className={`flex flex-col gap-1 px-3 py-2 text-left border-b-2 bg-surface-secondary hover:bg-surface-tertiary transition-colors ${visual.border}`}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`shrink-0 inline-flex items-center px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider border ${visual.chip}`}
          >
            {visual.label}
          </span>
          <span
            aria-label={`${visual.label} event count`}
            className="shrink-0 text-[11px] font-bold text-text-default tabular-nums border border-border-subtle px-1.5"
          >
            {group.count > 1 ? `${group.count} events` : '1 event'}
          </span>
        </div>
        <div className="flex flex-col items-end">
          <time
            dateTime={group.latest.occurredAt.toISOString()}
            className="text-[11px] text-text-default tabular-nums"
          >
            {relative}
          </time>
          <time
            dateTime={group.latest.occurredAt.toISOString()}
            aria-label={`Absolute time: ${absolute}`}
            className="text-[10px] text-text-secondary tabular-nums"
          >
            {absolute}
          </time>
        </div>
      </div>
      <div className="text-sm text-text-default font-semibold truncate">
        {display.label}
      </div>
      {sourceParts.length > 0 && (
        <div className="text-[11px] text-text-default truncate">
          {sourceParts.map((part, index) => (
            <span key={index} className={part.className} title={part.title}>
              {part.text}
              {index < sourceParts.length - 1 && ' \u00b7 '}
            </span>
          ))}
        </div>
      )}
      {group.latest.reasonText !== null && (
        <div className="text-[11px] text-text-default italic truncate">
          {group.latest.reasonText}
        </div>
      )}
      {entityCount > 1 && (
        <div className="text-[11px] text-text-secondary truncate">
          {entityCount} devices in the last 10 minutes: {group.entities.join(', ')}
        </div>
      )}
    </button>
  )
})

export function EventGroupedView({
  groups,
  now,
  onExpand,
}: {
  groups: readonly CategoryGroup[]
  now: Date
  onExpand: (category: string) => void
}) {
  return (
    <div
      role="group"
      aria-label="Grouped alert console"
      className={`grid gap-px bg-border-subtle border border-border-subtle overflow-auto max-h-[600px] ${groupedGridClass(groups.length)}`}
    >
      {groups.map((group) => (
        <EventCategoryRow key={group.category} group={group} now={now} onExpand={onExpand} />
      ))}
    </div>
  )
}
