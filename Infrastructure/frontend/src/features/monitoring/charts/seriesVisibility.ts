/**
 * Shared series-visibility registry for the monitoring feature.
 *
 * The rail sensor boxes and the chart legends must agree on which series are
 * drawn, so hidden-series state lives here instead of inside one chart. Each
 * chart registers the series keys (+ legend colors) present in its aligned
 * data; rows and legend buttons toggle against those keys. Registration is
 * additive across charts, so cross-chart series stay independent.
 */

export interface SeriesVisibilitySnapshot {
  readonly revision: number
  readonly hidden: ReadonlySet<string>
  readonly known: ReadonlyMap<string, string>
}

let revision = 0
const hidden = new Set<string>()
const colors = new Map<string, string>()
let snapshot: SeriesVisibilitySnapshot = { revision: 0, hidden: new Set(), known: new Map() }
const listeners = new Set<() => void>()

function emit(): void {
  revision += 1
  snapshot = { revision, hidden: new Set(hidden), known: new Map(colors) }
  for (const listener of listeners) listener()
}

export function registerSeriesEntries(entries: ReadonlyArray<{ key: string; color: string }>): void {
  let changed = false
  for (const { key, color } of entries) {
    if (!colors.has(key)) {
      colors.set(key, color)
      changed = true
    }
  }
  if (changed) emit()
}

export function isSeriesHidden(key: string): boolean {
  return hidden.has(key)
}

export function seriesColorFor(key: string): string | undefined {
  return colors.get(key)
}

export function toggleSeries(key: string): void {
  if (hidden.has(key)) hidden.delete(key)
  else hidden.add(key)
  emit()
}

/** Restore visibility for the given keys (a chart's own series only). */
export function resetSeriesVisibility(keys: readonly string[]): void {
  let changed = false
  for (const key of keys) {
    if (hidden.has(key)) {
      hidden.delete(key)
      changed = true
    }
  }
  if (changed) emit()
}

export function getSeriesVisibilitySnapshot(): SeriesVisibilitySnapshot {
  return snapshot
}

export function subscribeSeriesVisibility(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
