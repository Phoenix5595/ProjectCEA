import { SENSOR_STALE_AFTER_MS, type SensorSampleMeta, type ZoneSensorStatus } from '../types/sensor'

/** Aggregated sensor state of the known samples inside one zone prefix. */
export interface ZoneSensorSummary {
  /** At least one received sample carried an invalid (non-finite/absent) value. */
  readonly hasInvalid: boolean
  /** Source of the first matching entry in enumeration order, invalid included. */
  readonly firstSource: SensorSampleMeta['source'] | null
  /** Newest valid entry comparing `observedAtMs ?? receivedAtMs` millisecond stamps. */
  readonly newestValid: SensorSampleMeta | null
  /** Highest non-null `observedAtMs` among valid entries, `null` when never observed. */
  readonly newestObservedAtMs: number | null
}

/**
 * Summarize every `sensorMeta` entry whose key starts with the zone prefix.
 * Entries are visited in enumeration order so ties keep the first sample.
 */
export function summarizeZoneSensors(
  prefix: string,
  sensorMeta: Readonly<Record<string, SensorSampleMeta>>
): ZoneSensorSummary {
  let hasInvalid = false
  let firstSource: SensorSampleMeta['source'] | null = null
  let newestValid: SensorSampleMeta | null = null
  let newestValidMs: number | null = null
  let newestObservedAtMs: number | null = null

  for (const [key, meta] of Object.entries(sensorMeta)) {
    if (!key.startsWith(prefix) || !meta) continue
    if (firstSource === null) firstSource = meta.source ?? null
    if (meta.invalid) {
      hasInvalid = true
      continue
    }
    const sampleMs = meta.observedAtMs ?? meta.receivedAtMs
    if (newestValidMs === null || sampleMs > newestValidMs) {
      newestValid = meta
      newestValidMs = sampleMs
    }
    if (meta.observedAtMs != null && (newestObservedAtMs === null || meta.observedAtMs > newestObservedAtMs)) {
      newestObservedAtMs = meta.observedAtMs
    }
  }

  return { hasInvalid, firstSource, newestValid, newestObservedAtMs }
}

/**
 * Project a zone summary into the dashboard sensor status shape.
 * Without valid samples a zone is `bad` only when it actually received
 * invalid values; otherwise it is `missing`. Samples whose observed
 * timestamp lies in the future are clamped to age 0 and stay `live`
 * until they exceed the stale window.
 */
export function projectZoneSensorStatus(
  summary: ZoneSensorSummary,
  error: string | null,
  nowMs: number
): ZoneSensorStatus {
  if (summary.newestValid === null) {
    return {
      quality: summary.hasInvalid ? 'bad' : 'missing',
      newestObservedAtMs: null,
      ageMs: null,
      source: summary.firstSource,
      error,
    }
  }

  const newestAgeMs = summary.newestValid.observedAtMs ?? summary.newestValid.receivedAtMs
  const ageMs = Math.max(0, nowMs - newestAgeMs)
  return {
    quality: ageMs > SENSOR_STALE_AFTER_MS ? 'stale' : 'live',
    newestObservedAtMs: summary.newestObservedAtMs,
    ageMs,
    source: summary.newestValid.source,
    error,
  }
}
