/**
 * Deterministic history-overview bucket selection and exact aligned bounds.
 *
 * This is the frontend mirror of
 * `Infrastructure/monitoring-service/monitoring_service/history_buckets.py`.
 * The fixture server (`vite.monitoring.config.ts`), the overview contracts,
 * and the aligned overview builder must all use this one picker so a preview
 * (or a test) can never drift from the real backend selection.
 *
 * Display buckets are whole-hour epoch-aligned widths from 1 to 12 hours.
 * Selection returns the finest width whose actual intersecting count (clipped
 * edges included) fits the point budget; when even the 12-hour cap cannot fit
 * the range, selection raises instead of widening the bucket or the budget.
 */

export const MIN_REQUESTED_POINTS = 10
export const MAX_REQUESTED_POINTS = 1000

/** Whole-hour display widths, finest first; 12 hours is the hard cap. */
export const HISTORY_BUCKET_WIDTHS_SECONDS = [
  3600, 7200, 10800, 14400, 18000, 21600, 25200, 28800, 32400, 36000, 39600, 43200,
] as const

export type HistoryBucketSeconds = (typeof HISTORY_BUCKET_WIDTHS_SECONDS)[number]

/** Whole-hour width membership; rejects every coarser or sub-hour width. */
export function isHistoryBucketSeconds(value: number): value is HistoryBucketSeconds {
  return (HISTORY_BUCKET_WIDTHS_SECONDS as readonly number[]).includes(value)
}

/** Monitoring 400 detail when no whole-hour display width fits the budget. */
const UNFIT_RANGE_MESSAGE =
  'history range and point budget cannot fit the maximum 12-hour bucket size'

/** Sentinel bounds for an explicit width clamp on drill-down selections. */
export const MIN_DETAIL_DURATION_MS = 5 * 60 * 1_000
export const MAX_DETAIL_DURATION_MS = 7 * 24 * 60 * 60 * 1_000
/** A plain click (no width) drills down to a one-day detail window. */
const CLICK_DETAIL_DURATION_MS = 24 * 60 * 60 * 1_000

/** Raised for invalid aware ranges or out-of-domain point budgets. */
export class HistoryOverviewRangeError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'HistoryOverviewRangeError'
  }
}

/** Metadata identity of the selected overview bucket. */
export type HistoryBucketMetadata = {
  readonly kind: 'fixed'
  readonly seconds: HistoryBucketSeconds
}

/** One intersecting, requested-range-clipped bucket edge pair. */
export interface HistoryBucketBoundsEntry {
  readonly start: Date
  readonly end: Date
  readonly partial: boolean
}

export interface HistoryBucketBounds {
  readonly bucket: HistoryBucketMetadata
  readonly buckets: readonly HistoryBucketBoundsEntry[]
}

/** Any aware absolute `[start, end)` pair, at matching Date instants. */
export interface AbsoluteRangeLike {
  readonly start: Date
  readonly end: Date
}

function requireInstant(value: Date, field: string): Date {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    throw new HistoryOverviewRangeError(`bucket range ${field} must be a valid datetime`)
  }
  return value
}

interface OrderedRange {
  readonly start: Date
  readonly end: Date
  readonly startMs: number
  readonly endMs: number
}

/** Python-style floor division on BigInt (JS `/` truncates toward zero). */
function floorDiv(value: bigint, divisor: bigint): bigint {
  const quotient = value / divisor
  if (value % divisor === 0n) return quotient
  return quotient < 0n ? quotient - 1n : quotient
}

function requireOrderedRange(start: Date, end: Date): OrderedRange {
  const startInstant = requireInstant(start, 'start')
  const endInstant = requireInstant(end, 'end')
  const startMs = startInstant.getTime()
  const endMs = endInstant.getTime()
  if (endMs <= startMs) {
    throw new HistoryOverviewRangeError('range end must be later than range start')
  }
  return { start: startInstant, end: endInstant, startMs, endMs }
}

/**
 * Validate a point budget against the overview bounds and return it.
 *
 * Mirrors `validate_history_budget`: an integer in [10, 1000], else 400.
 */
export function validateHistoryBudget(maxPoints: number): number {
  if (!Number.isInteger(maxPoints)) {
    throw new HistoryOverviewRangeError(
      'requested_max_points must be an integer between 10 and 1000'
    )
  }
  if (maxPoints < MIN_REQUESTED_POINTS || maxPoints > MAX_REQUESTED_POINTS) {
    throw new HistoryOverviewRangeError('requested_max_points must be between 10 and 1000')
  }
  return maxPoints
}

/**
 * Select the finest whole-hour overview bucket whose intersecting count fits
 * the budget.
 *
 * Mirrors `choose_history_bucket`: widths are tried finest first over the
 * exact epoch-microsecond lattice, so clipped outer buckets count like any
 * other intersection. When even the 12-hour maximum does not fit, selection
 * raises `HistoryOverviewRangeError` (a monitoring 400) instead of widening
 * the bucket or silently raising the budget.
 */
export function chooseHistoryBucket(
  start: Date,
  end: Date,
  maxPoints: number
): HistoryBucketMetadata {
  const budget = validateHistoryBudget(maxPoints)
  const { start: startUtc, end: endUtc } = requireOrderedRange(start, end)

  // BigInt epoch microseconds keep the intersection count exact even though
  // request edges carry at most millisecond precision in the JS domain.
  const startUs = BigInt(startUtc.getTime()) * 1_000n
  const endUs = BigInt(endUtc.getTime()) * 1_000n

  for (const seconds of HISTORY_BUCKET_WIDTHS_SECONDS) {
    const stepUs = BigInt(seconds) * 1_000_000n
    if (floorDiv(endUs - 1n, stepUs) - floorDiv(startUs, stepUs) + 1n <= BigInt(budget)) {
      return { kind: 'fixed', seconds }
    }
  }
  throw new HistoryOverviewRangeError(UNFIT_RANGE_MESSAGE)
}

/**
 * Exact `(clippedStart, clippedEnd, partial)` for each bucket intersecting a
 * preselected bucket shape. Mirrors `history_bucket_bounds`; outer buckets are
 * clipped into the requested half-open range and flagged partial. Only the
 * whole-hour display widths 1..12 are honored; any other metadata is rejected
 * instead of silently widening the lattice.
 */
export function historyBucketBounds(
  start: Date,
  end: Date,
  bucket: HistoryBucketMetadata
): HistoryBucketBoundsEntry[] {
  const { startMs, endMs } = requireOrderedRange(start, end)
  if (!isHistoryBucketSeconds(bucket.seconds)) {
    // Defensive for untyped callers; typed metadata cannot carry this shape.
    throw new HistoryOverviewRangeError(
      'overview bucket width must be a fixed whole hour between 1 and 12'
    )
  }
  const stepUs = BigInt(bucket.seconds) * 1_000_000n
  const startUs = BigInt(startMs) * 1_000n
  const endUs = BigInt(endMs) * 1_000n
  const firstBucketUs = floorDiv(startUs, stepUs) * stepUs
  const lastBucketUs = floorDiv(endUs - 1n, stepUs) * stepUs
  const bounds: HistoryBucketBoundsEntry[] = []
  let bucketUs = firstBucketUs
  for (;;) {
    const bucketEndUs = bucketUs + stepUs
    const clippedStartUs = bucketUs > startUs ? bucketUs : startUs
    const clippedEndUs = bucketEndUs < endUs ? bucketEndUs : endUs
    bounds.push({
      start: new Date(Number(clippedStartUs / 1000n)),
      end: new Date(Number(clippedEndUs / 1000n)),
      partial: bucketUs < startUs || bucketEndUs > endUs,
    })
    if (bucketUs >= lastBucketUs) break
    bucketUs = bucketEndUs
  }
  return bounds
}

/**
 * Choose the survey bucket for a range/budget, then return its exact bounds.
 *
 * Mirrors the shared backend contract: the selected bucket matches corridor
 * `choose_history_bucket`, clipped edges stay inside the requested range and
 * remain representable (JS request edges never carry sub-millisecond parts).
 */
export function historyOverviewBucketBounds(
  start: Date,
  end: Date,
  maxPoints: number
): HistoryBucketBounds {
  const bucket = chooseHistoryBucket(start, end, maxPoints)
  return { bucket, buckets: historyBucketBounds(start, end, bucket) }
}

/**
 * Drill-down range from a parent overview range and a user selection.
 *
 * The returned `[start, end)` keeps the selection center and a five-minute
 * floor (a zero-width click becomes one day), shifted inside the parent.
 * Detail mode caps the span at seven days; Historical mode preserves wider
 * selections so the range owner can choose the appropriate graph and apply
 * its two-stage seven-day minimum.
 */
export function drillDownRange(
  parent: AbsoluteRangeLike,
  selection: AbsoluteRangeLike,
  mode: 'detail' | 'historical' = 'detail'
): { start: Date; end: Date } {
  const parentRange = requireOrderedRange(parent.start, parent.end)
  const selectionRange = {
    startMs: requireInstant(selection.start, 'selection start').getTime(),
    endMs: requireInstant(selection.end, 'selection end').getTime(),
  }
  const selectionMs = selectionRange.endMs - selectionRange.startMs
  if (selectionMs < 0) {
    throw new HistoryOverviewRangeError('selection end must not precede selection start')
  }
  if (parentRange.endMs - parentRange.startMs < MIN_DETAIL_DURATION_MS) {
    throw new HistoryOverviewRangeError('detail parent must span at least 5 minutes')
  }

  let desiredMs = selectionMs
  if (desiredMs <= 0) {
    // A click has no meaningful width; pick a usable one-day context window.
    desiredMs = CLICK_DETAIL_DURATION_MS
  } else if (desiredMs < MIN_DETAIL_DURATION_MS) {
    desiredMs = MIN_DETAIL_DURATION_MS
  } else if (mode === 'detail' && desiredMs > MAX_DETAIL_DURATION_MS) {
    desiredMs = MAX_DETAIL_DURATION_MS
  }
  const parentMs = parentRange.endMs - parentRange.startMs
  if (desiredMs > parentMs) desiredMs = parentMs

  const center = Math.floor((selectionRange.startMs + selectionRange.endMs) / 2)
  const half = desiredMs / 2
  let startMs = center - half
  let endMs = startMs + desiredMs
  if (startMs < parentRange.startMs) {
    startMs = parentRange.startMs
    endMs = startMs + desiredMs
  }
  if (endMs > parentRange.endMs) {
    endMs = parentRange.endMs
    startMs = endMs - desiredMs
  }
  if (startMs < parentRange.startMs) {
    // Parent narrower than the desired window: return the parent itself.
    startMs = parentRange.startMs
  }
  return { start: new Date(startMs), end: new Date(endMs) }
}
