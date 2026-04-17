import { minutesInPeriod } from './climatePeriodSchedule';

export interface RoomScheduleLike {
  day_start_time?: string;
  day_end_time?: string;
}

function parseHHMM(t: string | undefined): number {
  if (!t) return 0;
  const s = t.substring(0, 5);
  const [h, m] = s.split(':').map((x) => parseInt(x, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) return 0;
  return (h % 24) * 60 + (m % 60);
}

/** Next calendar Date at today's wall-clock minute `min` (0–1439); if already passed, tomorrow. */
function nextOccurrenceOfMinute(min: number, now: Date): Date {
  const h = Math.floor(min / 60) % 24;
  const mm = min % 60;
  const d = new Date(now);
  d.setSeconds(0, 0);
  d.setHours(h, mm, 0, 0);
  if (d.getTime() <= now.getTime()) {
    d.setDate(d.getDate() + 1);
  }
  return d;
}

export interface PhotoperiodCountdown {
  lightsOn: boolean;
  secondsUntilOpen: number | null;
  secondsUntilClose: number | null;
}

/**
 * Photoperiod: lights on between day_start_time and day_end_time (same semantics as room_schedule API).
 */
export function getPhotoperiodCountdown(schedule: RoomScheduleLike | null, now: Date): PhotoperiodCountdown {
  if (!schedule?.day_start_time || !schedule?.day_end_time) {
    return { lightsOn: false, secondsUntilOpen: null, secondsUntilClose: null };
  }
  const startM = parseHHMM(schedule.day_start_time);
  const endM = parseHHMM(schedule.day_end_time);
  const m = now.getHours() * 60 + now.getMinutes();
  const lightsOn = minutesInPeriod(m, startM, endM);

  let secondsUntilOpen: number | null = null;
  let secondsUntilClose: number | null = null;

  if (lightsOn) {
    const closeAt = nextOccurrenceOfMinute(endM, now);
    secondsUntilClose = Math.max(0, Math.floor((closeAt.getTime() - now.getTime()) / 1000));
  } else {
    const openAt = nextOccurrenceOfMinute(startM, now);
    secondsUntilOpen = Math.max(0, Math.floor((openAt.getTime() - now.getTime()) / 1000));
  }

  return { lightsOn, secondsUntilOpen, secondsUntilClose };
}
