import type { Device } from '../../types/device'
import type { ControlSnapshotResponse } from '../../services/api/devices'
import type { SensorQuality, SensorSampleMeta, SensorSource } from '../../types/sensor'
import type { SensorSeries } from '../../features/monitoring/api/contracts'
import { SENSOR_STALE_AFTER_MS } from '../../types/sensor'

export type ClimateMetric = 'temperature' | 'rh' | 'vpd' | 'co2'

export interface TrendPoint {
  timestampMs: number
  value: number
}

export interface TrendMetric {
  metric: ClimateMetric
  label: string
  unit: string
  points: TrendPoint[]
  delta10m: number | null
}

export type TrendData = Record<string, Partial<Record<ClimateMetric, TrendMetric>>>

export interface RoomSensorStatus {
  quality: SensorQuality
  newestAgeMs: number | null
  source: SensorSource | null
  cluster: string | null
}

export type TemperatureState = 'low' | 'high' | 'in_band' | 'missing'

export interface RoomDecisionLayer {
  cluster: string
  label: string
  temperature: number | null
  heatingSetpoint: number | null
  coolingSetpoint: number | null
  temperatureState: TemperatureState
  temperatureDelta: number | null
  vpd: number | null
  vpdSetpoint: number | null
  vpdDelta: number | null
  co2: number | null
  co2Setpoint: number | null
  co2Delta: number | null
  breachMinutes: number | null
  breachFullWindow: boolean
  correctingDevice: string | null
}

export interface RoomDecisionSummary {
  headline: string
  headlineKind: 'temperature' | 'device' | 'vpd' | 'in_band'
  layer: RoomDecisionLayer | null
  layers: RoomDecisionLayer[]
  correctingDevice: string | null
}

export type RoomControlMode = 'FAILSAFE' | 'MANUAL TIMED' | 'MANUAL OFF' | 'SCHEDULED' | 'AUTO'

export interface RoomControlContext {
  mode: RoomControlMode
  manualExpiresAt: string | null
  syncing: boolean
  mismatch: boolean
  mismatchDevices: string[]
  interlockReasons: string[]
  degraded: boolean
}

const METRIC_ALIASES: Record<ClimateMetric, string[]> = {
  temperature: [
    'dry_bulb_f',
    'dry_bulb_b',
    'dry_bulb',
    'temperature_sensor',
    'lab_temp',
    'temperature',
    'temp',
  ],
  rh: ['rh_f', 'rh_b', 'relative_humidity', 'rh', 'humidity'],
  vpd: ['vpd_f', 'vpd_b', 'vpd'],
  co2: ['co2_f', 'co2_b', 'co2'],
}

const METRIC_LABELS: Record<ClimateMetric, { label: string; unit: string }> = {
  temperature: { label: 'Temperature', unit: '°C' },
  rh: { label: 'RH', unit: '%' },
  vpd: { label: 'VPD', unit: 'kPa' },
  co2: { label: 'CO₂', unit: 'ppm' },
}

function valueFor(
  sensorData: Record<string, number>,
  location: string,
  cluster: string,
  metric: ClimateMetric
): number | null {
  for (const alias of METRIC_ALIASES[metric]) {
    const value = sensorData[`${location}_${cluster}_${alias}`]
    if (value == null || !Number.isFinite(Number(value))) continue
    const numeric = Number(value)
    if (metric === 'temperature' && numeric > 100) return ((numeric - 32) * 5) / 9
    return numeric
  }
  return null
}

function setpointFor(
  sensorData: Record<string, number>,
  location: string,
  metric: ClimateMetric
): number | null {
  const suffix = metric === 'temperature' ? 'heating_setpoint' : `${metric}_setpoint`
  const values = [
    sensorData[`${location}_main_${suffix}`],
    sensorData[`${location}_front_${suffix}`],
    sensorData[`${location}_back_${suffix}`],
  ]
  const value = values.find(candidate => candidate != null && Number.isFinite(Number(candidate)))
  return value == null ? null : Number(value)
}

function coolingSetpointFor(sensorData: Record<string, number>, location: string): number | null {
  const values = [
    sensorData[`${location}_main_cooling_setpoint`],
    sensorData[`${location}_front_cooling_setpoint`],
    sensorData[`${location}_back_cooling_setpoint`],
  ]
  const value = values.find(candidate => candidate != null && Number.isFinite(Number(candidate)))
  return value == null ? null : Number(value)
}

function continuousBreach(
  trend: TrendMetric | undefined,
  heatingSetpoint: number | null,
  coolingSetpoint: number | null,
  state: TemperatureState
): { minutes: number | null; fullWindow: boolean } {
  if (state !== 'low' && state !== 'high') return { minutes: null, fullWindow: false }
  if (!trend || trend.points.length === 0) return { minutes: 0, fullWindow: false }

  const isBreach = (value: number) =>
    state === 'low'
      ? heatingSetpoint != null && value < heatingSetpoint
      : coolingSetpoint != null && value > coolingSetpoint
  const points = [...trend.points].sort((a, b) => a.timestampMs - b.timestampMs)
  let startIndex = points.length - 1
  while (startIndex > 0 && isBreach(points[startIndex - 1].value)) startIndex -= 1
  if (!isBreach(points[startIndex].value)) return { minutes: 0, fullWindow: false }

  const durationMs = points[points.length - 1].timestampMs - points[startIndex].timestampMs
  const windowMs = points[points.length - 1].timestampMs - points[0].timestampMs
  return {
    minutes: Math.max(0, Math.round(durationMs / 60_000)),
    fullWindow: startIndex === 0 && windowMs >= 55 * 60_000,
  }
}

function correctingDeviceFor(
  devices: Device[],
  location: string,
  cluster: string,
  state: TemperatureState
): string | null {
  const candidates = devices.filter(
    device =>
      device.location === location &&
      device.cluster === cluster &&
      device.state === 1 &&
      !device.device_name.startsWith('light_')
  )
  const pattern =
    state === 'low'
      ? /heat|heater/i
      : state === 'high'
        ? /cool|cooling|fan|exhaust|dehumid/i
        : /humid|co2/i
  return candidates.find(device => pattern.test(device.device_name))?.device_name ?? null
}

export function deriveRoomSensorStatus(
  _location: string,
  clusters: string[],
  sensorMeta: Record<string, SensorSampleMeta>,
  nowMs: number
): RoomSensorStatus {
  const qualityRank: Record<SensorQuality, number> = { live: 0, missing: 1, stale: 2, bad: 3 }
  let selected: RoomSensorStatus = {
    quality: 'missing',
    newestAgeMs: null,
    source: null,
    cluster: null,
  }

  for (const cluster of clusters) {
    const prefix = `${_location}_${cluster}_`
    const entries = Object.entries(sensorMeta)
      .filter(([key]) => key.startsWith(prefix))
      .map(([, meta]) => meta)
    const valid = entries.filter(meta => !meta.invalid)
    const invalid = entries.some(meta => meta.invalid)
    const newest = [...valid].sort(
      (a, b) => (b.observedAtMs ?? b.receivedAtMs) - (a.observedAtMs ?? a.receivedAtMs)
    )[0]
    const ageMs = newest ? Math.max(0, nowMs - (newest.observedAtMs ?? newest.receivedAtMs)) : null
    const quality: SensorQuality =
      valid.length === 0
        ? invalid
          ? 'bad'
          : 'missing'
        : ageMs != null && ageMs > SENSOR_STALE_AFTER_MS
          ? 'stale'
          : 'live'
    const candidate: RoomSensorStatus = {
      quality,
      newestAgeMs: ageMs,
      source: newest?.source ?? entries[0]?.source ?? null,
      cluster,
    }
    if (qualityRank[candidate.quality] > qualityRank[selected.quality]) selected = candidate
    else if (
      qualityRank[candidate.quality] === qualityRank[selected.quality] &&
      (ageMs ?? Infinity) > (selected.newestAgeMs ?? -1)
    )
      selected = candidate
  }

  return selected
}

export function deriveRoomDecisionSummary(
  location: string,
  clusters: string[],
  sensorData: Record<string, number>,
  trendData: TrendData,
  devices: Device[]
): RoomDecisionSummary {
  const layers = clusters.map((cluster, index) => {
    const temperature = valueFor(sensorData, location, cluster, 'temperature')
    const heatingSetpoint = setpointFor(sensorData, location, 'temperature')
    const coolingSetpoint = coolingSetpointFor(sensorData, location)
    const vpd = valueFor(sensorData, location, cluster, 'vpd')
    const vpdSetpoint = setpointFor(sensorData, location, 'vpd')
    const co2 = valueFor(sensorData, location, cluster, 'co2')
    const co2Setpoint = setpointFor(sensorData, location, 'co2')
    const temperatureState: TemperatureState =
      temperature == null || (heatingSetpoint == null && coolingSetpoint == null)
        ? 'missing'
        : heatingSetpoint != null && temperature < heatingSetpoint
          ? 'low'
          : coolingSetpoint != null && temperature > coolingSetpoint
            ? 'high'
            : 'in_band'
    const temperatureDelta =
      temperature == null
        ? null
        : temperatureState === 'low' && heatingSetpoint != null
          ? temperature - heatingSetpoint
          : temperatureState === 'high' && coolingSetpoint != null
            ? temperature - coolingSetpoint
            : 0
    const trend = trendData[cluster]?.temperature
    const breach = continuousBreach(trend, heatingSetpoint, coolingSetpoint, temperatureState)
    return {
      cluster,
      label: location === 'Flower Room' ? (index === 0 ? 'Front' : 'Back') : 'Room',
      temperature,
      heatingSetpoint,
      coolingSetpoint,
      temperatureState,
      temperatureDelta,
      vpd,
      vpdSetpoint,
      vpdDelta: vpd != null && vpdSetpoint != null ? vpd - vpdSetpoint : null,
      co2,
      co2Setpoint,
      co2Delta: co2 != null && co2Setpoint != null ? co2 - co2Setpoint : null,
      breachMinutes: breach.minutes,
      breachFullWindow: breach.fullWindow,
      correctingDevice: correctingDeviceFor(devices, location, cluster, temperatureState),
    }
  })

  const temperatureLayer = layers.find(
    layer => layer.temperatureState === 'low' || layer.temperatureState === 'high'
  )
  const correctingLayer = layers.find(layer => layer.correctingDevice)
  const largestVpdLayer = [...layers]
    .filter(layer => layer.vpdDelta != null)
    .sort((a, b) => Math.abs(b.vpdDelta ?? 0) - Math.abs(a.vpdDelta ?? 0))[0]
  const selected = temperatureLayer ?? correctingLayer ?? largestVpdLayer ?? layers[0] ?? null
  const prefix = selected && location === 'Flower Room' ? `${selected.label} ` : ''
  const headline =
    temperatureLayer != null
      ? `${prefix}TEMP ${temperatureLayer.temperatureState === 'low' ? 'LOW' : 'HIGH'}`
      : correctingLayer != null
        ? `${prefix}${correctingLayer.correctingDevice} correcting`
        : largestVpdLayer?.vpdDelta != null
          ? `${prefix}VPD ${largestVpdLayer.vpdDelta >= 0 ? '+' : ''}${largestVpdLayer.vpdDelta.toFixed(2)} kPa`
          : `${prefix}TEMP IN BAND`

  return {
    headline,
    headlineKind: temperatureLayer
      ? 'temperature'
      : correctingLayer
        ? 'device'
        : largestVpdLayer
          ? 'vpd'
          : 'in_band',
    layer: selected,
    layers,
    correctingDevice: correctingLayer?.correctingDevice ?? null,
  }
}

export function deriveRoomControlContext(
  location: string,
  snapshot: ControlSnapshotResponse | null,
  devices: Device[],
  degraded: boolean
): RoomControlContext {
  const roomRelays =
    snapshot?.relays?.filter(relay => relay.assignment?.location === location) ?? []
  const roomDevices = devices.filter(device => device.location === location)
  const failsafe =
    snapshot?.failsafes?.some(entry => entry.location === location) === true ||
    roomDevices.some(device => device.mode.toLowerCase() === 'failsafe')
  const modes = [
    ...roomRelays.map(relay => relay.command_mode?.toLowerCase() ?? ''),
    ...roomDevices.map(device => device.mode.toLowerCase()),
  ]
  const mode: RoomControlMode = failsafe
    ? 'FAILSAFE'
    : modes.includes('timed_on')
      ? 'MANUAL TIMED'
      : modes.includes('manual_off')
        ? 'MANUAL OFF'
        : modes.includes('scheduled')
          ? 'SCHEDULED'
          : 'AUTO'
  const manualExpiresAt =
    roomRelays
      .filter(relay => relay.command_mode?.toLowerCase() === 'timed_on' && relay.command_expires_at)
      .map(relay => relay.command_expires_at as string)
      .sort()[0] ?? null
  const syncing = roomRelays.some(relay => relay.syncing)
  const mismatches = roomRelays
    .filter(
      relay =>
        !relay.syncing &&
        relay.desired_state != null &&
        relay.observed_state != null &&
        Boolean(relay.desired_state) !== relay.observed_state
    )
    .map(
      relay =>
        relay.assignment?.display_name ?? relay.assignment?.device_name ?? `relay ${relay.channel}`
    )
  const interlockReasons = [
    ...new Set(
      roomRelays
        .filter(relay => relay.interlock_blocked && relay.interlock_reason)
        .map(relay => relay.interlock_reason as string)
    ),
  ]

  return {
    mode,
    manualExpiresAt,
    syncing,
    mismatch: mismatches.length > 0,
    mismatchDevices: mismatches,
    interlockReasons,
    degraded,
  }
}

export function normalizeSensorSeries(
  series: SensorSeries
): { metric: ClimateMetric; points: TrendPoint[] } | null {
  const sensor = series.sensor.toLowerCase()
  const metric = (Object.keys(METRIC_ALIASES) as ClimateMetric[]).find(candidate =>
    METRIC_ALIASES[candidate].some(alias => sensor === alias || sensor.includes(alias))
  )
  if (!metric) return null
  const points = series.points
    .map(point => ({ timestampMs: point.timestamp.getTime(), value: point.average }))
    .filter(point => Number.isFinite(point.timestampMs) && Number.isFinite(point.value))
    .sort((a, b) => a.timestampMs - b.timestampMs)
  if (points.length === 0) return null
  return { metric, points }
}

export function buildTrendMetric(metric: ClimateMetric, points: TrendPoint[]): TrendMetric {
  const { label, unit } = METRIC_LABELS[metric]
  const newest = points[points.length - 1]
  const cutoff = newest ? newest.timestampMs - 10 * 60_000 : 0
  const prior = [...points].reverse().find(point => point.timestampMs <= cutoff)
  const spansMinimumWindow =
    points.length >= 2 && newest != null && newest.timestampMs - points[0].timestampMs >= 2 * 60_000
  return {
    metric,
    label,
    unit,
    points,
    delta10m: spansMinimumWindow && prior ? newest.value - prior.value : null,
  }
}
