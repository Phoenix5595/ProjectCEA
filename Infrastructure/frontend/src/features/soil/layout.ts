/**
 * Soil SCADA layout primitives: deterministic probe placement inside each
 * 4 ft x 4 ft bed square, plus palette/pattern primitives shared with the
 * historical chart.
 *
 * Probe identity is a stable eight-color palette; metric identity is a line
 * pattern (Water content solid, EC dashed, pH dotted, temperature dash-dot)
 * so color is never the only distinction. No slot coordinates persist.
 */

export interface ProbePosition {
  readonly x: number
  readonly y: number
}

/** Fixed percentage coordinates for 0-4 probes inside one bed square. */
export const PROBE_LAYOUT: Readonly<Record<0 | 1 | 2 | 3 | 4, readonly ProbePosition[]>> = {
  0: [],
  1: [{ x: 50, y: 50 }],
  2: [
    { x: 30, y: 50 },
    { x: 70, y: 50 },
  ],
  3: [
    { x: 50, y: 28 },
    { x: 32, y: 68 },
    { x: 68, y: 68 },
  ],
  4: [
    { x: 30, y: 30 },
    { x: 70, y: 30 },
    { x: 30, y: 70 },
    { x: 70, y: 70 },
  ],
}

export interface SoilProbeLike {
  readonly registry_id: number
  readonly bed: string
}

/** Group live probes per bed, stable-sorted by numeric Modbus address. */
export function groupByBed<T extends SoilProbeLike>(probes: readonly T[]): {
  frontBed: T[]
  backBed: T[]
} {
  const assigned = probes
    .filter((probe) => probe.bed === 'Front Bed' || probe.bed === 'Back Bed')
    .sort((left, right) => left.registry_id - right.registry_id)
  return {
    frontBed: assigned.filter((probe) => probe.bed === 'Front Bed'),
    backBed: assigned.filter((probe) => probe.bed === 'Back Bed'),
  }
}
