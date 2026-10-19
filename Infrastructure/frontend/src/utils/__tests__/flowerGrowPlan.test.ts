import { describe, expect, it } from 'vitest'

import { buildFlowerGrowPlanPreview, type FlowerGrowPlanInput } from '../flowerGrowPlan'

const baseInput: FlowerGrowPlanInput = {
  flowerEnd: '2026-10-01',
  flowerWeeks: 9,
  includePotPhases: false,
  cloneWeeks: 3,
  potWeeks: 2,
  bedWeeks: 3,
  stretchDays: 21,
  ripenDays: 21,
  dryingDays: 7,
}

describe('buildFlowerGrowPlanPreview bed phase', () => {
  it.each([false, true])('keeps %s pot inclusion separate from Flower bed weeks', includePotPhases => {
    const { phases, error } = buildFlowerGrowPlanPreview({ ...baseInput, includePotPhases })

    expect(error).toBeUndefined()
    const bed = phases.find(phase => phase.eventType === 'bed_veg')
    expect(bed).toMatchObject({ location: 'Flower Room', phaseOrder: 3 })
    expect(bed).toBeDefined()
    const inclusiveDays = Math.round(
      (new Date(`${bed!.end}T00:00:00Z`).getTime() -
        new Date(`${bed!.start}T00:00:00Z`).getTime()) /
        86_400_000
    ) + 1
    expect(inclusiveDays).toBe(baseInput.bedWeeks * 7)

    const earlyVegetation = phases.filter(phase => ['clone_window', 'pot_veg'].includes(phase.eventType))
    expect(earlyVegetation.every(phase => phase.location === 'Veg Room')).toBe(true)
    expect(earlyVegetation.length).toBe(includePotPhases ? 2 : 0)
  })
})
