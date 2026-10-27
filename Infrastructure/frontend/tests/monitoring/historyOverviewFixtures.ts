/**
 * Bounded all-history overview fixtures for the monitoring preview middleware.
 *
 * Serves the two bounded overview GET routes in `vite.monitoring.config.ts`:
 *
 * - `GET /api/sensors/monitoring/overview/{room}?start&end&max_points`
 * - `GET /api/monitoring/control/{room}/overview?start&end&max_points`
 *
 * The responses mirror the backend overview contracts exactly (metadata with
 * strict fixed bucket metadata, nullable zero-count statistics) while the
 * underlying facts stay finite and intentionally recorded:
 *
 * - canonical sensor/light/requested-PID sampling is bounded to the most
 *   recent 31 days of the requested window (20-minute grid for week-or-less
 *   windows, hourly across the 31-day window), derived from the canonical
 *   monitoring fixture identities and values — never the registry or the live
 *   current state; buckets without recorded samples stay null;
 * - sparse retained-history samples at fixed 365/1825/3650-day offsets echo
 *   the recorded long-archive fixture facts (12:10 UTC seeds); unrecorded
 *   older years remain unknown;
 * - relay durations reuse the canonical relay-timeline fact shape (real
 *   recorded lane identities, finite 30-second heartbeat windows: one 24-hour
 *   dense window plus 45-minute sparse windows) computed through the
 *   unchanged TypeScript interval engine (`buildRelayLaneIntervals`), so
 *   historical support, 60-second heartbeat expiry and relay unknown time
 *   follow the exact runtime semantics. No ten-year heartbeat arrays and no
 *   synthetic lanes.
 *
 * Bucket selection uses the shared core picker (`historyOverviewBucketBounds`):
 * whole-hour epoch-aligned display widths from 1 to 12 hours, finest width
 * whose actual intersecting count (clipped edges included) fits the requested
 * budget. A range/budget pair that cannot fit the 12-hour cap is rejected with
 * the exact monitoring 400 detail before any response is assembled, so
 * response buckets/budgets cannot drift from the real API.
 */
import {
  historyOverviewBucketBounds,
  HistoryOverviewRangeError,
  type HistoryBucketMetadata,
} from '../../src/features/history-overview/buckets.ts'
import { buildRelayLaneIntervals } from '../../src/features/relay-timeline/intervals.ts'
import { sensorRangeFixture } from '../../src/features/monitoring/config/fixtures.ts'

interface BucketBound {
  readonly startMs: number
  readonly endMs: number
  readonly start: string
  readonly end: string
  readonly partial: boolean
}

interface SampleFact {
  readonly atMs: number
  readonly value: number | null
}

interface OverviewQuery {
  readonly startMs: number
  readonly endMs: number
  readonly maxPoints: number
}

const SENSOR_MINIMUM_RANGE_MS = 1_000
const CONTROL_MINIMUM_RANGE_MS = 5 * 60 * 1_000
/** Backend generic default; the UI always requests an explicit budget. */
const DEFAULT_MAX_POINTS = 600
const HOUR_MS = 3_600_000
const DAY_MS = 86_400_000

/** Canonical recorded rooms/nodes of the sensor monitoring feature. */
const MONITORING_ROOMS = ['Flower Room', 'Veg Room'] as const
type MonitoringRoom = (typeof MONITORING_ROOMS)[number]

const ROOM_NODES: Record<MonitoringRoom, readonly string[]> = {
  'Flower Room': ['front', 'back'],
  'Veg Room': ['main'],
}
const isMonitoringRoom = (room: string): room is MonitoringRoom =>
  (MONITORING_ROOMS as readonly string[]).includes(room)

/** Canonical bounded window the monitoring range fixtures are anchored to. */
const CANONICAL_SENSOR_WINDOW_START = '2026-08-02T09:00:00.000Z'
const CANONICAL_SENSOR_WINDOW_END = '2026-08-02T12:00:00.000Z'

/**
 * Sparse retained-history seed offsets, mirroring the long-archive fixture
 * facts (365/1825/3650-day samples at 12:10 UTC) of the disposable proof.
 */
const RETAINED_OFFSET_DAYS = [365, 1825, 3650] as const
const RETAINED_SEED_SENSOR_TIME_MS =
  12 * HOUR_MS + 10 * 60_000 + 987 // 12:10:00.987 Z, as recorded by the archive fixture
const RETAINED_SEED_LIGHT_TIME_MS =
  12 * HOUR_MS + 10 * 60_000 + 654 // 12:10:00.654 Z

/** Sampled intensity structure of the recorded light ranges: 80% daytime. */
const LIGHT_DAY_INTENSITY = 80
const LIGHT_NIGHT_INTENSITIES = [25, 40, 55]

/** Recorded requested-PID percent cycle; null rows are recorded but excluded. */
const REQUESTED_PID_CYCLE = [12.5, 25, 50, 75, 100, null] as const
/** Recorded requested-PID seeds for heating (null row excluded from counts). */
const REQUESTED_PID_HEATING_SEEDS: ReadonlyArray<number | null> = [12.5, 25, null]
/** Recorded requested-PID seeds for co2. */
const REQUESTED_PID_CO2_SEEDS: ReadonlyArray<number | null> = [25, 50, 75]

const DENSE_RELAY_WINDOW_MS = 24 * HOUR_MS
/** Sparse recorded relay heartbeat windows: 45 minutes at the seed offsets. */
const RETAINED_RELAY_WINDOW_MS = 45 * 60_000

interface CanonicalSensorFact {
  readonly sensor: string
  readonly node: string
  readonly unitFamily: string
  readonly unit: string
  readonly base: number
  readonly alt: number
  readonly seat: number
}

/**
 * Canonical sensor identities/values of the monitoring fixtures. Read from the
 * existing bounded canonical range fixture so the identities and values stay
 * the fixture's authority.
 */
function canonicalSensorFacts(room: MonitoringRoom): readonly CanonicalSensorFact[] {
  const fixture = sensorRangeFixture(
    room,
    CANONICAL_SENSOR_WINDOW_START,
    CANONICAL_SENSOR_WINDOW_END,
    null
  ) as {
    series: ReadonlyArray<{
      sensor: string
      node: string
      unit_family: string
      unit: string
      points: ReadonlyArray<{ average: number }>
    }>
  }
  return fixture.series.map((series, seat) => ({
    sensor: series.sensor,
    node: series.node,
    unitFamily: series.unit_family,
    unit: series.unit,
    base: series.points[0]!.average,
    alt: series.points[1]!.average,
    seat,
  }))
}

/** Dense sampled facts cover only the recent bounded window (31 days) of any
 * requested range; earlier buckets stay honestly null except the retained
 * 365/1825/3650-day seeds. Unrecorded older years remain unknown. */
const DENSE_FACT_WINDOW_MS = 31 * DAY_MS

/** Dense sampled fact grid inside the dense window: 20min for a week or less,
 * hourly across the bounded 31-day window (the window never exceeds it). */
function denseFactIntervalMs(startMs: number, endMs: number): number {
  const denseSpan = endMs - Math.max(startMs, endMs - DENSE_FACT_WINDOW_MS)
  if (denseSpan <= 7 * DAY_MS) return 1_200_000
  return 3_600_000
}

/** Deterministic per-location UTC instant of a retained seed offset. */
function seedInstant(endMs: number, offsetDays: number, timeOfDayMs: number): number {
  const target = new Date(endMs - offsetDays * DAY_MS)
  const dayStart = Date.UTC(
    target.getUTCFullYear(),
    target.getUTCMonth(),
    target.getUTCDate()
  )
  return dayStart + timeOfDayMs
}

/** Sampled sensor facts: bounded dense recent window + retained seed offsets. */
function sensorSampleFacts(
  series: CanonicalSensorFact,
  startMs: number,
  endMs: number
): readonly SampleFact[] {
  const facts: SampleFact[] = []
  const interval = denseFactIntervalMs(startMs, endMs)
  let index = 0
  for (let atMs = Math.max(startMs, endMs - DENSE_FACT_WINDOW_MS); atMs < endMs; index += 1) {
    const value =
      series.base + (index % 2 === 0 ? 0 : series.alt - series.base) + 0.05 * (index % 7)
    facts.push({ atMs, value })
    atMs += interval
  }
  for (const [offsetIndex, offsetDays] of RETAINED_OFFSET_DAYS.entries()) {
    const atMs = seedInstant(endMs, offsetDays, RETAINED_SEED_SENSOR_TIME_MS)
    if (atMs < startMs || atMs >= endMs) continue
    const value = 20 + (offsetIndex + 1) + (series.seat + 1) / 100
    facts.push({ atMs, value })
  }
  return facts
}

interface RecordedLight {
  readonly deviceName: string
  readonly seat: number
}

/** The three canonical recorded lights per room (equipment-fixture names). */
function canonicalLights(room: MonitoringRoom): readonly RecordedLight[] {
  const suffix = room === 'Veg Room' ? 'v' : 'f'
  return [1, 2, 3].map(index => ({
    deviceName: `light_${suffix}_${index}`,
    seat: index - 1,
  }))
}

/** Sampled light facts (explicit nonnull intensity sampling, no ramp modeling). */
function lightSampleFacts(
  light: RecordedLight,
  startMs: number,
  endMs: number
): readonly SampleFact[] {
  const facts: SampleFact[] = []
  const interval = denseFactIntervalMs(startMs, endMs)
  for (let atMs = Math.max(startMs, endMs - DENSE_FACT_WINDOW_MS); atMs < endMs; atMs += interval) {
    const hour = new Date(atMs).getUTCHours()
    const value =
      hour >= 6 && hour < 18
        ? LIGHT_DAY_INTENSITY
        : LIGHT_NIGHT_INTENSITIES[light.seat] ?? 25
    facts.push({ atMs, value })
  }
  for (const offsetDays of RETAINED_OFFSET_DAYS) {
    const atMs = seedInstant(endMs, offsetDays, RETAINED_SEED_LIGHT_TIME_MS)
    if (atMs < startMs || atMs >= endMs) continue
    facts.push({ atMs, value: LIGHT_DAY_INTENSITY })
  }
  return facts
}

interface RecordedPidDevice {
  readonly deviceName: string
  readonly deviceType: 'heating' | 'co2'
}

/**
 * Canonical PID devices per room (the recorded relay-registry subsets with
 * requested output). Never guessed from the current registry.
 */
function canonicalPidDevices(room: MonitoringRoom): readonly RecordedPidDevice[] {
  const prefix = room === 'Veg Room' ? 'veg' : 'flower'
  return [
    { deviceName: `${prefix}_heater`, deviceType: 'heating' },
    { deviceName: `${prefix}_co2`, deviceType: 'co2' },
  ]
}

/** Sampled requested-PID facts; null rows are recorded but never counted. */
function pidSampleFacts(
  location: MonitoringRoom,
  device: RecordedPidDevice,
  startMs: number,
  endMs: number
): readonly SampleFact[] {
  const facts: SampleFact[] = []
  const ordinal = device.deviceType === 'heating' ? 0 : 2
  const interval = denseFactIntervalMs(startMs, endMs)
  let index = 0
  for (let atMs = Math.max(startMs, endMs - DENSE_FACT_WINDOW_MS); atMs < endMs; index += 1) {
    const value = REQUESTED_PID_CYCLE[(index + ordinal) % REQUESTED_PID_CYCLE.length]
    facts.push({ atMs, value: value === undefined ? null : value })
    atMs += interval
  }
  const seeds =
    device.deviceType === 'heating' ? REQUESTED_PID_HEATING_SEEDS : REQUESTED_PID_CO2_SEEDS
  for (const [offsetIndex, offsetDays] of RETAINED_OFFSET_DAYS.entries()) {
    const atMs = seedInstant(endMs, offsetDays, RETAINED_SEED_SENSOR_TIME_MS)
    if (atMs < startMs || atMs >= endMs) continue
    const value = seeds[offsetIndex]
    if (value === undefined) continue
    facts.push({ atMs, value })
  }
  return facts
}

/** One aggregation of ordered sample facts into nullable sampled statistics. */
interface FactSummary {
  readonly sampleCount: number
  readonly average: number | null
  readonly minimum: number | null
  readonly maximum: number | null
  readonly stddevSamp: number | null
}

function summarizeFacts(facts: readonly SampleFact[]): FactSummary {
  const values = facts.flatMap(fact => (fact.value === null ? [] : [fact.value]))
  if (values.length === 0) {
    return { sampleCount: 0, average: null, minimum: null, maximum: null, stddevSamp: null }
  }
  let sum = 0
  let sumSquares = 0
  let minimum = values[0]!
  let maximum = values[0]!
  for (const value of values) {
    sum += value
    sumSquares += value * value
    if (value < minimum) minimum = value
    if (value > maximum) maximum = value
  }
  const count = values.length
  const average = sum / count
  if (count === 1) {
    return {
      sampleCount: 1,
      average,
      minimum,
      maximum,
      stddevSamp: 0.0,
    }
  }
  const squaredDeviation = (sumSquares - (sum * sum) / count) / (count - 1)
  return {
    sampleCount: count,
    average,
    minimum,
    maximum,
    stddevSamp: squaredDeviation < 0 ? 0 : Math.sqrt(squaredDeviation),
  }
}

function summarizeWindow(
  facts: readonly SampleFact[],
  windowStartMs: number,
  windowEndMs: number
): FactSummary {
  const inside: SampleFact[] = []
  for (const fact of facts) {
    if (fact.atMs >= windowStartMs && fact.atMs < windowEndMs) inside.push(fact)
  }
  return summarizeFacts(inside)
}

function pointsForFacts(
  facts: readonly SampleFact[],
  bounds: readonly BucketBound[],
  maxPoints: number
): Array<Record<string, unknown>> {
  const points: Array<Record<string, unknown>> = []
  let cursor = 0
  for (const bound of bounds) {
    while (cursor < facts.length && facts[cursor]!.atMs < bound.startMs) cursor += 1
    const inside: SampleFact[] = []
    for (let index = cursor; index < facts.length && facts[index]!.atMs < bound.endMs; index += 1) {
      inside.push(facts[index]!)
    }
    const summary = summarizeFacts(inside)
    points.push({
      start: bound.start,
      end: bound.end,
      average: summary.average,
      minimum: summary.minimum,
      maximum: summary.maximum,
      sample_count: summary.sampleCount,
      partial: bound.partial,
    })
    if (points.length > maxPoints) break
  }
  return points
}

// ---------------------------------------------------------------------------
// Canonical relay facts (mirror of the guarded relay-pid-timeline fixtures)
// ---------------------------------------------------------------------------

interface RelayFixtureDevice {
  readonly device_id: number
  readonly device_name: string
  readonly device_type: string
  readonly location: MonitoringRoom
  readonly channel: number
  readonly pid_enabled: boolean
}

/**
 * Recorded relay lane identities of the fixture registry (mirror of
 * RELAY_TIMELINE_DEVICES in tests/monitoring/monitoringPreviewFixtures.ts).
 */
const RELAY_LANE_DEVICES: readonly RelayFixtureDevice[] = [
  { device_id: 101, device_name: 'veg_heater', device_type: 'heating', location: 'Veg Room', channel: 0, pid_enabled: false },
  { device_id: 103, device_name: 'veg_co2', device_type: 'co2', location: 'Veg Room', channel: 7, pid_enabled: true },
  { device_id: 102, device_name: 'veg_light', device_type: 'light', location: 'Veg Room', channel: 1, pid_enabled: false },
  { device_id: 201, device_name: 'flower_heater', device_type: 'heating', location: 'Flower Room', channel: 2, pid_enabled: false },
  { device_id: 203, device_name: 'flower_co2', device_type: 'co2', location: 'Flower Room', channel: 8, pid_enabled: true },
  { device_id: 202, device_name: 'flower_light', device_type: 'light', location: 'Flower Room', channel: 3, pid_enabled: false },
]

/** Canonical fixture relay session shared by all recorded lane observations. */
const RELAY_FACT_SESSION = 'b8a8be48-8ee7-4f10-9d5c-7a63f9f0a001'
const HEARTBEAT_MAX_GAP_MS = 60_000

interface RelayFactRow {
  readonly observation_id: number
  readonly time: Date
  readonly channel: number | null
  readonly observed_state: boolean | null
  readonly reason:
    | 'initial'
    | 'state_changed'
    | 'stale'
    | 'recovered'
    | 'assignment_changed'
    | 'recording_gap'
    | 'heartbeat'
  readonly session_id: string
  readonly registry_version: number
  readonly device_id: number | null
  readonly device_name: string | null
  readonly device_type: string | null
  readonly location: string | null
  readonly cluster: string | null
}

/** Canonical recorded relay observation row (mirror of the guarded fixture). */
function relayFactRow(
  observationId: number,
  atMs: number,
  channel: number | null,
  state: boolean | null,
  reason: RelayFactRow['reason'],
  device: RelayFixtureDevice | null
): RelayFactRow {
  return {
    observation_id: observationId,
    time: new Date(atMs),
    channel,
    observed_state: state,
    reason,
    session_id: RELAY_FACT_SESSION,
    registry_version: 1,
    device_id: device?.device_id ?? null,
    device_name: device?.device_name ?? null,
    device_type: device?.device_type ?? null,
    location: device?.location ?? null,
    cluster: device ? 'main' : null,
  }
}

/**
 * One lane's recorded fact rows for a requested range: a bounded dense recent
 * heartbeat window (24 hours, mirroring the canonical relay-timeline fact
 * shape) plus sparse retained heartbeat windows at the 365/1825/3650-day
 * offsets. Relay knowledge is heartbeat-supported only, so no ten-year
 * heartbeat arrays are invented and everything outside these finite windows
 * stays honest relay unknown time. Every window carries its own `initial`
 * anchors and 30-second heartbeats so the interval engine's support/expiry
 * semantics decide the ON/OFF/unknown coverage.
 */
function relayLaneFactRows(
  lane: RelayFixtureDevice,
  endMs: number
): readonly RelayFactRow[] {
  const rows: RelayFactRow[] = []
  let nextId = 1_000
  const push = (
    atMs: number,
    channel: number | null,
    state: boolean | null,
    reason: RelayFactRow['reason'],
    device: RelayFixtureDevice | null
  ): void => {
    rows.push(relayFactRow(nextId++, atMs, channel, state, reason, device))
  }
  const denseStart = endMs - DENSE_RELAY_WINDOW_MS
  // Canonical dense-window anchors and lane transitions (as recorded by the
  // guarded relay timeline fixture, relative to the dense window start).
  push(denseStart - 40_000, lane.channel, lane.device_type === 'heating', 'initial', lane)
  push(denseStart - 40_000, denseLaneChannel(lane, 'co2'), false, 'initial', sameRoomDevice(lane, 'co2'))
  push(denseStart - 40_000, denseLaneChannel(lane, 'light'), false, 'initial', sameRoomDevice(lane, 'light'))
  if (lane.device_type === 'heating') {
    push(denseStart + 8 * 60_000, lane.channel, false, 'state_changed', lane)
    push(denseStart + 9 * 60_000, lane.channel, true, 'state_changed', lane)
    push(denseStart + 18 * 60_000, lane.channel, null, 'stale', lane)
    push(denseStart + 20 * 60_000, lane.channel, false, 'recovered', lane)
    push(denseStart + 25 * 60_000, lane.channel, true, 'state_changed', lane)
    push(denseStart + 27 * 60_000, lane.channel, false, 'state_changed', lane)
    push(denseStart + 28 * 60_000, lane.channel, true, 'state_changed', lane)
  } else if (lane.device_type === 'co2') {
    push(denseStart + 10 * 60_000, lane.channel, true, 'state_changed', lane)
    push(denseStart + 12 * 60_000, lane.channel, false, 'state_changed', lane)
  } else {
    push(denseStart + 6 * 60_000, lane.channel, true, 'state_changed', lane)
  }
  const staleAt = denseStart + 18 * 60_000
  const recoveredAt = denseStart + 20 * 60_000
  // Heartbeats sustain every lane's support across the dense window only.
  for (let atMs = denseStart + 15_000; atMs < endMs; atMs += 30_000) {
    if (atMs >= staleAt && atMs < recoveredAt) continue
    push(atMs, null, null, 'heartbeat', null)
  }
  // Sparse retained heartbeat windows: one per recorded archive offset.
  for (const offsetDays of RETAINED_OFFSET_DAYS) {
    const windowEnd = seedInstant(endMs, offsetDays, RETAINED_SEED_SENSOR_TIME_MS)
    const windowStart = windowEnd - RETAINED_RELAY_WINDOW_MS
    push(windowStart - 40_000, lane.channel, lane.device_type === 'heating', 'initial', lane)
    push(windowStart - 40_000, denseLaneChannel(lane, 'co2'), false, 'initial', sameRoomDevice(lane, 'co2'))
    push(windowStart - 40_000, denseLaneChannel(lane, 'light'), false, 'initial', sameRoomDevice(lane, 'light'))
    push(windowStart - 10_000, null, null, 'heartbeat', null)
    if (lane.device_type === 'heating') {
      push(windowStart + 5 * 60_000, lane.channel, true, 'state_changed', lane)
    } else if (lane.device_type === 'co2') {
      push(windowStart + 10 * 60_000, lane.channel, true, 'state_changed', lane)
      push(windowStart + 20 * 60_000, lane.channel, false, 'state_changed', lane)
    } else {
      push(windowStart + 10 * 60_000, lane.channel, false, 'state_changed', lane)
    }
    for (let atMs = windowStart + 15_000; atMs < windowEnd; atMs += 30_000) {
      push(atMs, null, null, 'heartbeat', null)
    }
  }
  rows.sort((a, b) => a.time.getTime() - b.time.getTime())
  return rows
}

/** The same-room device of a lane's type (lane rows need canonical per-channel ownership). */
function sameRoomDevice(
  lane: RelayFixtureDevice,
  deviceType: 'heating' | 'co2' | 'light'
): RelayFixtureDevice {
  return (
    RELAY_LANE_DEVICES.find(
      device => device.location === lane.location && device.device_type === deviceType
    ) ?? lane
  )
}

function denseLaneChannel(lane: RelayFixtureDevice, deviceType: 'co2' | 'light'): number {
  return sameRoomDevice(lane, deviceType).channel
}

// ---------------------------------------------------------------------------
// Overview response assembly
// ---------------------------------------------------------------------------

/** Relay lane durations for one bucket, in exact integer microseconds. */
interface RelayBucketDurations {
  onUs: number
  offUs: number
  unknownUs: number
}

interface RelayEngineInterval {
  readonly start: number
  readonly end: number
  readonly state: 'on' | 'off' | 'unknown'
}

function relayLaneDurations(
  intervals: readonly RelayEngineInterval[],
  startMs: number,
  endMs: number,
  bounds: readonly BucketBound[]
): readonly RelayBucketDurations[] {
  const durations: RelayBucketDurations[] = bounds.map(() => ({ onUs: 0, offUs: 0, unknownUs: 0 }))
  const clipped = intervals
    .map(interval => ({
      state: interval.state,
      start: Math.max(startMs, interval.start),
      end: Math.min(endMs, interval.end),
    }))
    .filter(interval => interval.end > interval.start)
    .sort((a, b) => a.start - b.start)
  let cursor = 0
  for (const [index, bound] of bounds.entries()) {
    while (cursor < clipped.length && clipped[cursor]!.end <= bound.startMs) cursor += 1
    const bucket = durations[index]!
    for (let i = cursor; i < clipped.length && clipped[i]!.start < bound.endMs; i += 1) {
      const overlapStart = Math.max(clipped[i]!.start, bound.startMs)
      const overlapEnd = Math.min(clipped[i]!.end, bound.endMs)
      if (overlapEnd <= overlapStart) continue
      const us = (overlapEnd - overlapStart) * 1000
      const state = clipped[i]!.state
      if (state === 'on') bucket.onUs += us
      else if (state === 'off') bucket.offUs += us
      else bucket.unknownUs += us
    }
  }
  return durations
}

/** Exact metadata block mirrored from the backend overview contract. */
function overviewMetadata(
  room: MonitoringRoom,
  startMs: number,
  endMs: number,
  maxPoints: number,
  bucket: HistoryBucketMetadata
): Record<string, unknown> {
  return {
    generated_at: new Date().toISOString(),
    range: { start: new Date(startMs).toISOString(), end: new Date(endMs).toISOString() },
    room: { room, nodes: [...ROOM_NODES[room]] },
    requested_max_points: maxPoints,
    bucket: { ...bucket },
    source_coverage: 'ready',
    all_aggregated: true,
  }
}

/** Bounded all-history sensor overview response (exact backend shape). */
function sensorOverviewResponse(
  room: MonitoringRoom,
  startMs: number,
  endMs: number,
  maxPoints: number
): Record<string, unknown> {
  const { bucket, buckets } = historyOverviewBucketBounds(new Date(startMs), new Date(endMs), maxPoints)
  const bounds: BucketBound[] = buckets.map(bound => ({
    startMs: bound.start.getTime(),
    endMs: bound.end.getTime(),
    start: bound.start.toISOString(),
    end: bound.end.toISOString(),
    partial: bound.partial,
  }))
  const series = canonicalSensorFacts(room).map(fact => {
    const facts = sensorSampleFacts(fact, startMs, endMs)
    return {
      sensor: fact.sensor,
      node: fact.node,
      unit_family: fact.unitFamily,
      unit: fact.unit,
      points: pointsForFacts(facts, bounds, maxPoints),
    }
  })
  const statistics = canonicalSensorFacts(room).map(fact => {
    const summary = summarizeWindow(
      sensorSampleFacts(fact, startMs, endMs),
      startMs,
      endMs
    )
    return {
      sensor: fact.sensor,
      node: fact.node,
      sample_count: summary.sampleCount,
      minimum: summary.minimum,
      maximum: summary.maximum,
      average: summary.average,
      stddev_samp: summary.stddevSamp,
      stddev_quality: 'exact',
    }
  })
  return {
    metadata: overviewMetadata(room, startMs, endMs, maxPoints, bucket),
    series,
    statistics,
  }
}

/** Bounded all-history control overview response (exact backend shape). */
function controlOverviewResponse(
  room: MonitoringRoom,
  startMs: number,
  endMs: number,
  maxPoints: number,
  scope: 'all' | 'lights'
): Record<string, unknown> {
  const { bucket, buckets } = historyOverviewBucketBounds(new Date(startMs), new Date(endMs), maxPoints)
  const bounds: BucketBound[] = buckets.map(bound => ({
    startMs: bound.start.getTime(),
    endMs: bound.end.getTime(),
    start: bound.start.toISOString(),
    end: bound.end.toISOString(),
    partial: bound.partial,
  }))
  const lights = canonicalLights(room).map(light => ({
    location: room,
    cluster: 'main',
    device_name: light.deviceName,
    points: pointsForFacts(lightSampleFacts(light, startMs, endMs), bounds, maxPoints),
  }))
  if (scope === 'lights') {
    return {
      metadata: overviewMetadata(room, startMs, endMs, maxPoints, bucket),
      lights,
      requested_pid: [],
      relays: [],
    }
  }
  const requestedPid = canonicalPidDevices(room).map(device => ({
    location: room,
    cluster: 'main',
    device_name: device.deviceName,
    points: pointsForFacts(
      pidSampleFacts(room, device, startMs, endMs),
      bounds,
      maxPoints
    ),
  }))
  const lanes = RELAY_LANE_DEVICES.filter(device => device.location === room)
  const relays = lanes.map(lane => {
    const { intervals } = buildRelayLaneIntervals({
      lane: {
        deviceId: lane.device_id,
        deviceName: lane.device_name,
        deviceType: lane.device_type,
        channel: lane.channel,
        location: lane.location,
        cluster: 'main',
      },
      range: { start: new Date(startMs), end: new Date(endMs) },
      anchors: [],
      transitions: relayLaneFactRows(lane, endMs),
      sourceCoverageComplete: true,
    })
    const durations = relayLaneDurations(intervals, startMs, endMs, bounds)
    return {
      location: lane.location,
      cluster: 'main',
      channel: lane.channel,
      device_id: lane.device_id,
      device_name: lane.device_name,
      device_type: lane.device_type,
      points: bounds.map((bound, index) => {
        const durationsForBucket = durations[index]!
        return {
          start: bound.start,
          end: bound.end,
          on_seconds: durationsForBucket.onUs / 1_000_000,
          off_seconds: durationsForBucket.offUs / 1_000_000,
          unknown_seconds: durationsForBucket.unknownUs / 1_000_000,
          coverage_complete: durationsForBucket.unknownUs === 0,
          partial: bound.partial,
        }
      }),
    }
  })
  return {
    metadata: overviewMetadata(room, startMs, endMs, maxPoints, bucket),
    lights,
    requested_pid: requestedPid,
    relays,
  }
}

// ---------------------------------------------------------------------------
// Loopback request dispatch
// ---------------------------------------------------------------------------

export interface OverviewFixtureReply {
  readonly status: number
  readonly body: unknown
}

export interface OverviewFixtureRequest {
  readonly url?: string
  readonly method?: string
}

const SENSORS_OVERVIEW_PATTERN = /^\/api\/sensors\/monitoring\/overview\/([^/]+)$/
const CONTROL_OVERVIEW_PATTERN = /^\/api\/monitoring\/control\/([^/]+)\/overview$/
const AWARE_TIMESTAMP_PATTERN = /(?:[Zz]$|[+-]\d{2}:?\d{2}$)/
const UNAVAILABLE_DETAIL = 'history overview aggregate coverage is unavailable'

class OverviewFixtureError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string
  ) {
    super(detail)
    this.name = 'OverviewFixtureError'
  }
}

function parseAwareMs(value: string, name: string): number {
  if (!AWARE_TIMESTAMP_PATTERN.test(value)) {
    throw new OverviewFixtureError(400, `${name} must include a timezone offset`)
  }
  const parsed = new Date(value).getTime()
  if (!Number.isFinite(parsed)) {
    throw new OverviewFixtureError(
      400,
      `${name} must be an ISO 8601 timestamp with a timezone offset`
    )
  }
  return parsed
}

function parseOverviewQuery(search: URLSearchParams): OverviewQuery {
  const rawStart = search.get('start')
  const rawEnd = search.get('end')
  if (rawStart === null || rawEnd === null) {
    throw new OverviewFixtureError(400, 'start and end must be supplied together')
  }
  const startMs = parseAwareMs(rawStart, 'start')
  const endMs = parseAwareMs(rawEnd, 'end')
  if (endMs <= startMs) {
    throw new OverviewFixtureError(400, 'range end must be later than range start')
  }
  const rawMaxPoints = search.get('max_points')
  if (rawMaxPoints === null) {
    return { startMs, endMs, maxPoints: DEFAULT_MAX_POINTS }
  }
  // NaN and non-integer budgets reject through the shared core budget check,
  // which mirrors the monitoring 400 detail messages exactly.
  return { startMs, endMs, maxPoints: Number(rawMaxPoints) }
}

/**
 * One guarded overview fixture reply, or null when the request is not an
 * overview route. GET-only; the shared core budget/range rejections mirror the
 * monitoring 400 contract, room resolution reports the exact 404, the routed
 * minimums reject short ranges, and scenario `overview-unavailable` returns
 * the exact coverage 503 the hook error path exercises.
 */
export function historyOverviewFixtureReply(
  req: OverviewFixtureRequest,
  scenario: string | null
): OverviewFixtureReply | null {
  const url = new URL(req.url ?? '/', 'http://fixture.invalid')
  const sensorMatch = SENSORS_OVERVIEW_PATTERN.exec(url.pathname)
  const controlMatch = sensorMatch === null ? CONTROL_OVERVIEW_PATTERN.exec(url.pathname) : null
  if (sensorMatch === null && controlMatch === null) return null
  if ((req.method ?? 'GET') !== 'GET') {
    return {
      status: 405,
      body: { detail: { code: 'read_only_fixture', detail: 'Overview routes are read-only' } },
    }
  }
  try {
    const query = parseOverviewQuery(url.searchParams)
    const rawScope = url.searchParams.get('scope')
    if (controlMatch !== null && rawScope !== null && rawScope !== 'all' && rawScope !== 'lights') {
      throw new OverviewFixtureError(400, 'scope must be all or lights')
    }
    const scope = rawScope === 'lights' ? 'lights' : 'all'
    // Core picker: budget (10..1000) then range order — mirroring the real
    // Query-then-route validation order; rejections map to 400 details.
    historyOverviewBucketBounds(new Date(query.startMs), new Date(query.endMs), query.maxPoints)
    if (sensorMatch !== null) {
      if (query.endMs - query.startMs < SENSOR_MINIMUM_RANGE_MS) {
        throw new OverviewFixtureError(400, 'range duration must be at least 1 second')
      }
    } else if (query.endMs - query.startMs < CONTROL_MINIMUM_RANGE_MS) {
      throw new OverviewFixtureError(
        400,
        'control overview range duration must be at least 5 minutes'
      )
    }
    const rawRoom = decodeURIComponent((sensorMatch ?? controlMatch)![1] ?? '')
    if (!isMonitoringRoom(rawRoom)) {
      return { status: 404, body: { detail: `unknown room: ${rawRoom}` } }
    }
    if (scenario === 'overview-unavailable') {
      return { status: 503, body: { detail: UNAVAILABLE_DETAIL } }
    }
    const body =
      sensorMatch !== null
        ? sensorOverviewResponse(rawRoom, query.startMs, query.endMs, query.maxPoints)
        : controlOverviewResponse(rawRoom, query.startMs, query.endMs, query.maxPoints, scope)
    return { status: 200, body }
  } catch (error) {
    if (error instanceof OverviewFixtureError) {
      return { status: error.status, body: { detail: error.detail } }
    }
    if (error instanceof HistoryOverviewRangeError) {
      // Core picker rejections carry the exact monitoring 400 detail strings.
      return { status: 400, body: { detail: error.message } }
    }
    throw error
  }
}
