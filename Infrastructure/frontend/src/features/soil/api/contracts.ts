/**
 * Zod contracts mirroring the backend sensor-registry API
 * (`/api/sensors/registry*`, `/api/sensors/soil/*`).
 *
 * The backend is the authority (`app/sensor_registry_models.py`); these
 * schemas parse the wire once at the boundary. Timestamps are aware ISO
 * 8601 (`Z` or `+00:00`) and are parsed into `Date` exactly once.
 */
import { z } from 'zod/v3'

const ISO_AWARE_SHAPE =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/

/** Parse an aware ISO 8601 timestamp into a `Date` exactly once. */
export const awareDate = z
  .string()
  .regex(ISO_AWARE_SHAPE, 'timestamp must be an aware ISO 8601 instant')
  .transform((value) => new Date(value))
  .refine((date) => !Number.isNaN(date.getTime()), 'timestamp is not a valid date')

// ---------------------------------------------------------------------------
// Registry records
// ---------------------------------------------------------------------------

export const RegistryBus = z.enum(['can', 'rs485'])
export type RegistryBus = z.infer<typeof RegistryBus>

export const CanLocation = z.enum(['front', 'back', 'main'])
export type CanLocation = z.infer<typeof CanLocation>

export const Rs485Bed = z.enum(['Front Bed', 'Back Bed'])
export type Rs485Bed = z.infer<typeof Rs485Bed>

export const RegistryAssignment = z.discriminatedUnion('kind', [
  z.object({
    kind: z.literal('can'),
    room: z.string(),
    location_in_room: CanLocation,
  }),
  z.object({
    kind: z.literal('rs485'),
    room: z.string(),
    bed: z.string(),
  }),
])
export type RegistryAssignment = z.infer<typeof RegistryAssignment>

export const SensorRegistryRecord = z.object({
  registry_id: z.number().int(),
  bus: RegistryBus,
  hardware_address: z.number().int().positive(),
  display_name: z.string(),
  status: z.enum(['assigned', 'unassigned']),
  first_seen: awareDate,
  last_seen: awareDate,
  assignment: RegistryAssignment.nullable(),
})
export type SensorRegistryRecord = z.infer<typeof SensorRegistryRecord>

export const SensorRegistryList = z.object({
  records: z.array(SensorRegistryRecord),
  unassigned_count: z.number().int().nonnegative(),
})
export type SensorRegistryList = z.infer<typeof SensorRegistryList>

// ---------------------------------------------------------------------------
// Soil live
// ---------------------------------------------------------------------------

export const SoilMetricName = z.enum(['temperature', 'water_content', 'ec', 'ph'])
export type SoilMetricName = z.infer<typeof SoilMetricName>

export const SoilMetricValue = z.object({
  value: z.number(),
  unit: z.string(),
  observed_at: awareDate,
  age_seconds: z.number().nonnegative(),
})
export type SoilMetricValue = z.infer<typeof SoilMetricValue>

export const SoilMetricsRecord = z.object({
  temperature: SoilMetricValue.nullable(),
  water_content: SoilMetricValue.nullable(),
  ec: SoilMetricValue.nullable(),
  ph: SoilMetricValue.nullable(),
})

export const SoilProbeLive = z.object({
  registry_id: z.number().int(),
  hardware_address: z.number().int().positive(),
  display_name: z.string(),
  bed: z.string(),
  last_seen: awareDate,
  metrics: SoilMetricsRecord,
})
export type SoilProbeLive = z.infer<typeof SoilProbeLive>

export const SoilLiveResponse = z.object({
  generated_at: awareDate,
  probes: z.array(SoilProbeLive),
})
export type SoilLiveResponse = z.infer<typeof SoilLiveResponse>

// ---------------------------------------------------------------------------
// Soil history
// ---------------------------------------------------------------------------

export const SoilHistoryPoint = z.object({
  bucket_start: awareDate,
  average: z.number().nullable(),
  minimum: z.number().nullable(),
  maximum: z.number().nullable(),
  sample_count: z.number().int().positive(),
})
export type SoilHistoryPoint = z.infer<typeof SoilHistoryPoint>

export const SoilMetricHistory = z.object({
  registry_id: z.number().int(),
  hardware_address: z.number().int().positive(),
  display_name: z.string(),
  bed: z.string(),
  metric: SoilMetricName,
  unit: z.string(),
  points: z.array(SoilHistoryPoint),
})
export type SoilMetricHistory = z.infer<typeof SoilMetricHistory>

export const SoilHistoryResponse = z.object({
  start: awareDate,
  end: awareDate,
  max_points: z.number().int().positive(),
  tier: z.string(),
  bucket_seconds: z.number().int().positive(),
  series: z.array(SoilMetricHistory),
})
export type SoilHistoryResponse = z.infer<typeof SoilHistoryResponse>

// ---------------------------------------------------------------------------
// Assignment request bodies
// ---------------------------------------------------------------------------

export const CanAssignmentRequest = z.object({
  kind: z.literal('can'),
  room: z.string().min(1),
  location_in_room: CanLocation,
})
export type CanAssignmentRequest = z.infer<typeof CanAssignmentRequest>

export const Rs485AssignmentRequest = z.object({
  kind: z.literal('rs485'),
  bed: Rs485Bed,
})
export type Rs485AssignmentRequest = z.infer<typeof Rs485AssignmentRequest>

export type AssignmentRequest = CanAssignmentRequest | Rs485AssignmentRequest

/** Operator-facing metric labels used across the soil surfaces. */
export const SOIL_METRIC_LABELS: Record<SoilMetricName, string> = {
  temperature: 'Temperature',
  water_content: 'Water content',
  ec: 'EC',
  ph: 'pH',
}
