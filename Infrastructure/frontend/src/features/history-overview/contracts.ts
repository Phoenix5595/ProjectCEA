/**
 * Frontend contracts for the bounded all-history monitoring overview
 * endpoints, mirroring
 * `Infrastructure/monitoring-service/monitoring_service/history_overview_models.py`.
 *
 * The schemas reject any response whose buckets, counts, or duration
 * invariants disagree with the request (or with the backend model rules)
 * before the caller can publish it, so a stale or mismatched payload can
 * never repaint an overview view.
 */
import { z } from 'zod'

import { RoomMetadata, UnitFamily, utcDate } from '../monitoring/api'
import type { ControlOverviewScope } from '../monitoring/api'

import {
  MAX_REQUESTED_POINTS,
  MIN_REQUESTED_POINTS,
  chooseHistoryBucket,
  historyBucketBounds,
  isHistoryBucketSeconds,
} from './buckets'

// ---------------------------------------------------------------------------
// Request contract
// ---------------------------------------------------------------------------

/** Absolute fixed request carried by every overview parse call. */
export interface HistoryOverviewRequest {
  readonly range: { readonly start: Date; readonly end: Date }
  readonly maxPoints: number
}

const SENSOR_MIN_DURATION_MS = 1_000
const CONTROL_MIN_DURATION_MS = 5 * 60 * 1_000

// ---------------------------------------------------------------------------
// Bucket metadata
// ---------------------------------------------------------------------------

export const HistoryBucketSchema = z
  .object({
    kind: z.literal('fixed'),
    seconds: z
      .number()
      .int()
      .refine(
        isHistoryBucketSeconds,
        'overview bucket seconds must be a whole hour between 1 and 12'
      ),
  })
  .strict()
export type HistoryBucket = z.infer<typeof HistoryBucketSchema>

// ---------------------------------------------------------------------------
// Points and series
// ---------------------------------------------------------------------------

const finite = z.number().finite()

export const HistoryOverviewPoint = z
  .object({
    start: utcDate,
    end: utcDate,
    average: finite.nullable(),
    minimum: finite.nullable(),
    maximum: finite.nullable(),
    sample_count: z.number().int().nonnegative(),
    partial: z.boolean(),
  })
  .strict()
  .superRefine((point, context) => {
    if (point.end.getTime() <= point.start.getTime()) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'overview point end must be later than its start',
      })
      return
    }
    if (
      point.sample_count === 0 &&
      point.average === null &&
      point.minimum === null &&
      point.maximum === null
    ) {
      return
    }
    if (point.average === null || point.minimum === null || point.maximum === null) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'populated overview points must set average, minimum and maximum',
      })
      return
    }
    if (point.minimum > point.average || point.average > point.maximum) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'overview point minimum, average and maximum must be ordered',
      })
    }
  })
export type HistoryOverviewPoint = z.infer<typeof HistoryOverviewPoint>

const HistoryOverviewSamplePoint = HistoryOverviewPoint.superRefine((point, context) => {
  if (
    point.sample_count === 0 &&
    (point.average !== null || point.minimum !== null || point.maximum !== null)
  ) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'zero-count overview points must leave average, minimum and maximum unset',
    })
  }
  if (
    point.sample_count === 1 &&
    !(point.minimum === point.average && point.average === point.maximum)
  ) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: 'single-sample overview points must set average, minimum and maximum equally',
    })
  }
})

export const HistoryOverviewRelayPoint = z
  .object({
    start: utcDate,
    end: utcDate,
    on_seconds: finite,
    off_seconds: finite,
    unknown_seconds: finite,
    coverage_complete: z.boolean(),
    partial: z.boolean(),
  })
  .strict()
  .superRefine((point, context) => {
    if (point.end.getTime() <= point.start.getTime()) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'relay point end must be later than its start',
      })
      return
    }
    const elapsedUs = (point.end.getTime() - point.start.getTime()) * 1_000
    const totalUs =
      Math.round(point.on_seconds * 1_000_000) +
      Math.round(point.off_seconds * 1_000_000) +
      Math.round(point.unknown_seconds * 1_000_000)
    const minSeconds = Math.min(point.on_seconds, point.off_seconds, point.unknown_seconds)
    if (minSeconds < 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'relay point durations cannot be negative',
      })
      return
    }
    if (
      Math.abs(totalUs - elapsedUs) > 1 ||
      !Number.isSafeInteger(Math.round(point.on_seconds * 1_000_000)) ||
      !Number.isSafeInteger(Math.round(point.off_seconds * 1_000_000)) ||
      !Number.isSafeInteger(Math.round(point.unknown_seconds * 1_000_000))
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          'relay point on, off and unknown durations must sum to the bucket duration within one microsecond',
      })
    }
    if (point.coverage_complete && point.unknown_seconds !== 0) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'complete relay coverage cannot report unknown time',
      })
    }
  })
export type HistoryOverviewRelayPoint = z.infer<typeof HistoryOverviewRelayPoint>

/** Identity box shared by location-scoped control series (mirror of backend fields). */
const ControlIdentity = z
  .object({
    location: z.string().min(1),
    cluster: z.string().min(1),
    device_name: z.string().min(1),
  })
  .strict()

export const HistoryOverviewSensorSeries = z
  .object({
    sensor: z.string().min(1),
    node: z.string().min(1),
    unit: z.string().min(1),
    unit_family: UnitFamily,
    points: z.array(HistoryOverviewSamplePoint),
  })
  .strict()
export type HistoryOverviewSensorSeries = z.infer<typeof HistoryOverviewSensorSeries>

export const HistoryOverviewControlSeries = ControlIdentity.extend({
  points: z.array(HistoryOverviewSamplePoint),
}).strict()
export type HistoryOverviewControlSeries = z.infer<typeof HistoryOverviewControlSeries>
export const HistoryOverviewRequestedPidSeries = ControlIdentity.extend({
  points: z.array(HistoryOverviewPoint),
}).strict()
export type HistoryOverviewRequestedPidSeries = z.infer<
  typeof HistoryOverviewRequestedPidSeries
>

export const HistoryOverviewRelaySeries = ControlIdentity.extend({
  channel: z.number().int().min(0).max(15),
  device_id: z.number().int().nonnegative(),
  device_type: z.string().min(1),
  points: z.array(HistoryOverviewRelayPoint),
}).strict()
export type HistoryOverviewRelaySeries = z.infer<typeof HistoryOverviewRelaySeries>

// ---------------------------------------------------------------------------
// Range-level statistics (zero/one sample stay nullable)
// ---------------------------------------------------------------------------

export const HistoryOverviewSensorStatistics = z
  .object({
    sensor: z.string().min(1),
    node: z.string().min(1),
    sample_count: z.number().int().nonnegative(),
    minimum: finite.nullable(),
    maximum: finite.nullable(),
    average: finite.nullable(),
    stddev_samp: finite.nullable(),
    stddev_quality: z.enum(['exact', 'approximate']).default('exact'),
  })
  .strict()
  .superRefine((stats, context) => {
    if (stats.sample_count === 0) {
      if (
        stats.minimum !== null ||
        stats.maximum !== null ||
        stats.average !== null ||
        stats.stddev_samp !== null
      ) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            'zero-count overview statistics must leave minimum, maximum, average and stddev_samp unset',
        })
      }
      return
    }
    if (stats.minimum === null || stats.maximum === null || stats.average === null) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'populated overview statistics must set minimum, maximum and average',
      })
      return
    }
    if (
      stats.minimum > stats.maximum ||
      !(stats.minimum <= stats.average && stats.average <= stats.maximum)
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'overview statistics minimum, average and maximum must be ordered',
      })
    }
    if (stats.sample_count === 1) {
      if (!(stats.minimum === stats.average && stats.average === stats.maximum)) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            'single-sample overview statistics must set minimum, average and maximum to the same value',
        })
      }
      if (stats.stddev_samp !== 0.0) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: 'single-sample overview statistics must report zero deviation',
        })
      }
      return
    }
    if (stats.stddev_samp === null) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'multi-sample overview statistics must report a nonnegative deviation',
      })
    }
  })
export type HistoryOverviewSensorStatistics = z.infer<typeof HistoryOverviewSensorStatistics>

// ---------------------------------------------------------------------------
// Response envelopes
// ---------------------------------------------------------------------------

export const HistoryOverviewMetadata = z
  .object({
    generated_at: utcDate,
    range: z.object({ start: utcDate, end: utcDate }).strict(),
    room: RoomMetadata,
    requested_max_points: z.number().int().min(MIN_REQUESTED_POINTS).max(MAX_REQUESTED_POINTS),
    bucket: HistoryBucketSchema,
    source_coverage: z.enum(['ready', 'unavailable']),
    all_aggregated: z.literal(true),
  })
  .strict()
export type HistoryOverviewMetadata = z.infer<typeof HistoryOverviewMetadata>

export const HistoryOverviewSensorResponse = z
  .object({
    metadata: HistoryOverviewMetadata,
    series: z.array(HistoryOverviewSensorSeries),
    statistics: z.array(HistoryOverviewSensorStatistics),
  })
  .strict()
export type HistoryOverviewSensorResponse = z.infer<typeof HistoryOverviewSensorResponse>

export const HistoryOverviewControlResponse = z
  .object({
    metadata: HistoryOverviewMetadata,
    lights: z.array(HistoryOverviewControlSeries),
    requested_pid: z.array(HistoryOverviewRequestedPidSeries),
    relays: z.array(HistoryOverviewRelaySeries),
  })
  .strict()
export type HistoryOverviewControlResponse = z.infer<typeof HistoryOverviewControlResponse>

// ---------------------------------------------------------------------------
// Request-bound factories: reject mismatched responses before any publish
// ---------------------------------------------------------------------------

function bumpPoints(context: z.RefinementCtx, message: string): void {
  context.addIssue({ code: z.ZodIssueCode.custom, message })
}

function refineMetadata(
  metadata: z.infer<typeof HistoryOverviewMetadata>,
  request: HistoryOverviewRequest,
  minimumDurationMs: number,
  context: z.RefinementCtx
): boolean {
  const requestStartMs = request.range.start.getTime()
  const requestEndMs = request.range.end.getTime()
  const rangeValid =
    requestEndMs > requestStartMs &&
    Number.isFinite(requestStartMs) &&
    Number.isFinite(requestEndMs)
  if (!rangeValid) {
    bumpPoints(context, 'overview request range is not an ordered finite interval')
    return false
  }
  if (
    metadata.range.start.getTime() !== requestStartMs ||
    metadata.range.end.getTime() !== requestEndMs
  ) {
    bumpPoints(context, 'overview metadata range does not match the requested range')
    return false
  }
  if (metadata.range.end.getTime() - metadata.range.start.getTime() < minimumDurationMs) {
    bumpPoints(context, 'overview metadata range is shorter than its reader minimum')
    return false
  }
  if (metadata.requested_max_points !== request.maxPoints) {
    bumpPoints(context, 'overview metadata requested_max_points does not match the request')
    return false
  }
  let expectedBucket
  try {
    expectedBucket = chooseHistoryBucket(request.range.start, request.range.end, request.maxPoints)
  } catch {
    bumpPoints(context, 'overview bucket selection is unavailable for the requested range')
    return false
  }
  if (metadata.bucket.seconds !== expectedBucket.seconds) {
    bumpPoints(context, 'overview bucket does not match the range-selected bucket')
    return false
  }
  return true
}

/** Validate one series' points against the exact backend bucket lattice. */
function refineSeriesPoints(
  points: readonly { start: Date; end: Date; partial: boolean }[],
  metadata: z.infer<typeof HistoryOverviewMetadata>,
  context: z.RefinementCtx,
  seriesLabel: string
): void {
  if (points.length > metadata.requested_max_points) {
    bumpPoints(context, `${seriesLabel} exceeds the requested point budget`)
  }
  const rangeStartMs = metadata.range.start.getTime()
  const rangeEndMs = metadata.range.end.getTime()
  const lattice = new Map<number, { end: Date; partial: boolean }>()
  for (const entry of historyBucketBounds(
    new Date(rangeStartMs),
    new Date(rangeEndMs),
    metadata.bucket
  )) {
    lattice.set(entry.start.getTime(), { end: entry.end, partial: entry.partial })
  }
  let previous: { start: Date; end: Date } | null = null
  for (const point of points) {
    if (previous !== null && point.start.getTime() < previous.end.getTime()) {
      bumpPoints(context, 'overview points must be ordered and nonoverlapping')
      continue
    }
    previous = point
    if (point.start.getTime() < rangeStartMs || point.end.getTime() > rangeEndMs) {
      bumpPoints(context, 'overview points must stay inside the requested range')
      continue
    }
    const expected = lattice.get(point.start.getTime())
    if (
      expected === undefined ||
      expected.end.getTime() !== point.end.getTime() ||
      expected.partial !== point.partial
    ) {
      bumpPoints(context, 'overview points must match the requested bucket lattice')
    }
  }
}

function addResponseRefinement<TResponse>(
  contract: z.ZodType<TResponse>,
  refine: (response: TResponse, context: z.RefinementCtx) => void
): z.ZodType<TResponse> {
  return contract.superRefine(refine)
}

/** Parse-and-bound contract for `/api/sensors/monitoring/overview/{room}`. */
export function historyOverviewSensorContract(
  request: HistoryOverviewRequest
): z.ZodType<HistoryOverviewSensorResponse> {
  return addResponseRefinement(HistoryOverviewSensorResponse, (response, context) => {
    if (!refineMetadata(response.metadata, request, SENSOR_MIN_DURATION_MS, context)) return
    for (const series of response.series) {
      refineSeriesPoints(
        series.points,
        response.metadata,
        context,
        `sensor series ${series.sensor}`
      )
    }
  })
}

/**
 * Parse-and-bound contract for `/api/monitoring/control/{room}/overview`.
 * A `lights` request accepts only light series: populated `requested_pid`
 * and `relays` groups are rejected so a stale or full-scope backend cannot
 * feed automation curves into room Monitoring/Historical overview charts.
 */
export function historyOverviewControlContract(
  request: HistoryOverviewRequest,
  scope: ControlOverviewScope = 'all'
): z.ZodType<HistoryOverviewControlResponse> {
  return addResponseRefinement(HistoryOverviewControlResponse, (response, context) => {
    if (!refineMetadata(response.metadata, request, CONTROL_MIN_DURATION_MS, context)) return
    if (scope === 'lights' && (response.requested_pid.length > 0 || response.relays.length > 0)) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'lights overview must not return requested-PID or relay series',
      })
    }
    const groups: readonly [string, z.infer<typeof HistoryOverviewControlResponse>['lights']][] = [
      ['light', response.lights],
      ['requested PID', response.requested_pid],
    ]
    for (const [label, seriesGroup] of groups) {
      for (const series of seriesGroup) {
        refineSeriesPoints(
          series.points,
          response.metadata,
          context,
          `${label} series ${series.device_name}`
        )
      }
    }
    for (const relay of response.relays) {
      refineSeriesPoints(
        relay.points,
        response.metadata,
        context,
        `relay series ${relay.device_name}`
      )
    }
  })
}
