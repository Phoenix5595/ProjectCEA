/** Flower grow plan date math — America/Toronto. */
import { addDays, differenceInCalendarDays, format, parseISO } from 'date-fns';
import { toZonedTime } from 'date-fns-tz';

export const CALENDAR_TZ = 'America/Toronto';

export interface FlowerGrowPlanInput {
  flowerEnd: string;
  flowerWeeks: number;
  includePotPhases: boolean;
  cloneWeeks: number;
  potWeeks: number;
  bedWeeks: number;
  stretchDays: number;
  ripenDays: number;
  dryingDays: number;
}

export interface PlanPhasePreview {
  eventType: string;
  title: string;
  location: string;
  start: string;
  end: string;
  phaseOrder: number;
}

function parseLocalDate(isoDate: string): Date {
  return toZonedTime(parseISO(isoDate), CALENDAR_TZ);
}

function fmt(d: Date): string {
  return format(d, 'yyyy-MM-dd');
}

export function buildFlowerGrowPlanPreview(
  input: FlowerGrowPlanInput
): { phases: PlanPhasePreview[]; error?: string } {
  const flowerEnd = parseLocalDate(input.flowerEnd);
  const flowerDays = input.flowerWeeks * 7;
  const bulkDays = flowerDays - input.stretchDays - input.ripenDays;
  if (bulkDays < 1) {
    const minWeeks = Math.ceil((input.stretchDays + input.ripenDays + 1) / 7);
    return {
      phases: [],
      error: `Flower length must be at least ${minWeeks} weeks for ${input.stretchDays}d stretch + ${input.ripenDays}d ripen.`,
    };
  }

  const flowerStart = addDays(flowerEnd, -(flowerDays - 1));
  const stretchEnd = addDays(flowerStart, input.stretchDays - 1);
  const ripenStart = addDays(flowerEnd, -(input.ripenDays - 1));
  const bulkStart = addDays(stretchEnd, 1);
  const bulkEnd = addDays(ripenStart, -1);

  const phases: PlanPhasePreview[] = [];

  if (input.includePotPhases) {
    const bedEnd = addDays(flowerStart, -1);
    const bedStart = addDays(bedEnd, -(input.bedWeeks * 7 - 1));
    const potEnd = addDays(bedStart, -1);
    const potStart = addDays(potEnd, -(input.potWeeks * 7 - 1));
    const cloneEnd = addDays(potStart, -1);
    const cloneStart = addDays(cloneEnd, -(input.cloneWeeks * 7 - 1));
    phases.push(
      { eventType: 'clone_window', title: 'Clone', location: 'Veg Room', start: fmt(cloneStart), end: fmt(cloneEnd), phaseOrder: 1 },
      { eventType: 'pot_veg', title: 'Pot veg', location: 'Veg Room', start: fmt(potStart), end: fmt(potEnd), phaseOrder: 2 },
      { eventType: 'bed_veg', title: 'Bed veg', location: 'Veg Room', start: fmt(bedStart), end: fmt(bedEnd), phaseOrder: 3 }
    );
  } else {
    const bedWeeksEff = input.bedWeeks + 2;
    const bedEnd = addDays(flowerStart, -1);
    const bedStart = addDays(bedEnd, -(bedWeeksEff * 7 - 1));
    phases.push({
      eventType: 'bed_veg',
      title: 'Bed veg',
      location: 'Veg Room',
      start: fmt(bedStart),
      end: fmt(bedEnd),
      phaseOrder: 3,
    });
  }

  phases.push(
    {
      eventType: 'flip_to_flower',
      title: 'Flip to flower',
      location: 'Flower Room',
      start: fmt(flowerStart),
      end: fmt(flowerStart),
      phaseOrder: 4,
    },
    {
      eventType: 'flower_stretch',
      title: 'Stretch',
      location: 'Flower Room',
      start: fmt(flowerStart),
      end: fmt(stretchEnd),
      phaseOrder: 5,
    },
    {
      eventType: 'flower_bulk',
      title: 'Bulk',
      location: 'Flower Room',
      start: fmt(bulkStart),
      end: fmt(bulkEnd),
      phaseOrder: 6,
    },
    {
      eventType: 'flower_ripen',
      title: 'Ripen',
      location: 'Flower Room',
      start: fmt(ripenStart),
      end: fmt(flowerEnd),
      phaseOrder: 7,
    },
    {
      eventType: 'drying',
      title: 'Drying',
      location: 'Flower Room',
      start: fmt(addDays(flowerEnd, 1)),
      end: fmt(addDays(flowerEnd, input.dryingDays)),
      phaseOrder: 8,
    },
    {
      eventType: 'harvest',
      title: 'Harvest',
      location: 'Flower Room',
      start: fmt(addDays(flowerEnd, input.dryingDays)),
      end: fmt(addDays(flowerEnd, input.dryingDays)),
      phaseOrder: 9,
    }
  );

  return { phases };
}

export function isFlowerEndInPast(flowerEndIso: string): boolean {
  const end = parseLocalDate(flowerEndIso);
  const today = toZonedTime(new Date(), CALENDAR_TZ);
  return differenceInCalendarDays(end, today) < 0;
}
