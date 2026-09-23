import type { SensorSampleMeta } from '../types/sensor'

type LiveDataPoint = {
  value?: number | string | null
  time?: string | number | Date | null
  timestamp?: string | number | Date | null
}

type LiveSensorResponse = {
  data?: LiveDataPoint[]
}

export interface ParsedLiveSnapshot {
  values: Record<string, number>
  meta: Record<string, SensorSampleMeta>
}

function parseObservedAtMs(point: LiveDataPoint): number | null {
  const raw = point.time ?? point.timestamp
  if (raw == null) return null
  const observedAtMs = raw instanceof Date ? raw.getTime() : new Date(raw).getTime()
  return Number.isFinite(observedAtMs) ? observedAtMs : null
}

/**
 * Parse live sensor data while retaining timestamps and invalid-value state.
 * Stale finite samples remain available so the caller can render STALE.
 */
export function parseLiveSnapshot(
  location: string,
  cluster: string,
  liveData: Record<string, LiveSensorResponse> | null | undefined,
  nowMs = Date.now()
): ParsedLiveSnapshot {
  const values: Record<string, number> = {}
  const meta: Record<string, SensorSampleMeta> = {}
  if (!liveData || typeof liveData !== 'object') return { values, meta }

  for (const [sensorType, response] of Object.entries(liveData)) {
    const point =
      Array.isArray(response?.data) && response.data.length > 0 ? response.data[0] : null
    if (!point) continue

    const key = `${location}_${cluster}_${sensorType}`
    const observedAtMs = parseObservedAtMs(point)
    const numericValue = Number(point.value)
    const invalid = point.value == null || !Number.isFinite(numericValue)
    meta[key] = {
      observedAtMs,
      receivedAtMs: nowMs,
      source: 'poll',
      invalid,
    }
    if (!invalid) values[key] = numericValue
  }

  return { values, meta }
}
