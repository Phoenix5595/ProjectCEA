/**
 * Contract-bound parse tests for the bounded history-overview responses.
 *
 * Each case exercises a backend model rule from
 * `history_overview_models.py` through the request-bound frontend factories:
 * request parity, budget bounds, ordered nonoverlapping lattice-aligned
 * points, zero/one sample boundaries, and relay duration invariants.
 */
import { describe, expect, it } from 'vitest'

import { historyOverviewControlContract, historyOverviewSensorContract } from '../contracts'

const SENSOR_RANGE = {
  start: '2026-01-01T00:00:00.000Z',
  end: '2026-01-06T00:00:00.000Z',
}
const SENSOR_REQUEST = {
  range: { start: new Date(SENSOR_RANGE.start), end: new Date(SENSOR_RANGE.end) },
  maxPoints: 10,
}

/** Requested 5-day range picks 12-hour buckets: 10 aligned buckets. */
const BUCKET_MS = 43_200_000

interface MutablePoint {
  readonly [key: string]: unknown
}

function bucketPoint(
  bucketsFromStart: number,
  overrides: Record<string, unknown> = {}
): MutablePoint {
  const startMs = new Date(SENSOR_RANGE.start).getTime() + bucketsFromStart * BUCKET_MS
  return {
    start: new Date(startMs).toISOString(),
    end: new Date(startMs + BUCKET_MS).toISOString(),
    average: 21.5,
    minimum: 21,
    maximum: 22,
    sample_count: 17,
    partial: false,
    ...overrides,
  }
}

function sensorMetadata(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    generated_at: '2026-01-05T00:00:00.500Z',
    range: SENSOR_RANGE,
    room: { room: 'Flower Room', nodes: ['front'] },
    requested_max_points: 10,
    bucket: { kind: 'fixed', seconds: 43_200 },
    source_coverage: 'ready',
    all_aggregated: true,
    ...overrides,
  }
}

function sensorSeries(points: MutablePoint[]): Record<string, unknown> {
  return {
    sensor: 'dry_bulb_f',
    node: 'front',
    unit: '°C',
    unit_family: 'celsius',
    points,
  }
}

function sensorPayload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    metadata: sensorMetadata(),
    series: [sensorSeries([bucketPoint(0)])],
    statistics: [
      {
        sensor: 'dry_bulb_f',
        node: 'front',
        sample_count: 17,
        minimum: 21,
        maximum: 22,
        average: 21.5,
        stddev_samp: 0.5,
        stddev_quality: 'exact',
      },
    ],
    ...overrides,
  }
}

describe('historyOverviewSensorContract', () => {
  it('accepts a valid request-aligned sensor response', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    const parsed = contract.parse(sensorPayload())
    expect(parsed.metadata.bucket).toEqual({ kind: 'fixed', seconds: 43_200 })
    expect(parsed.metadata.room).toEqual({ room: 'Flower Room', nodes: ['front'] })
  })

  it('accepts the full 4000-point plot-width budget (R1)', () => {
    const contract = historyOverviewSensorContract({ ...SENSOR_REQUEST, maxPoints: 4000 })
    const parsed = contract.parse(
      sensorPayload({
        metadata: sensorMetadata({ requested_max_points: 4000, bucket: { kind: 'fixed', seconds: 3_600 } }),
        series: [],
      })
    )
    expect(parsed.metadata.requested_max_points).toBe(4000)
  })

  it('rejects responses whose range or budget disagree with the request', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    expect(() =>
      contract.parse(
        sensorPayload({
          metadata: sensorMetadata({
            range: { start: '2026-01-01T00:00:00.000Z', end: '2026-01-04T13:00:00.000Z' },
          }),
        })
      )
    ).toThrow()
    expect(() =>
      contract.parse(sensorPayload({ metadata: sensorMetadata({ requested_max_points: 200 }) }))
    ).toThrow()
    expect(() =>
      contract.parse(
        sensorPayload({ metadata: sensorMetadata({ bucket: { kind: 'fixed', seconds: 43_201 } }) })
      )
    ).toThrow()
  })

  it('rejects calendar and out-of-ladder bucket metadata', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    for (const bucket of [
      { kind: 'calendar', months: 1, time_zone: 'UTC' },
      { kind: 'fixed', seconds: 86_400 },
      { kind: 'fixed', seconds: 1_800 },
      { kind: 'fixed', seconds: 0 },
    ]) {
      expect(() =>
        contract.parse(sensorPayload({ metadata: sensorMetadata({ bucket }) }))
      ).toThrow()
    }
  })

  it('rejects a request whose range cannot fit the 12-hour bucket cap', () => {
    const contract = historyOverviewSensorContract({
      range: {
        start: new Date('2015-01-01T00:00:00.000Z'),
        end: new Date('2026-01-01T00:00:00.000Z'),
      },
      maxPoints: 10,
    })
    expect(() =>
      contract.parse(
        sensorPayload({
          metadata: sensorMetadata({
            range: { start: '2015-01-01T00:00:00.000Z', end: '2026-01-01T00:00:00.000Z' },
            requested_max_points: 10,
          }),
        })
      )
    ).toThrow()
  })

  it('rejects points outside or misaligned with the requested lattice', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    expect(() =>
      contract.parse(sensorPayload({ series: [sensorSeries([bucketPoint(0), bucketPoint(0.5)])] }))
    ).toThrow()

    expect(() =>
      contract.parse(sensorPayload({ series: [sensorSeries([bucketPoint(0, { partial: true })])] }))
    ).toThrow()

    expect(() =>
      contract.parse(
        sensorPayload({
          series: [
            sensorSeries([
              bucketPoint(1, {
                start: new Date(SENSOR_RANGE.end).toISOString(),
                partial: true,
                sample_count: 0,
                average: null,
                minimum: null,
                maximum: null,
              }),
            ]),
          ],
        })
      )
    ).toThrow()
  })

  it('rejects unordered and overlapping point series but accepts touching edges', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    expect(() =>
      contract.parse(sensorPayload({ series: [sensorSeries([bucketPoint(2), bucketPoint(1)])] }))
    ).toThrow()
    const touching = contract.parse(
      sensorPayload({ series: [sensorSeries([bucketPoint(0), bucketPoint(1)])] })
    )
    expect(touching.series).toHaveLength(1)
  })

  it('keeps single-row sensor points equal while requested PID may be predecessor-weighted', () => {
    const sensorContract = historyOverviewSensorContract(SENSOR_REQUEST)
    expect(() =>
      sensorContract.parse(
        sensorPayload({
          series: [sensorSeries([bucketPoint(0, { sample_count: 1 })])],
        })
      )
    ).toThrow()
  })

  it('enforces zero-count and single-sample boundaries', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    expect(() =>
      contract.parse(
        sensorPayload({
          statistics: [
            {
              sensor: 'dry_bulb_f',
              node: 'front',
              sample_count: 0,
              minimum: 21,
              maximum: 22,
              average: 21.5,
              stddev_samp: 0.5,
            },
          ],
        })
      )
    ).toThrow()
    expect(() =>
      contract.parse(
        sensorPayload({
          statistics: [
            {
              sensor: 'dry_bulb_f',
              node: 'front',
              sample_count: 1,
              minimum: 21,
              maximum: 22,
              average: 21.5,
              stddev_samp: 0,
            },
          ],
        })
      )
    ).toThrow()
    expect(() =>
      contract.parse(
        sensorPayload({
          statistics: [
            {
              sensor: 'dry_bulb_f',
              node: 'front',
              sample_count: 9,
              minimum: 21,
              maximum: 22,
              average: 21.5,
              stddev_samp: null,
            },
          ],
        })
      )
    ).toThrow()

    const single = contract.parse(
      sensorPayload({
        statistics: [
          {
            sensor: 'dry_bulb_f',
            node: 'front',
            sample_count: 1,
            minimum: 21.25,
            maximum: 21.25,
            average: 21.25,
            stddev_samp: 0,
          },
        ],
      })
    )
    expect(single.statistics[0]?.stddev_quality).toBe('exact')
  })

  it('rejects a series longer than the point budget but accepts a full-lattice series', () => {
    const contract = historyOverviewSensorContract(SENSOR_REQUEST)
    const points = Array.from({ length: 11 }, (_, index) => bucketPoint(index))
    expect(() => contract.parse(sensorPayload({ series: [sensorSeries(points)] }))).toThrow()
    const withinBudget = contract.parse(
      sensorPayload({ series: [sensorSeries(points.slice(0, 7))] })
    )
    expect(withinBudget.series).toHaveLength(1)
  })
})

describe('historyOverviewControlContract', () => {
  const CONTROL_RANGE = {
    start: '2026-01-01T00:00:00.000Z',
    end: '2026-01-01T00:05:00.000Z',
  }
  const CONTROL_REQUEST = {
    range: { start: new Date(CONTROL_RANGE.start), end: new Date(CONTROL_RANGE.end) },
    maxPoints: 600,
  }

  function controlPayload(metadataRange = CONTROL_RANGE): Record<string, unknown> {
    const startMs = new Date(metadataRange.start).getTime()
    const endMs = new Date(metadataRange.end).getTime()
    const rangeSeconds = (endMs - startMs) / 1000
    const sampledPoint = (average: number): Record<string, unknown> => ({
      start: new Date(startMs).toISOString(),
      end: new Date(endMs).toISOString(),
      average,
      minimum: average,
      maximum: average,
      sample_count: 2,
      partial: true,
    })
    return {
      metadata: {
        generated_at: '2026-01-01T00:05:01.000Z',
        range: metadataRange,
        room: { room: 'Flower Room', nodes: ['front'] },
        requested_max_points: 600,
        bucket: { kind: 'fixed', seconds: 3600 },
        source_coverage: 'ready',
        all_aggregated: true,
      },
      lights: [
        {
          location: 'Flower Room',
          cluster: 'front',
          device_name: 'light_f_1',
          points: [sampledPoint(80)],
        },
      ],
      requested_pid: [
        {
          location: 'Flower Room',
          cluster: 'front',
          device_name: 'Heater Flower',
          points: [sampledPoint(12.5)],
        },
      ],
      relays: [
        {
          location: 'Flower Room',
          cluster: 'front',
          channel: 3,
          device_id: 11,
          device_name: 'Exhaust Fan',
          device_type: 'fan',
          points: [
            {
              start: new Date(startMs).toISOString(),
              end: new Date(endMs).toISOString(),
              on_seconds: rangeSeconds * 0.5,
              off_seconds: rangeSeconds * 0.25,
              unknown_seconds: rangeSeconds * 0.25,
              coverage_complete: false,
              partial: true,
            },
          ],
        },
      ],
    }
  }

  it('accepts a valid control response and derives durations within a microsecond', () => {
    const contract = historyOverviewControlContract(CONTROL_REQUEST)
    const parsed = contract.parse(controlPayload())
    expect(parsed.relays[0]?.channel).toBe(3)
    expect(parsed.lights[0]?.device_name).toBe('light_f_1')
  })
  it('accepts a single-row requested PID point whose LOCF average includes its predecessor', () => {
    const contract = historyOverviewControlContract(CONTROL_REQUEST)
    const payload = controlPayload()
    const requestedSeries = (
      payload.requested_pid as Array<{ points: Array<Record<string, unknown>> }>
    )[0]
    const point = requestedSeries?.points[0]
    if (!requestedSeries || !point) throw new Error('requested PID fixture point is missing')
    requestedSeries.points[0] = {
      ...point,
      sample_count: 1,
      average: 15,
      minimum: 10,
      maximum: 20,
    }

    const parsed = contract.parse(payload)
    expect(parsed.requested_pid[0]?.points[0]).toMatchObject({
      sample_count: 1,
      average: 15,
      minimum: 10,
      maximum: 20,
    })

    const invalidLightPayload = controlPayload()
    const lightSeries = (
      invalidLightPayload.lights as Array<{ points: Array<Record<string, unknown>> }>
    )[0]
    const lightPoint = lightSeries?.points[0]
    if (!lightSeries || !lightPoint) throw new Error('light fixture point is missing')
    lightSeries.points[0] = {
      ...lightPoint,
      sample_count: 1,
      average: 15,
      minimum: 10,
      maximum: 20,
    }
    expect(() => contract.parse(invalidLightPayload)).toThrow()
  })

  it('accepts a predecessor-covered requested PID point with zero in-span rows', () => {
    const contract = historyOverviewControlContract(CONTROL_REQUEST)
    const payload = controlPayload()
    const requestedSeries = (
      payload.requested_pid as Array<{ points: Array<Record<string, unknown>> }>
    )[0]
    const point = requestedSeries?.points[0]
    if (!requestedSeries || !point) throw new Error('requested PID fixture point is missing')
    requestedSeries.points[0] = {
      ...point,
      sample_count: 0,
      average: 15,
      minimum: 10,
      maximum: 20,
    }

    const parsed = contract.parse(payload)
    expect(parsed.requested_pid[0]?.points[0]).toMatchObject({
      sample_count: 0,
      average: 15,
      minimum: 10,
      maximum: 20,
    })
  })


  it('rejects control ranges shorter than five minutes', () => {
    const short = historyOverviewControlContract({
      range: {
        start: new Date('2026-01-01T00:00:00.000Z'),
        end: new Date('2026-01-01T00:04:59.999Z'),
      },
      maxPoints: 600,
    })
    expect(() => short.parse(controlPayload())).toThrow()
  })

  it('rejects relay durations that do not sum to the bucket duration', () => {
    const contract = historyOverviewControlContract(CONTROL_REQUEST)
    const broken = controlPayload()
    const relays = broken.relays as unknown as Array<Record<string, unknown>>
    relays[0] = {
      ...relays[0],
      points: [
        {
          start: CONTROL_RANGE.start,
          end: CONTROL_RANGE.end,
          on_seconds: 190,
          off_seconds: 100.125,
          unknown_seconds: 19.625,
          coverage_complete: false,
          partial: true,
        },
      ],
    }
    expect(() => contract.parse(broken)).toThrow()
  })

  it('rejects complete coverage that still reports unknown time', () => {
    const contract = historyOverviewControlContract(CONTROL_REQUEST)
    const broken = controlPayload()
    const relays = broken.relays as unknown as Array<Record<string, unknown>>
    relays[0] = {
      ...relays[0],
      points: [
        {
          start: CONTROL_RANGE.start,
          end: CONTROL_RANGE.end,
          on_seconds: 180.25,
          off_seconds: 100.125,
          unknown_seconds: 19.625,
          coverage_complete: true,
          partial: true,
        },
      ],
    }
    expect(() => contract.parse(broken)).toThrow()
  })

  it('accepts a lights response and rejects populated PID and relay groups', () => {
    const lights = historyOverviewControlContract(CONTROL_REQUEST, 'lights')
    const lightsPayload = controlPayload()
    lightsPayload.requested_pid = []
    lightsPayload.relays = []
    const parsed = lights.parse(lightsPayload)
    expect(parsed.lights[0]?.device_name).toBe('light_f_1')
    expect(parsed.requested_pid).toHaveLength(0)
    expect(parsed.relays).toHaveLength(0)

    const lightsWithPid = controlPayload()
    lightsWithPid.relays = []
    expect(() => lights.parse(lightsWithPid)).toThrow()

    const lightsWithRelays = controlPayload()
    lightsWithRelays.requested_pid = []
    expect(() => lights.parse(lightsWithRelays)).toThrow()
  })

  it('leaves the all scope free of the lights-only restriction', () => {
    const all = historyOverviewControlContract(CONTROL_REQUEST, 'all')
    expect(all.parse(controlPayload()).requested_pid[0]?.device_name).toBe('Heater Flower')
    expect(all.parse(controlPayload()).relays[0]?.device_name).toBe('Exhaust Fan')
  })
})
