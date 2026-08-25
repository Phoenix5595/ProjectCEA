/**
 * Sensor monitoring contracts mirroring the backend
 * `/api/sensors/monitoring/{range,live,stats}` response shapes.
 */
import { z } from 'zod/v3'
import { MonitoringRange, Tier, UnitFamily, utcDate } from './shared'

/** Average and envelope for one timestamp on the shared UTC grid. */
export const SeriesPoint = z.object({
  timestamp: utcDate,
  average: z.number(),
  minimum: z.number(),
  maximum: z.number(),
  sample_count: z.number().int().nonnegative(),
})
export type SeriesPoint = z.infer<typeof SeriesPoint>

/** One named sensor series and its UTC-aligned points. */
export const SensorSeries = z.object({
  sensor: z.string().min(1),
  node: z.string().min(1),
  unit_family: UnitFamily,
  unit: z.string().min(1),
  points: z.array(SeriesPoint),
  point_count: z.number().int().nonnegative().optional(),
  sample_count_total: z.number().int().nonnegative().optional(),
})
export type SensorSeries = z.infer<typeof SensorSeries>

/** Exact SQL MIN, MAX, AVG, and STDDEV_SAMP statistics for one sensor. */
export const SensorStatistics = z.object({
  sensor: z.string().min(1),
  node: z.string().min(1),
  minimum: z.number(),
  maximum: z.number(),
  average: z.number(),
  stddev_samp: z.number().nonnegative(),
  sample_count: z.number().int().nonnegative(),
  stddev_quality: z.enum(['exact', 'approximate']).optional(),
})
export type SensorStatistics = z.infer<typeof SensorStatistics>

/** Canonical room and ordered sensor nodes selected for monitoring. */
export const RoomMetadata = z.object({
  room: z.string().min(1),
  nodes: z.array(z.string().min(1)),
})
export type RoomMetadata = z.infer<typeof RoomMetadata>

/** Generation, tier, range, and topology metadata for a response. */
export const MonitoringMetadata = z.object({
  generated_at: utcDate,
  tier: Tier,
  range: MonitoringRange,
  room: RoomMetadata,
  requested_max_points: z.number().int().min(10).max(100_000).optional(),
  interval_seconds: z.number().int().positive().optional(),
})
export type MonitoringMetadata = z.infer<typeof MonitoringMetadata>

/** Complete sensor monitoring range/stats response. */
export const MonitoringResponse = z.object({
  metadata: MonitoringMetadata,
  series: z.array(SensorSeries),
  statistics: z.array(SensorStatistics),
})
export type MonitoringResponse = z.infer<typeof MonitoringResponse>

/** A current Redis value with an aware UTC observation timestamp. */
export const LiveSensorValue = z.object({
  sensor: z.string().min(1),
  value: z.number(),
  timestamp: utcDate,
})
export type LiveSensorValue = z.infer<typeof LiveSensorValue>

/** The live endpoint returns a JSON array of live values. */
export const LiveSensorValues = z.array(LiveSensorValue)
export type LiveSensorValues = z.infer<typeof LiveSensorValues>
