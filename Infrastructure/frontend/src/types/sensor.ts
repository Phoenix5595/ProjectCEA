/** Types for sensor data. */

export interface SensorData {
  sensor: string
  value: number
  time: string
  unit: string
}

export interface SensorDataResponse {
  [sensorName: string]: {
    data: Array<{ time: string; value: number }>
    unit: string
    sensor_name: string
  }
}
export type SensorSource = 'poll' | 'websocket'

export type SensorQuality = 'live' | 'stale' | 'bad' | 'missing'

export interface SensorSampleMeta {
  observedAtMs: number | null
  receivedAtMs: number
  source: SensorSource
  invalid: boolean
}

export interface ZoneSensorStatus {
  quality: SensorQuality
  newestObservedAtMs: number | null
  ageMs: number | null
  source: SensorSource | null
  error: string | null
}

export const SENSOR_STALE_AFTER_MS = 45_000
