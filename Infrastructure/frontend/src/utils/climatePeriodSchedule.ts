import type { ClimatePeriod } from '../types/climatePeriod';

function parseHHMM(t: string): number {
  const s = (t || '00:00').substring(0, 5);
  const [h, m] = s.split(':').map((x) => parseInt(x, 10));
  if (Number.isNaN(h) || Number.isNaN(m)) return 0;
  return (h % 24) * 60 + (m % 60);
}

function minuteOfDay(d: Date): number {
  return d.getHours() * 60 + d.getMinutes();
}

/** Whether `nowM` falls inside [start, end) with optional overnight wrap. */
export function minutesInPeriod(nowM: number, startM: number, endM: number): boolean {
  if (startM < endM) {
    return nowM >= startM && nowM < endM;
  }
  if (startM > endM) {
    return nowM >= startM || nowM < endM;
  }
  return false;
}

export function findActiveClimatePeriod(periods: ClimatePeriod[], now: Date): ClimatePeriod | null {
  if (!periods?.length) return null;
  const m = minuteOfDay(now);
  for (const p of periods) {
    const s = parseHHMM(p.start_time);
    const e = parseHHMM(p.end_time);
    if (minutesInPeriod(m, s, e)) return p;
  }
  return null;
}

/** Next instant when the active climate period boundary is crossed (end of current period). */
export function nextClimatePeriodBoundaryDate(periods: ClimatePeriod[], now: Date): Date | null {
  const active = findActiveClimatePeriod(periods, now);
  if (!active) return null;
  const endM = parseHHMM(active.end_time);
  const d = new Date(now);
  d.setSeconds(0, 0);
  const h = Math.floor(endM / 60) % 24;
  const min = endM % 60;
  d.setHours(h, min, 0, 0);
  if (d.getTime() <= now.getTime()) {
    d.setDate(d.getDate() + 1);
  }
  return d;
}

export function secondsUntilNextClimateBoundary(periods: ClimatePeriod[], now: Date): number | null {
  const t = nextClimatePeriodBoundaryDate(periods, now);
  if (!t) return null;
  return Math.max(0, Math.floor((t.getTime() - now.getTime()) / 1000));
}

/** Period that follows `active` in table order (wrap); used for "next" label/setpoints preview. */
export function findNextClimatePeriodAfter(
  periods: ClimatePeriod[],
  active: ClimatePeriod | null
): ClimatePeriod | null {
  if (!periods.length) return null;
  if (!active) return periods[0];
  const idx = periods.findIndex((p) => p === active);
  if (idx < 0) return periods[0];
  return periods[(idx + 1) % periods.length];
}

export function formatCountdown(totalSeconds: number): string {
  const s = Math.floor(totalSeconds % 60);
  const m = Math.floor((totalSeconds / 60) % 60);
  const h = Math.floor(totalSeconds / 3600);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}
