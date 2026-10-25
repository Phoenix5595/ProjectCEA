/**
 * Raw live tail for the monitoring alignment layer (R4).
 *
 * A `LiveTailBuffer` keeps, per sensor, the raw live samples that arrived
 * after the last completed recorded bucket (`baseCompleteUntil`). At most one
 * raw point per bucket survives:
 *
 * - a sample whose Redis observation timestamp did not advance (`t <= last t`)
 *   is ignored, so one-second polling never duplicates the same instant;
 * - a sample inside the bucket of the retained point replaces it, so the
 *   newest value of an in-progress bucket wins;
 * - otherwise the sample is appended.
 *
 * Completed buckets from the incremental refresh advance `baseCompleteUntil`
 * and trim every raw point that a recorded bucket now covers.
 */
export interface LiveTailPoint {
  readonly t: number
  readonly v: number
}

const DEFAULT_INTERVAL_MS = 1_000

function normalizeInterval(intervalMs: number): number {
  return Number.isFinite(intervalMs) && intervalMs > 0
    ? Math.floor(intervalMs)
    : DEFAULT_INTERVAL_MS
}

export class LiveTailBuffer {
  private intervalMsValue: number
  private completeUntil: number | null = null
  private readonly bySensor = new Map<string, LiveTailPoint[]>()
  private revisionValue = 0

  constructor(intervalMs = DEFAULT_INTERVAL_MS) {
    this.intervalMsValue = normalizeInterval(intervalMs)
  }

  /** Monotonic revision; changes whenever points or the baseline change. */
  get revision(): number {
    return this.revisionValue
  }

  get intervalMs(): number {
    return this.intervalMsValue
  }

  /** First instant not covered by a completed recorded bucket, or null. */
  get baseCompleteUntil(): number | null {
    return this.completeUntil
  }

  /** Total retained raw points across every sensor. */
  get size(): number {
    let total = 0
    for (const points of this.bySensor.values()) total += points.length
    return total
  }

  /**
   * Install a new recorded baseline (after a full or incremental response).
   * Raw points older than the baseline are dropped.
   */
  reset(baseCompleteUntil: number | null, intervalMs = this.intervalMsValue): void {
    const nextInterval = normalizeInterval(intervalMs)
    if (
      this.completeUntil === baseCompleteUntil &&
      this.intervalMsValue === nextInterval
    ) {
      return
    }
    this.intervalMsValue = nextInterval
    this.completeUntil = baseCompleteUntil
    this.trim()
    this.revisionValue++
  }

  /** Advance only the baseline, keeping raw points at or after it. */
  advance(baseCompleteUntil: number | null): void {
    if (this.completeUntil === baseCompleteUntil) return
    this.completeUntil = baseCompleteUntil
    this.trim()
    this.revisionValue++
  }

  clear(): void {
    if (this.bySensor.size === 0 && this.completeUntil === null) return
    this.bySensor.clear()
    this.completeUntil = null
    this.revisionValue++
  }

  push(sample: { readonly sensor: string; readonly value: number; readonly timestamp: Date | number }): void {
    const t =
      typeof sample.timestamp === 'number' ? sample.timestamp : sample.timestamp.getTime()
    if (!Number.isFinite(t) || !Number.isFinite(sample.value)) return
    if (this.completeUntil !== null && t < this.completeUntil) return
    const points = this.bySensor.get(sample.sensor) ?? []
    const last = points[points.length - 1]
    if (last !== undefined) {
      if (t <= last.t) return
      if (Math.floor(t / this.intervalMsValue) === Math.floor(last.t / this.intervalMsValue)) {
        points[points.length - 1] = { t, v: sample.value }
        this.bySensor.set(sample.sensor, points)
        this.revisionValue++
        return
      }
    }
    points.push({ t, v: sample.value })
    this.bySensor.set(sample.sensor, points)
    this.revisionValue++
  }

  pushMany(
    samples: readonly {
      readonly sensor: string
      readonly value: number
      readonly timestamp: Date | number
    }[]
  ): void {
    for (const sample of samples) this.push(sample)
  }

  /** Retained raw points for one sensor, oldest first. */
  pointsFor(sensor: string): readonly LiveTailPoint[] {
    return this.bySensor.get(sensor) ?? []
  }

  /** Every retained sensor tail, sorted by sensor name for a stable grid. */
  entries(): readonly (readonly [string, readonly LiveTailPoint[]])[] {
    return [...this.bySensor.entries()].sort(([left], [right]) => left.localeCompare(right))
  }

  private trim(): void {
    const floor = this.completeUntil
    if (floor === null) return
    for (const [sensor, points] of this.bySensor) {
      const retained = points.filter(point => point.t >= floor)
      if (retained.length === 0) {
        this.bySensor.delete(sensor)
        continue
      }
      if (retained.length !== points.length) this.bySensor.set(sensor, retained)
    }
  }
}
