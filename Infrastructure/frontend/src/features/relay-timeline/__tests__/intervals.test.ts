import { describe, expect, it } from 'vitest'

import type { RelayTimelineLoadPoint, RelayTimelineTransition } from '../contracts'
import { buildRelayLaneIntervals, buildRequestedOutputSegments } from '../intervals'
import type { RelayTimelineWindow } from '../intervals'

const SESSION_A = 'b8a8be48-8ee7-4f10-9d5c-7a63f9f0a001'
const SESSION_B = 'b8a8be48-8ee7-4f10-9d5c-7a63f9f0a002'
const BASE_MS = Date.UTC(2026, 8, 25, 12, 0, 0)
const DEVICE = {
  deviceId: 101,
  deviceName: 'veg_heater',
  deviceType: 'heating',
  channel: 0,
  location: 'Veg Room',
  cluster: 'main',
} as const
const OTHER_DEVICE = { id: 202, name: 'veg_cooler', type: 'cooling' } as const
function range(startSeconds: number, endSeconds: number): RelayTimelineWindow {
  return {
    start: new Date(BASE_MS + startSeconds * 1_000),
    end: new Date(BASE_MS + endSeconds * 1_000),
  }
}

function observation(
  observationId: number,
  seconds: number,
  channel: number | null,
  observedState: boolean | null,
  reason: RelayTimelineTransition['reason'],
  owner: { id: number; name: string; type: string } | null,
  sessionId = SESSION_A
): RelayTimelineTransition {
  return {
    observation_id: observationId,
    observed_at: new Date(BASE_MS + seconds * 1_000),
    channel,
    observed_state: observedState,
    reason,
    session_id: sessionId,
    registry_version: 5,
    device_id: owner?.id ?? null,
    device_name: owner?.name ?? null,
    device_type: owner?.type ?? null,
    location: owner ? 'Veg Room' : null,
    cluster: owner ? 'main' : null,
  }
}

const heartbeat = (id: number, seconds: number, session = SESSION_A) =>
  observation(id, seconds, null, null, 'heartbeat', null, session)
const assigned = { id: DEVICE.deviceId, name: DEVICE.deviceName, type: DEVICE.deviceType }

function buildLane(
  rangeWindow: RelayTimelineWindow,
  anchors: readonly RelayTimelineTransition[],
  transitions: readonly RelayTimelineTransition[],
  sourceCoverageComplete = true
) {
  return buildRelayLaneIntervals({
    lane: DEVICE,
    range: rangeWindow,
    anchors,
    transitions,
    sourceCoverageComplete,
  })
}

describe('relay timeline interval reconstruction', () => {
  it('counts sample-time ON/OFF changes and excludes clipped boundary runs from duration averages', () => {
    const result = buildLane(
      range(0, 300),
      [observation(1, -40, 0, false, 'initial', assigned), heartbeat(2, -10)],
      [
        heartbeat(3, 30),
        heartbeat(4, 60),
        observation(5, 90, 0, true, 'state_changed', assigned),
        heartbeat(6, 90),
        heartbeat(7, 120),
        heartbeat(8, 150),
        observation(9, 180, 0, false, 'state_changed', assigned),
        heartbeat(10, 180),
        heartbeat(11, 210),
        heartbeat(12, 240),
        observation(13, 270, 0, true, 'state_changed', assigned),
        heartbeat(14, 270),
      ]
    )

    expect(result.summary).toMatchObject({
      onTransitions: 2,
      offTransitions: 1,
      cyclesPerHour: 24,
      knownPercent: 100,
      meanOnDurationSeconds: 90,
      meanOffDurationSeconds: 90,
      onSeconds: 120,
      offSeconds: 180,
      coverageComplete: true,
    })
    expect(result.summary.completeOnDurationsSeconds).toEqual([90])
    expect(result.summary.completeOffDurationsSeconds).toEqual([90])
    expect(result.intervals[0]).toMatchObject({
      state: 'off',
      partialStart: true,
      partialEnd: false,
    })
    expect(result.intervals[result.intervals.length - 1]).toMatchObject({
      state: 'on',
      partialStart: false,
      partialEnd: true,
    })
  })

  it('splits stale and recovery spans into explicit unknown coverage', () => {
    const result = buildLane(
      range(0, 240),
      [observation(1, -40, 0, true, 'initial', assigned), heartbeat(2, -10)],
      [
        heartbeat(3, 30),
        observation(4, 60, 0, null, 'stale', null),
        observation(5, 150, 0, true, 'recovered', assigned),
        heartbeat(6, 180),
        heartbeat(7, 210),
      ],
      false
    )

    expect(result.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ state: 'on', start: BASE_MS, end: BASE_MS + 60_000 }),
        expect.objectContaining({
          state: 'unknown',
          start: BASE_MS + 60_000,
          end: BASE_MS + 150_000,
        }),
        expect.objectContaining({ state: 'on', start: BASE_MS + 150_000, end: BASE_MS + 240_000 }),
      ])
    )
    expect(result.summary.knownPercent).toBe(62.5)
    expect(result.summary.coverageComplete).toBe(false)
  })
  it('extends a live edge only when a fresh snapshot agrees with the persisted state', () => {
    const anchors = [observation(1, -40, 0, true, 'initial', assigned), heartbeat(2, -10)]
    const transitions = [heartbeat(3, 30)]
    const window = range(0, 120)
    const dataEnd = new Date(BASE_MS + 60_000)
    const matchingSnapshot = buildRelayLaneIntervals({
      lane: DEVICE,
      range: window,
      dataEnd,
      liveEdge: { timestamp: new Date(BASE_MS + 100_000), state: true },
      anchors,
      transitions,
      sourceCoverageComplete: true,
    })
    const conflictingSnapshot = buildRelayLaneIntervals({
      lane: DEVICE,
      range: window,
      dataEnd,
      liveEdge: { timestamp: new Date(BASE_MS + 100_000), state: false },
      anchors,
      transitions,
      sourceCoverageComplete: true,
    })

    expect(matchingSnapshot.intervals).toEqual([
      expect.objectContaining({ state: 'on', start: BASE_MS, end: BASE_MS + 120_000 }),
    ])
    expect(matchingSnapshot.summary.coverageComplete).toBe(true)
    expect(conflictingSnapshot.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          state: 'unknown',
          start: BASE_MS + 60_000,
          end: BASE_MS + 100_000,
        }),
        expect.objectContaining({ state: 'off', start: BASE_MS + 100_000, end: BASE_MS + 120_000 }),
      ])
    )
    expect(conflictingSnapshot.summary.coverageComplete).toBe(false)
  })

  it('breaks continuity after a heartbeat outage and requires a fresh channel observation to resume', () => {
    const result = buildLane(
      range(0, 190),
      [observation(1, -40, 0, true, 'initial', assigned), heartbeat(2, -10)],
      [
        heartbeat(3, 70),
        observation(4, 100, 0, true, 'state_changed', assigned),
        heartbeat(5, 130),
        heartbeat(6, 160),
      ]
    )

    expect(result.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ state: 'on', start: BASE_MS, end: BASE_MS + 50_000 }),
        expect.objectContaining({
          state: 'unknown',
          start: BASE_MS + 50_000,
          end: BASE_MS + 100_000,
        }),
        expect.objectContaining({ state: 'on', start: BASE_MS + 100_000, end: BASE_MS + 190_000 }),
      ])
    )
    expect(result.summary.coverageComplete).toBe(false)
  })
  it('keeps recorder queue gaps unknown until a fresh channel baseline arrives', () => {
    const result = buildLane(
      range(0, 180),
      [observation(1, -40, 0, true, 'initial', assigned), heartbeat(2, -10)],
      [
        heartbeat(3, 30),
        observation(4, 60, 0, null, 'recording_gap', null),
        observation(5, 120, 0, true, 'state_changed', assigned),
        heartbeat(6, 150),
      ],
      false
    )

    expect(result.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          state: 'unknown',
          start: BASE_MS + 60_000,
          end: BASE_MS + 120_000,
          reason: 'recording gap',
        }),
      ])
    )
    expect(result.summary.coverageComplete).toBe(false)
  })

  it('does not attribute a replacement owner or a restarted process to the current lane', () => {
    const anchor = [observation(1, -40, 0, true, 'initial', assigned), heartbeat(2, -10)]
    const assignmentChange = buildLane(range(0, 180), anchor, [
      heartbeat(3, 30),
      observation(4, 60, 0, false, 'assignment_changed', OTHER_DEVICE),
      heartbeat(5, 90),
      heartbeat(6, 120),
    ])
    const restart = buildLane(range(0, 240), anchor, [
      heartbeat(3, 30),
      heartbeat(4, 60),
      observation(5, 150, 0, false, 'initial', assigned, SESSION_B),
      heartbeat(6, 180, SESSION_B),
      heartbeat(7, 210, SESSION_B),
    ])

    expect(assignmentChange.intervals[assignmentChange.intervals.length - 1]).toMatchObject({
      state: 'unknown',
      reason: 'relay assignment changed',
    })
    expect(restart.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          state: 'unknown',
          start: BASE_MS + 120_000,
          end: BASE_MS + 150_000,
        }),
      ])
    )
    expect(restart.summary.coverageComplete).toBe(false)
  })

  it('treats initial and recovery baselines as partial duration boundaries', () => {
    const result = buildLane(
      range(0, 200),
      [],
      [
        observation(1, 20, 0, true, 'initial', assigned),
        heartbeat(2, 50),
        observation(3, 80, 0, false, 'state_changed', assigned),
        heartbeat(4, 90),
        observation(5, 120, 0, null, 'stale', assigned),
        observation(6, 160, 0, true, 'recovered', assigned),
        heartbeat(7, 180),
      ],
      false
    )

    const initialRun = result.intervals.find(interval => interval.start === BASE_MS + 20_000)
    const recoveredRun = result.intervals.find(interval => interval.start === BASE_MS + 160_000)
    expect(initialRun).toMatchObject({ state: 'on', partialStart: true, partialEnd: false })
    expect(recoveredRun).toMatchObject({ state: 'on', partialStart: true, partialEnd: true })
    expect(result.summary.completeOnDurationsSeconds).toEqual([])
    expect(result.summary.meanOnDurationSeconds).toBeNull()
  })

  it('counts a recorded switch after unknown coverage without claiming a full cycle', () => {
    const result = buildLane(
      range(0, 180),
      [],
      [
        observation(1, 60, 0, true, 'state_changed', assigned),
        heartbeat(2, 90),
        heartbeat(3, 120),
        heartbeat(4, 150),
      ],
      false
    )

    expect(result.summary.onTransitions).toBe(1)
    expect(result.summary.offTransitions).toBe(0)
    expect(result.summary.cyclesPerHour).toBe(0)
    expect(result.intervals).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ state: 'unknown', start: BASE_MS, end: BASE_MS + 60_000 }),
        expect.objectContaining({
          state: 'on',
          start: BASE_MS + 60_000,
          end: BASE_MS + 180_000,
          partialStart: true,
          partialEnd: true,
        }),
      ])
    )
  })
})

describe('requested PID output step segments', () => {
  const loadPoint = (
    seconds: number,
    percent: number | null,
    deviceId = 101,
    deviceName = 'veg_heater'
  ): RelayTimelineLoadPoint => ({
    device_id: deviceId,
    device_name: deviceName,
    timestamp: new Date(BASE_MS + seconds * 1_000),
    requested_percent: percent,
    aggregated: false,
    interval_seconds: 30,
  })

  it('step-holds finite percentages, breaks at null, and rejects reassigned or non-PID output', () => {
    const points = [
      loadPoint(0, 0),
      loadPoint(30, 50),
      loadPoint(60, null),
      loadPoint(90, 100),
      loadPoint(120, 50, 101, 'renamed_old_owner'),
    ]
    const result = buildRequestedOutputSegments({
      deviceType: 'heating',
      deviceId: DEVICE.deviceId,
      deviceName: DEVICE.deviceName,
      range: range(0, 180),
      points,
    })

    expect(result).toEqual([
      {
        start: BASE_MS,
        end: BASE_MS + 30_000,
        requestedPercent: 0,
        aggregated: false,
        intervalSeconds: 30,
      },
      {
        start: BASE_MS + 30_000,
        end: BASE_MS + 60_000,
        requestedPercent: 50,
        aggregated: false,
        intervalSeconds: 30,
      },
      {
        start: BASE_MS + 90_000,
        end: BASE_MS + 150_000,
        requestedPercent: 100,
        aggregated: false,
        intervalSeconds: 30,
      },
    ])
    expect(
      buildRequestedOutputSegments({
        deviceType: 'humidifier',
        deviceId: 101,
        deviceName: 'veg_heater',
        range: range(0, 180),
        points,
      })
    ).toEqual([])
  })

  it('marks aggregate resolution without changing the requested percent', () => {
    const result = buildRequestedOutputSegments({
      deviceType: 'co2',
      deviceId: 103,
      deviceName: 'veg_co2',
      range: range(0, 300),
      points: [
        {
          device_id: 103,
          device_name: 'veg_co2',
          timestamp: new Date(BASE_MS + 60_000),
          requested_percent: 50,
          aggregated: true,
          interval_seconds: 60,
        },
      ],
    })

    expect(result[0]).toMatchObject({ requestedPercent: 50, aggregated: true, intervalSeconds: 60 })
  })
})
