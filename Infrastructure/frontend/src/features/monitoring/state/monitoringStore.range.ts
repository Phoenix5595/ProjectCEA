import type { MonitoringRange } from './monitoringStore.types'

import { sameRange } from './monitoringStore.merge'

export function rangeBounds(range: MonitoringRange, now: Date): { start: Date; end: Date } {
  if (range.kind === 'fixed') return { start: range.start, end: range.end }
  return { start: new Date(now.getTime() - range.duration), end: now }
}

export function rangeChanged(range: MonitoringRange, previous: MonitoringRange | undefined): boolean {
  return previous === undefined || !sameRange(range, previous)
}
