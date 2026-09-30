import { describe, expect, it } from 'vitest'

import { SENSOR_STALE_AFTER_MS } from '../../types/sensor'
import { projectZoneSensorStatus, summarizeZoneSensors } from '../zoneSensorSummary'

const PREFIX = 'Veg Room_main_'
const BASE_MS = new Date('2026-09-29T12:00:00Z').getTime()
const emptySummary = summarizeZoneSensors(PREFIX, {})

function meta(overrides: { observedAtMs?: number | null; invalid?: boolean; source?: 'poll' | 'websocket' }) {
  const observedAtMs = 'observedAtMs' in overrides ? overrides.observedAtMs ?? null : null
  const invalid = overrides.invalid ?? false
  return { observedAtMs, receivedAtMs: BASE_MS, source: overrides.source ?? 'poll', invalid }
}

describe('summarizeZoneSensors', () => {
  it('reports a missing zone as empty without invalid or valid samples', () => {
    expect(summarizeZoneSensors(PREFIX, {})).toEqual({
      hasInvalid: false,
      firstSource: null,
      newestValid: null,
      newestObservedAtMs: null,
    })
  })

  it('keeps only entries inside the requested zone prefix', () => {
    const summary = summarizeZoneSensors(PREFIX, {
      'Flower Room_front_dry_bulb_f': meta({ observedAtMs: BASE_MS, source: 'poll' }),
      'Veg Room_main_dry_bulb': meta({ observedAtMs: BASE_MS - 100, source: 'websocket' }),
    })
    expect(summary.newestValid?.source).toBe('websocket')
    expect(summary.newestObservedAtMs).toBe(BASE_MS - 100)
  })

  it('flags an all-invalid zone and keeps the first source including invalid entries', () => {
    const summary = summarizeZoneSensors(PREFIX, {
      [`${PREFIX}dry_bulb`]: meta({ invalid: true, source: 'websocket' }),
      [`${PREFIX}vpd`]: meta({ invalid: true, source: 'poll' }),
    })
    expect(summary.hasInvalid).toBe(true)
    expect(summary.firstSource).toBe('websocket')
    expect(summary.newestValid).toBeNull()
    expect(summary.newestObservedAtMs).toBeNull()
  })

  it('picks the newest valid entry with receivedAtMs fallback and ignores null observed stamps in newestObservedAtMs', () => {
    const validNoObserved = meta({ observedAtMs: null, source: 'websocket' })
    const validOldObserved = meta({ observedAtMs: BASE_MS - 1500, source: 'poll' })
    const summary = summarizeZoneSensors(PREFIX, {
      [`${PREFIX}dry_bulb`]: validNoObserved,
      [`${PREFIX}vpd`]: validOldObserved,
    })
    expect(summary.newestValid).toBe(validNoObserved)
    expect(summary.newestObservedAtMs).toBe(BASE_MS - 1500)
  })

  it('keeps the first valid entry on timestamp ties between two zones', () => {
    const first = meta({ observedAtMs: BASE_MS, source: 'poll' })
    const second = { ...meta({ observedAtMs: BASE_MS, source: 'websocket' }), receivedAtMs: BASE_MS + 5 }
    const summary = summarizeZoneSensors(PREFIX, {
      [`${PREFIX}dry_bulb`]: first,
      [`${PREFIX}vpd`]: second,
    })
    expect(summary.newestValid).toBe(first)
  })

  it('selects the newest valid sample while skipping an invalid newer receipt', () => {
    const invalidOlder = meta({ observedAtMs: BASE_MS - 10, invalid: true })
    const validNewer = meta({ observedAtMs: BASE_MS + 10, source: 'websocket' })
    const summary = summarizeZoneSensors(PREFIX, {
      [`${PREFIX}dry_bulb`]: invalidOlder,
      [`${PREFIX}vpd`]: validNewer,
    })
    expect(summary.hasInvalid).toBe(true)
    expect(summary.newestValid).toBe(validNewer)
    expect(summary.newestObservedAtMs).toBe(BASE_MS + 10)
  })
})

describe('projectZoneSensorStatus', () => {
  it('projects an empty summary as missing with a null age and error passthrough', () => {
    const status = projectZoneSensorStatus(emptySummary, 'Sensor request failed', BASE_MS)
    expect(status).toEqual({
      quality: 'missing',
      newestObservedAtMs: null,
      ageMs: null,
      source: null,
      error: 'Sensor request failed',
    })
  })

  it('projects an all-invalid summary as bad carrying the first source', () => {
    const summary = summarizeZoneSensors(PREFIX, {
      [`${PREFIX}dry_bulb`]: meta({ invalid: true, source: 'websocket' }),
    })
    const status = projectZoneSensorStatus(summary, null, BASE_MS)
    expect(status).toEqual({
      quality: 'bad',
      newestObservedAtMs: null,
      ageMs: null,
      source: 'websocket',
      error: null,
    })
  })

  it('clamps a future observed stamp to age zero and stays live', () => {
    const summary = summarizeZoneSensors(PREFIX, {
      [`${PREFIX}dry_bulb`]: meta({ observedAtMs: BASE_MS + 5_000, source: 'websocket' }),
    })
    const status = projectZoneSensorStatus(summary, null, BASE_MS)
    expect(status.ageMs).toBe(0)
    expect(status.quality).toBe('live')
    expect(status.source).toBe('websocket')
  })

  it('reports stale only after the stale threshold and documents the exact 45000/45001 boundary', () => {
    const boundaryMeta = meta({ observedAtMs: BASE_MS - 45_000, source: 'poll' })
    const summary = summarizeZoneSensors(PREFIX, { [`${PREFIX}dry_bulb`]: boundaryMeta })
    const liveStatus = projectZoneSensorStatus(summary, null, BASE_MS)
    expect(SENSOR_STALE_AFTER_MS).toBe(45_000)
    expect(liveStatus.quality).toBe('live')
    expect(liveStatus.ageMs).toBe(45_000)

    const staleStatus = projectZoneSensorStatus(summary, null, BASE_MS + 1)
    expect(staleStatus.quality).toBe('stale')
    expect(staleStatus.ageMs).toBe(45_001)
  })

  it('passes receivedAtMs samples through the stale threshold once their receipt age grows', () => {
    const fallbackMeta = meta({ observedAtMs: null, source: 'poll' })
    const summary = summarizeZoneSensors(PREFIX, { [`${PREFIX}dry_bulb`]: fallbackMeta })
    expect(projectZoneSensorStatus(summary, null, BASE_MS).quality).toBe('live')
    expect(projectZoneSensorStatus(summary, null, BASE_MS + SENSOR_STALE_AFTER_MS + 1).quality).toBe(
      'stale'
    )
  })
})
