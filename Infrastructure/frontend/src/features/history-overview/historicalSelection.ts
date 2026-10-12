/**
 * Origin-aware drill-down routing for the Historical monitoring pages.
 *
 * Zoom / click / drag selections are interpreted against the range currently
 * on screen (the range owner's displayed range):
 *
 *  - displayed span longer than seven days (the overview): a selection of
 *    seven days or more stays Historical with its actual span; a shorter
 *    selection snaps to an exactly-seven-day window centred on it and clamped
 *    inside the displayed bounds — still Historical.
 *  - displayed span of at most seven days (the detailed reader, canonical
 *    exactly seven days after the snap or the seven-day preset): any selection
 *    shorter than seven days hands off to regular Monitoring with the exact
 *    selection (floored to the toolbar's five-minute minimum).
 *
 * The five-minute floor and the centre-preserving shift inside the displayed
 * bounds come from `drillDownRange`, so chart clicks and drags and the snap
 * share one geometry contract; only the surface decision and the seven-day
 * snap are added here.
 */
import { MAX_RANGE_MS, MIN_RANGE_MS } from '../monitoring/components/timeRangeToolbar.time'
import type { MonitoringRange } from '../monitoring/state'

import { drillDownRange } from './buckets'

/** Surface a historical chart selection lands on. */
export type HistoricalSelectionSurface = 'history' | 'monitoring'

export interface HistoricalSelection {
  readonly surface: HistoricalSelectionSurface
  readonly start: Date
  readonly end: Date
}

/**
 * Resolve a chart selection made while `displayed` was on screen. Selections
 * are floored to the five-minute toolbar minimum; the returned surface names
 * the page the range owner must show.
 */
export function resolveHistoricalSelection(
  displayed: MonitoringRange,
  start: Date,
  end: Date
): HistoricalSelection {
  const selectionStartMs = start.getTime()
  const selectionEndMs = Math.max(end.getTime(), selectionStartMs + MIN_RANGE_MS)
  const selectionMs = selectionEndMs - selectionStartMs

  if (
    displayed.kind === 'fixed' &&
    displayed.end.getTime() - displayed.start.getTime() > MAX_RANGE_MS
  ) {
    if (selectionMs < MAX_RANGE_MS) {
      const center = Math.floor((selectionStartMs + selectionEndMs) / 2)
      const half = MAX_RANGE_MS / 2
      const snapped = drillDownRange(
        { start: displayed.start, end: displayed.end },
        { start: new Date(center - half), end: new Date(center + half) },
        'historical'
      )
      return { surface: 'history', start: snapped.start, end: snapped.end }
    }
    return { surface: 'history', start: new Date(selectionStartMs), end: new Date(selectionEndMs) }
  }

  if (selectionMs < MAX_RANGE_MS) {
    return {
      surface: 'monitoring',
      start: new Date(selectionStartMs),
      end: new Date(selectionEndMs),
    }
  }
  return { surface: 'history', start: new Date(selectionStartMs), end: new Date(selectionEndMs) }
}
