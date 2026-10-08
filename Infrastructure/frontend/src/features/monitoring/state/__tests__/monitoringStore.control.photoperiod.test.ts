import { describe, expect, it } from 'vitest'

import type {
  ControlMonitoringResponse,
  MonitoringResponse,
  PhotoperiodTimelinePoint,
  ProjectionPublicationResponse,
  TimelineProvenance,
} from '../../api'
import {
  applyControl,
  applyControlFresh,
  applyInitial,
  applyInitialPartial,
  applyProjection,
} from '../monitoringStore.control'
import { mergeControlHistory } from '../monitoringStore.merge'
import type { StoreData } from '../monitoringStore.types'

const T0 = new Date('2026-08-20T08:00:00.000Z').getTime()
const at = (minutes: number): Date => new Date(T0 + minutes * 60 * 1000)

const RECORDED: TimelineProvenance = {
  origin: 'recorded',
  quality: 'exact',
  is_aggregated: false,
}
const DERIVED: TimelineProvenance = {
  origin: 'derived',
  quality: 'estimated',
  is_aggregated: false,
}
const UNAVAILABLE: TimelineProvenance = {
  origin: 'recorded',
  quality: 'unavailable',
  is_aggregated: false,
}

function phasePoint(
  timestamp: Date,
  phase: PhotoperiodTimelinePoint['phase'],
  provenance: TimelineProvenance = RECORDED,
  metadata: Partial<
    Pick<PhotoperiodTimelinePoint, 'mode_id' | 'submode_id' | 'runtime_snapshot_version'>
  > = {}
): PhotoperiodTimelinePoint {
  return { timestamp, phase, provenance, ...metadata }
}

function sensorResponse(): MonitoringResponse {
  return {
    metadata: {
      generated_at: at(0),
      tier: 'raw',
      range: { start: at(-60), end: at(0) },
      room: { room: 'Flower Room', nodes: ['front', 'back'] },
    },
    series: [],
    statistics: [],
  }
}

function controlHistory(
  range: { start: Date; end: Date },
  photoperiod: PhotoperiodTimelinePoint[],
  runtimeSnapshotVersion = 7
): ControlMonitoringResponse {
  return {
    range,
    runtime_snapshot_version: runtimeSnapshotVersion,
    cursors: [{ source: 'photoperiod_history', cursor: '3', has_more: false }],
    flush_health: [
      { source: 'photoperiod_history', dropped_rows: 0, last_flushed_at: range.end, healthy: true },
    ],
    climate: [],
    lights: [],
    devices: [],
    pid: [],
    photoperiod,
  }
}

function photoperiodPublication(
  phasePoints: {
    valid_from: Date
    valid_until: Date
    value: number | null
    quality?: 'estimated' | 'unavailable'
  }[],
  revision = 'aa11bb2'
): ProjectionPublicationResponse {
  return {
    quality: 'estimated',
    value: [
      {
        version: { contract_version: 1, config_version: 7, revision },
        generated_at: at(0),
        valid_from: phasePoints[0]?.valid_from ?? at(0),
        valid_until: phasePoints.at(-1)?.valid_until ?? at(30),
        series: phasePoints.map(point => ({
          series_id: { value: 'light.photoperiod' },
          value: point.value,
          quality: point.quality ?? 'estimated',
          valid_from: point.valid_from,
          valid_until: point.valid_until,
        })),
      },
    ],
  }
}

function emptyProjection(): ProjectionPublicationResponse {
  return { quality: 'unavailable', value: [] }
}

describe('applyInitial', () => {
  it('installs the recorded history phase list without the projection phases', () => {
    const recorded = [
      phasePoint(at(-120), 'UNKNOWN', UNAVAILABLE),
      phasePoint(at(-90), 'SUN'),
      phasePoint(at(-30), 'MOON'),
    ]
    const projection = photoperiodPublication([
      { valid_from: at(0), valid_until: at(30), value: 1 },
      { valid_from: at(30), valid_until: at(60), value: 0 },
    ])

    const data = applyInitial(
      sensorResponse(),
      sensorResponse(),
      controlHistory({ start: at(-120), end: at(0) }, recorded),
      projection
    )

    expect(data.photoperiod).toEqual(recorded)
    expect(data.projectionHistory?.photoperiod.map(point => [point.timestamp, point.phase])).toEqual([
      [at(0), 'SUN'],
      [at(30), 'MOON'],
      [at(60), 'UNKNOWN'],
    ])
  })
})

describe('applyInitialPartial', () => {
  it('keeps the previous recorded list as the last good fallback when control fails', () => {
    const recorded = [phasePoint(at(-60), 'SUN'), phasePoint(at(-10), 'MOON')]
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-60), end: at(0) }, recorded),
      photoperiod: recorded,
    }
    const projection = photoperiodPublication([
      { valid_from: at(0), valid_until: at(30), value: 0 },
    ])

    const data = applyInitialPartial(existing, sensorResponse(), null, projection)

    expect(data.photoperiod).toBe(recorded)
    expect(data.projectionHistory?.photoperiod.map(point => [point.timestamp, point.phase])).toEqual([
      [at(0), 'MOON'],
      [at(30), 'UNKNOWN'],
    ])
  })

  it('installs the new recorded list when control succeeds, dropping stale phases', () => {
    const stale = [phasePoint(at(-120), 'SUN')]
    const fresh = [phasePoint(at(-60), 'MOON')]
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-120), end: at(0) }, stale),
      photoperiod: stale,
    }

    const data = applyInitialPartial(
      existing,
      sensorResponse(),
      controlHistory({ start: at(-60), end: at(0) }, fresh),
      emptyProjection()
    )

    expect(data.photoperiod).toEqual(fresh)
  })
})

describe('applyControl', () => {
  it('replaces overlapping UNKNOWN with later committed evidence inside the tail range', () => {
    const outside = phasePoint(at(-60), 'SUN')
    const placeholder = phasePoint(at(0), 'UNKNOWN', UNAVAILABLE)
    const projectionHistory = controlHistory(
      { start: at(0), end: at(30) },
      [phasePoint(at(0), 'SUN', UNAVAILABLE)],
      7
    )
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-60), end: at(0) }, [outside, placeholder]),
      projectionHistory,
      photoperiod: [outside, placeholder],
    }
    const tail = controlHistory({ start: at(0), end: at(10) }, [phasePoint(at(0), 'MOON')], 8)

    const data = applyControl(existing, tail)

    expect(data.controlHistory?.photoperiod).toEqual([outside, phasePoint(at(0), 'MOON')])
    expect(data.projectionHistory?.photoperiod).toEqual([phasePoint(at(0), 'SUN', UNAVAILABLE)])
    expect(data.photoperiod).toEqual([outside, phasePoint(at(0), 'MOON')])
    expect(
      data.photoperiod.filter(point => point.provenance.origin === 'projected')
    ).toStrictEqual([])
  })

  it('keeps resident points outside the incoming tail range untouched', () => {
    const old = phasePoint(at(-60), 'UNKNOWN', UNAVAILABLE)
    const atExclusiveEnd = phasePoint(at(20), 'MOON')
    const afterRange = phasePoint(at(25), 'UNKNOWN', UNAVAILABLE)
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory(
        { start: at(-60), end: at(30) },
        [old, atExclusiveEnd, afterRange]
      ),
      photoperiod: [old, atExclusiveEnd, afterRange],
    }
    const tail = controlHistory({ start: at(10), end: at(20) }, [phasePoint(at(10), 'SUN')], 8)

    const data = applyControl(existing, tail)

    expect(data.photoperiod).toEqual([
      old,
      phasePoint(at(10), 'SUN'),
      atExclusiveEnd,
      afterRange,
    ])
  })

  it('does not inflate the timeline with repeated identical tail anchors', () => {
    const anchor = phasePoint(at(0), 'SUN')
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-10), end: at(0) }, [anchor]),
      photoperiod: [anchor],
    }

    let data = applyControl(
      existing,
      controlHistory({ start: at(0), end: at(10) }, [phasePoint(at(0), 'SUN')], 8)
    )
    data = applyControl(
      data,
      controlHistory({ start: at(10), end: at(20) }, [phasePoint(at(10), 'SUN')], 8)
    )
    data = applyControl(
      data,
      controlHistory({ start: at(20), end: at(30) }, [phasePoint(at(20), 'SUN')], 8)
    )

    expect(data.photoperiod).toEqual([anchor])
  })

  it('wipes exactly the incoming range when a tail carries no phase points', () => {
    const outside = phasePoint(at(-60), 'SUN')
    const insideRange = phasePoint(at(10), 'SUN')
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-60), end: at(0) }, [outside]),
      photoperiod: [outside, insideRange],
    }
    const tail = controlHistory({ start: at(0), end: at(20) }, [], 8)

    const data = applyControl(existing, tail)

    expect(data.photoperiod).toEqual([outside])
  })
})

describe('applyControlFresh', () => {
  it('replaces the resident list wholesale without merging stale phases', () => {
    const stale = [phasePoint(at(-60), 'SUN'), phasePoint(at(-30), 'MOON')]
    const fresh = [phasePoint(at(-60), 'MOON'), phasePoint(at(-10), 'UNKNOWN', UNAVAILABLE)]
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-120), end: at(0) }, stale),
      photoperiod: stale,
    }

    const data = applyControlFresh(
      existing,
      controlHistory({ start: at(-60), end: at(0) }, fresh, 9)
    )

    expect(data.photoperiod).toEqual(fresh)
  })
})

describe('applyProjection', () => {
  it('leaves the recorded historical list unchanged while refreshing the projection', () => {
    const recorded = [phasePoint(at(-60), 'MOON'), phasePoint(at(-10), 'SUN')]
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-60), end: at(0) }, recorded),
      projectionHistory: controlHistory({ start: at(0), end: at(30) }, [], 7),
      photoperiod: recorded,
    }
    const projection = photoperiodPublication([
      { valid_from: at(0), valid_until: at(30), value: 1 },
    ])

    const { data, changed } = applyProjection(existing, projection)

    expect(changed).toBe(true)
    expect(data.photoperiod).toBe(existing.photoperiod)
    expect(data.photoperiod).toEqual(recorded)
  })

  it('returns the same data unchanged when revision and anchor quality match', () => {
    const recorded = [phasePoint(at(-60), 'SUN')]
    const existing: StoreData = {
      ...emptyStore(),
      controlHistory: controlHistory({ start: at(-60), end: at(0) }, recorded),
      projectionRevision: 'aa11bb2',
      projectionVersion: 7,
      anchorQuality: 'estimated',
      photoperiod: recorded,
    }
    const projection = photoperiodPublication(
      [{ valid_from: at(0), valid_until: at(30), value: 0 }],
      'aa11bb2'
    )

    const { data, changed } = applyProjection(existing, projection)

    expect(changed).toBe(false)
    expect(data).toBe(existing)
  })
})

describe('mergeControlHistory photoperiod semantics', () => {
  it('preserves derived/recorded/unavailable provenance boundaries', () => {
    const existing = controlHistory({ start: at(0), end: at(10) }, [
      phasePoint(at(0), 'MOON', DERIVED),
      phasePoint(at(5), 'SUN', DERIVED),
    ])
    const incoming = controlHistory({ start: at(10), end: at(20) }, [
      phasePoint(at(10), 'SUN'),
      phasePoint(at(15), 'MOON'),
      phasePoint(at(19), 'SUN'),
    ], 8)

    const merged = mergeControlHistory(existing, incoming)

    expect(merged.photoperiod).toEqual([
      phasePoint(at(0), 'MOON', DERIVED),
      phasePoint(at(5), 'SUN', DERIVED),
      phasePoint(at(10), 'SUN'),
      phasePoint(at(15), 'MOON'),
      phasePoint(at(19), 'SUN'),
    ])
  })

  it('collapses adjacent identical signatures so anchors stay compact', () => {
    const existing = controlHistory({ start: at(0), end: at(10) }, [
      phasePoint(at(0), 'SUN', RECORDED, { runtime_snapshot_version: 7 }),
    ])
    const incoming = controlHistory({ start: at(10), end: at(20) }, [
      phasePoint(at(12), 'SUN', RECORDED, { runtime_snapshot_version: 7 }),
    ], 8)

    const merged = mergeControlHistory(existing, incoming)

    expect(merged.photoperiod).toEqual([
      phasePoint(at(0), 'SUN', RECORDED, { runtime_snapshot_version: 7 }),
    ])
  })

  it('does not collapse adjacent points whose metadata differs', () => {
    const existing = controlHistory({ start: at(0), end: at(10) }, [
      phasePoint(at(0), 'SUN'),
    ])
    const incoming = controlHistory({ start: at(10), end: at(20) }, [
      phasePoint(at(10), 'SUN', RECORDED, {
        mode_id: 3,
        submode_id: 1,
        runtime_snapshot_version: 8,
      }),
    ], 9)

    const merged = mergeControlHistory(existing, incoming)

    expect(merged.photoperiod).toEqual([
      phasePoint(at(0), 'SUN'),
      phasePoint(at(10), 'SUN', RECORDED, {
        mode_id: 3,
        submode_id: 1,
        runtime_snapshot_version: 8,
      }),
    ])
  })

  it('prefers the incoming point at a tied timestamp inside the replaced range', () => {
    const existing = controlHistory({ start: at(0), end: at(10) }, [
      phasePoint(at(5), 'UNKNOWN', UNAVAILABLE),
    ])
    const incoming = controlHistory({ start: at(5), end: at(10) }, [
      phasePoint(at(5), 'MOON'),
    ], 8)

    const merged = mergeControlHistory(existing, incoming)

    expect(merged.photoperiod).toEqual([phasePoint(at(5), 'MOON')])
  })
})

function emptyStore(): StoreData {
  return {
    series: [],
    statistics: [],
    live: [],
    controlHistory: null,
    projectionHistory: null,
    photoperiod: [],
    cursors: [],
    projectionRevision: null,
    projectionVersion: null,
    anchorFingerprint: null,
    anchorQuality: null,
    anchorValidUntil: null,
    runtimeSnapshotVersion: null,
    flushHealth: [],
  }
}
