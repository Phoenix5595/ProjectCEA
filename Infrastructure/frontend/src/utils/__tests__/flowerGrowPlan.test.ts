import { describe, expect, it } from 'vitest'

import {
  buildFlowerGrowPlanPreview,
  defaultBedVegStart,
  flowerStartFor,
  type FlowerGrowPlanInput,
  type FlowerStartInput,
} from '../flowerGrowPlan'

const flowerStartInput: FlowerStartInput = {
  anchor: 'flower_start',
  anchorDate: '2026-11-02',
  bedVegDays: 14,
  stretchDays: 21,
  bulkDays: 21,
  ripenDays: 21,
}

const baseInput: FlowerGrowPlanInput = {
  ...flowerStartInput,
  bedVegStart: '2026-10-19',
  dryingDays: 7,
  potStart: null,
  cloneStart: null,
}

describe('flowerStartFor anchors', () => {
  it('derives the same flower start from veg start, flower start and ripen end', () => {
    expect(flowerStartFor(flowerStartInput)).toBe('2026-11-02')
    expect(
      flowerStartFor({ ...flowerStartInput, anchor: 'bed_veg_start', anchorDate: '2026-10-19' })
    ).toBe('2026-11-02')
    expect(
      flowerStartFor({ ...flowerStartInput, anchor: 'ripen_end', anchorDate: '2027-01-03' })
    ).toBe('2026-11-02')
    expect(defaultBedVegStart(flowerStartInput)).toBe('2026-10-19')
  })
})

describe('buildFlowerGrowPlanPreview', () => {
  it('builds identical phases from all three anchors', () => {
    const expected = buildFlowerGrowPlanPreview(baseInput)
    expect(expected.error).toBeUndefined()

    expect(
      buildFlowerGrowPlanPreview({
        ...baseInput,
        anchor: 'bed_veg_start',
        anchorDate: '2026-10-19',
      })
    ).toEqual(expected)
    expect(
      buildFlowerGrowPlanPreview({ ...baseInput, anchor: 'ripen_end', anchorDate: '2027-01-03' })
    ).toEqual(expected)
  })

  it('places bed veg before flower and harvest on the last ripen day', () => {
    const { phases } = buildFlowerGrowPlanPreview(baseInput)

    expect(phases.find(phase => phase.eventType === 'bed_veg')).toMatchObject({
      location: 'Flower Room',
      start: '2026-10-19',
      end: '2026-11-01',
      phaseOrder: 3,
    })
    expect(phases.find(phase => phase.eventType === 'flower_stretch')).toMatchObject({
      start: '2026-11-02',
      end: '2026-11-22',
      phaseOrder: 5,
    })
    expect(phases.find(phase => phase.eventType === 'flower_bulk')).toMatchObject({
      start: '2026-11-23',
      end: '2026-12-13',
      phaseOrder: 6,
    })
    expect(phases.find(phase => phase.eventType === 'flower_ripen')).toMatchObject({
      start: '2026-12-14',
      end: '2027-01-03',
      phaseOrder: 7,
    })
    expect(phases.find(phase => phase.eventType === 'harvest')).toMatchObject({
      start: '2027-01-03',
      end: '2027-01-03',
      phaseOrder: 8,
    })
    expect(phases.find(phase => phase.eventType === 'drying')).toMatchObject({
      start: '2027-01-04',
      end: '2027-01-10',
      phaseOrder: 9,
    })
    expect(phases.map(phase => phase.phaseOrder)).toEqual([3, 4, 5, 6, 7, 8, 9])
  })

  it('adds optional clone and pot windows in the Veg Room', () => {
    const { phases } = buildFlowerGrowPlanPreview({
      ...baseInput,
      potStart: '2026-10-05',
      cloneStart: '2026-09-14',
    })

    expect(phases.find(phase => phase.eventType === 'clone_window')).toMatchObject({
      location: 'Veg Room',
      start: '2026-09-14',
      end: '2026-10-04',
      phaseOrder: 1,
    })
    expect(phases.find(phase => phase.eventType === 'pot_veg')).toMatchObject({
      location: 'Veg Room',
      start: '2026-10-05',
      end: '2026-10-18',
      phaseOrder: 2,
    })
    expect(phases.map(phase => phase.phaseOrder)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
  })

  it('ends the clone window before the veg bed when no pot phase is set', () => {
    const { phases } = buildFlowerGrowPlanPreview({ ...baseInput, cloneStart: '2026-09-28' })

    expect(phases.find(phase => phase.eventType === 'clone_window')).toMatchObject({
      start: '2026-09-28',
      end: '2026-10-18',
    })
    expect(phases.some(phase => phase.eventType === 'pot_veg')).toBe(false)
  })

  it.each<[string, Partial<FlowerGrowPlanInput>, string]>([
    [
      'a bed-veg anchor that differs from the bed veg start',
      { anchor: 'bed_veg_start', anchorDate: '2026-10-20' },
      'bed_veg_start must equal anchor_date for the bed_veg_start anchor',
    ],
    [
      'bed veg that starts on the flower start',
      { bedVegStart: '2026-11-02' },
      'Bed veg must start before flower start',
    ],
    [
      'a pot start on or after the bed veg start',
      { potStart: '2026-10-19' },
      'pot_start must be before bed_veg_start',
    ],
    [
      'a clone start on or after the pot start',
      { potStart: '2026-10-05', cloneStart: '2026-10-05' },
      'clone_start must be before pot_start',
    ],
    [
      'a clone start after the bed veg start with no pot phase',
      { cloneStart: '2026-10-19' },
      'clone_start must be before bed_veg_start',
    ],
  ])('reports %s', (_name, overrides, message) => {
    const { phases, error } = buildFlowerGrowPlanPreview({ ...baseInput, ...overrides })

    expect(error).toBe(message)
    expect(phases).toEqual([])
  })
})
