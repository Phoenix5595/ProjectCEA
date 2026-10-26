/** Flower grow plan date math — America/Toronto. */
import { addDays, format, parseISO } from 'date-fns'
import { toZonedTime } from 'date-fns-tz'

export const CALENDAR_TZ = 'America/Toronto'

export type FlowerGrowPlanAnchor = 'bed_veg_start' | 'flower_start' | 'ripen_end'

/** Fields shared by the flower-start derivations. */
export interface FlowerStartInput {
  anchor: FlowerGrowPlanAnchor
  anchorDate: string
  bedVegDays: number
  stretchDays: number
  bulkDays: number
  ripenDays: number
}

export interface FlowerGrowPlanInput extends FlowerStartInput {
  bedVegStart: string
  dryingDays: number
  potStart: string | null
  cloneStart: string | null
}

export interface PlanPhasePreview {
  eventType: string
  title: string
  location: string
  start: string
  end: string
  phaseOrder: number
}

function parseLocalDate(isoDate: string): Date {
  return toZonedTime(parseISO(isoDate), CALENDAR_TZ)
}

function fmt(d: Date): string {
  return format(d, 'yyyy-MM-dd')
}

/** Shift a yyyy-MM-dd date by whole days in the calendar timezone. */
export function shiftIsoDate(isoDate: string, days: number): string {
  return fmt(addDays(parseLocalDate(isoDate), days))
}

/** First day of flower, derived from the plan anchor. */
export function flowerStartFor(input: FlowerStartInput): string {
  if (input.anchor === 'flower_start') return input.anchorDate
  if (input.anchor === 'bed_veg_start') return shiftIsoDate(input.anchorDate, input.bedVegDays)
  return shiftIsoDate(input.anchorDate, -(input.stretchDays + input.bulkDays + input.ripenDays) + 1)
}

/** Bed veg start that fills exactly `bedVegDays` before flower start. */
export function defaultBedVegStart(input: FlowerStartInput): string {
  return shiftIsoDate(flowerStartFor(input), -input.bedVegDays)
}

export function buildFlowerGrowPlanPreview(input: FlowerGrowPlanInput): {
  phases: PlanPhasePreview[]
  error?: string
} {
  if (input.anchor === 'bed_veg_start' && input.bedVegStart !== input.anchorDate) {
    return {
      phases: [],
      error: 'bed_veg_start must equal anchor_date for the bed_veg_start anchor',
    }
  }

  const flowerStartIso = flowerStartFor(input)
  if (input.bedVegStart >= flowerStartIso) {
    return { phases: [], error: 'Bed veg must start before flower start' }
  }
  if (input.potStart !== null && input.potStart >= input.bedVegStart) {
    return { phases: [], error: 'pot_start must be before bed_veg_start' }
  }
  if (input.cloneStart !== null) {
    const cloneBound = input.potStart ?? input.bedVegStart
    if (input.cloneStart >= cloneBound) {
      return {
        phases: [],
        error:
          input.potStart !== null
            ? 'clone_start must be before pot_start'
            : 'clone_start must be before bed_veg_start',
      }
    }
  }

  const flowerStart = parseLocalDate(flowerStartIso)
  const stretchEnd = addDays(flowerStart, input.stretchDays - 1)
  const bulkEnd = addDays(stretchEnd, input.bulkDays)
  const ripenEnd = addDays(bulkEnd, input.ripenDays)

  const phases: PlanPhasePreview[] = []

  if (input.cloneStart !== null) {
    phases.push({
      eventType: 'clone_window',
      title: 'Clone',
      location: 'Veg Room',
      start: input.cloneStart,
      end: shiftIsoDate(input.potStart ?? input.bedVegStart, -1),
      phaseOrder: 1,
    })
  }
  if (input.potStart !== null) {
    phases.push({
      eventType: 'pot_veg',
      title: 'Pot veg',
      location: 'Veg Room',
      start: input.potStart,
      end: shiftIsoDate(input.bedVegStart, -1),
      phaseOrder: 2,
    })
  }

  phases.push(
    {
      eventType: 'bed_veg',
      title: 'Bed veg',
      location: 'Flower Room',
      start: input.bedVegStart,
      end: shiftIsoDate(flowerStartIso, -1),
      phaseOrder: 3,
    },
    {
      eventType: 'flip_to_flower',
      title: 'Flip to flower',
      location: 'Flower Room',
      start: flowerStartIso,
      end: flowerStartIso,
      phaseOrder: 4,
    },
    {
      eventType: 'flower_stretch',
      title: 'Stretch',
      location: 'Flower Room',
      start: flowerStartIso,
      end: fmt(stretchEnd),
      phaseOrder: 5,
    },
    {
      eventType: 'flower_bulk',
      title: 'Bulk',
      location: 'Flower Room',
      start: fmt(addDays(stretchEnd, 1)),
      end: fmt(bulkEnd),
      phaseOrder: 6,
    },
    {
      eventType: 'flower_ripen',
      title: 'Ripen',
      location: 'Flower Room',
      start: fmt(addDays(bulkEnd, 1)),
      end: fmt(ripenEnd),
      phaseOrder: 7,
    },
    {
      eventType: 'harvest',
      title: 'Harvest',
      location: 'Flower Room',
      start: fmt(ripenEnd),
      end: fmt(ripenEnd),
      phaseOrder: 8,
    },
    {
      eventType: 'drying',
      title: 'Drying',
      location: 'Flower Room',
      start: fmt(addDays(ripenEnd, 1)),
      end: fmt(addDays(ripenEnd, input.dryingDays)),
      phaseOrder: 9,
    }
  )

  return { phases }
}
