/**
 * Shared monitoring API primitives: branded identifiers, provenance enums,
 * UTC timestamp normalization, and the range/provenance/projection/cursor
 * contracts shared by the sensor and control monitoring APIs.
 *
 * Every timestamp on the wire is a UTC ISO 8601 string with a trailing `Z`.
 * `utcDate` parses it once into a `Date` at the boundary, so downstream
 * consumers never re-parse or re-normalize a timestamp.
 */
import { z } from 'zod/v3'

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

/** How a timeline value entered the response. */
export const Origin = z.enum(['recorded', 'derived', 'projected'])
export type Origin = z.infer<typeof Origin>

/** Confidence in a timeline value independently of its origin. */
export const Quality = z.enum(['exact', 'estimated', 'unavailable'])
export type Quality = z.infer<typeof Quality>

/** The resolved photoperiod phase for a room. */
export const Phase = z.enum(['SUN', 'MOON', 'UNKNOWN'])
export type Phase = z.infer<typeof Phase>

/** Sensor series aggregation tier. */
export const Tier = z.enum(['raw', '1min', '5min'])
export type Tier = z.infer<typeof Tier>

/** Canonical sensor unit family. */
export const UnitFamily = z.enum(['celsius', 'percent', 'kpa', 'ppm', 'hpa', 'mm'])
export type UnitFamily = z.infer<typeof UnitFamily>

// ---------------------------------------------------------------------------
// UTC timestamp normalization (once, at the boundary)
// ---------------------------------------------------------------------------

const UTC_ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/

/** Parse a UTC ISO 8601 `Z` string into a `Date` exactly once. */
export const utcDate = z
  .string()
  .regex(UTC_ISO_RE, 'timestamp must be UTC ISO 8601 with a trailing Z')
  .transform((value) => new Date(value))
  .refine((date) => !Number.isNaN(date.getTime()), 'timestamp is not a valid UTC date')
export type UtcDate = z.infer<typeof utcDate>

// ---------------------------------------------------------------------------
// Shared range / provenance / projection / cursor contracts
// ---------------------------------------------------------------------------

/** Validated aware-UTC half-open interval `[start, end)`. */
export const MonitoringRange = z.object({
  start: utcDate,
  end: utcDate,
})
export type MonitoringRange = z.infer<typeof MonitoringRange>

/** Orthogonal source, confidence, and aggregation facts for timeline values. */
export const TimelineProvenance = z.object({
  origin: Origin,
  quality: Quality,
  is_aggregated: z.boolean(),
})
export type TimelineProvenance = z.infer<typeof TimelineProvenance>

/** Source bucket information retained when a timeline series is aggregated. */
export const AggregationMetadata = z.object({
  interval_seconds: z.number().int().positive(),
  sample_count: z.number().int().nonnegative(),
})
export type AggregationMetadata = z.infer<typeof AggregationMetadata>

/** Stable provenance captured with every projected control series. */
export const ProjectionMetadata = z.object({
  projection_revision: z.string().min(1),
  anchor_fingerprint: z.string().min(1),
  anchor_observed_at: utcDate,
  anchor_quality: Quality,
  anchor_valid_until: utcDate,
})
export type ProjectionMetadata = z.infer<typeof ProjectionMetadata>

/** A non-fatal reason a control timeline is estimated or incomplete. */
export const MonitoringWarning = z.object({
  code: z.string().min(1),
  detail: z.string().min(1),
})
export type MonitoringWarning = z.infer<typeof MonitoringWarning>

/** Per-source flush cursor and bounded-page state. */
export const SourceCursor = z.object({
  source: z.string().min(1),
  cursor: z.string().nullable().optional(),
  has_more: z.boolean(),
})
export type SourceCursor = z.infer<typeof SourceCursor>

/** Persisted-history flush status, including rows dropped before storage. */
export const FlushHealth = z.object({
  source: z.string().min(1),
  dropped_rows: z.number().int().nonnegative(),
  last_flushed_at: utcDate.nullable().optional(),
  healthy: z.boolean(),
})
export type FlushHealth = z.infer<typeof FlushHealth>
