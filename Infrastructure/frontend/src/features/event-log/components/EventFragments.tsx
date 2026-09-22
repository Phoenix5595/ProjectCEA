import { Flower2, Sprout } from 'lucide-react'
import type { ReactNode } from 'react'
import { formatRelativeTime, formatExactTime, formatLocalTime } from '../presentation/timeFormat'
type SourcePart = { text: string; className?: string; title?: string }

/**
 * The dual visible timestamp pair shared by flat rows and group summaries:
 * relative chip (with dateTime + UTC hover title) plus a visible localized
 * absolute clock — title-only timestamps are not acceptable.
 */
export function EventTimestamps({
  occurredAt,
  now,
  formatAbsolute = formatLocalTime,
}: {
  occurredAt: Date
  now: Date
  formatAbsolute?: (date: Date, now: Date) => string
}): ReactNode {
  const absolute = formatAbsolute(occurredAt, now)
  return (
    <div className="flex flex-col items-end">
      <time
        dateTime={occurredAt.toISOString()}
        title={formatExactTime(occurredAt)}
        className="shrink-0 text-11 text-text-default tabular-nums"
      >
        {formatRelativeTime(occurredAt, now)}
      </time>
      <time
        dateTime={occurredAt.toISOString()}
        aria-label={`Absolute time: ${absolute}`}
        className="shrink-0 text-10 text-text-secondary tabular-nums"
      >
        {absolute}
      </time>
    </div>
  )
}


export function EventRoomIndicator({ room }: { room: unknown }): ReactNode | null {
  if (typeof room !== 'string' || room.length === 0) return null
  const isFlower = room.toLowerCase().includes('flower')
  const isVeg = room.toLowerCase().includes('veg')
  if (!isFlower && !isVeg) return null
  const label = isFlower ? 'Flower Room' : 'Veg Room'
  const Icon = isFlower ? Flower2 : Sprout
  return (
    <span
      className="inline-flex items-center gap-1 text-11 text-text-secondary"
      title={`Event room: ${label}`}
      aria-label={`Event room: ${label}`}
    >
      <Icon aria-hidden="true" className="size-3.5 shrink-0" />
      <span className="sr-only">{label}</span>
    </span>
  )
}
/** The "at a glance" source line: entity · zone · state · controller · from-to values. */
export function EventSourceLine({ parts }: { parts: readonly SourcePart[] }): ReactNode | null {
  if (parts.length === 0) return null
  return (
    <div className="text-11 text-text-default truncate">
      {parts.map((part, index) => (
        <span key={`${part.text}-${index}`} className={part.className} title={part.title}>
          {part.text}
          {index < parts.length - 1 && ' \u00b7 '}
        </span>
      ))}
    </div>
  )
}
