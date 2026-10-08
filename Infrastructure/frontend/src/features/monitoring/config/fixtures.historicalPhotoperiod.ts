/**
 * Exact `historical-photoperiod` browser-proof scenario.
 *
 * The phase timeline is anchored in absolute UTC (T = 2026-08-20T12:00:00Z)
 * and never regenerates: every history/tail request returns the supported
 * state at the requested start plus in-range transitions, so zooming or
 * re-requesting can never move an absolute transition. This mirrors the
 * backend semantics of the new transition stream: transitions hold
 * indefinitely, one read-side state point is emitted at the requested start
 * from supported predecessor evidence, and unsupported spans stay absent so
 * the client renders UNKNOWN.
 */

const T0 = '2026-08-20T12:00:00.000Z'

export const HISTORICAL_T_MS = Date.parse(T0)

interface PhaseAnchor {
  readonly hours: number
  readonly phase: 'SUN' | 'MOON' | 'UNKNOWN'
  readonly origin: 'recorded' | 'derived'
  readonly quality: 'exact' | 'estimated' | 'unavailable'
}

/** The eight exact plan-specified anchors, in hours relative to T. */
const ANCHORS: readonly PhaseAnchor[] = [
  { hours: -30, phase: 'SUN', origin: 'recorded', quality: 'exact' },
  { hours: -27, phase: 'MOON', origin: 'recorded', quality: 'exact' },
  { hours: -24, phase: 'UNKNOWN', origin: 'recorded', quality: 'unavailable' },
  { hours: -23, phase: 'SUN', origin: 'derived', quality: 'estimated' },
  { hours: -21, phase: 'SUN', origin: 'recorded', quality: 'exact' },
  { hours: -18, phase: 'MOON', origin: 'recorded', quality: 'exact' },
  { hours: -9, phase: 'SUN', origin: 'recorded', quality: 'exact' },
  { hours: -6, phase: 'MOON', origin: 'recorded', quality: 'exact' },
]

const HOUR_MS = 3_600_000

function anchorMs(anchor: PhaseAnchor): number {
  return HISTORICAL_T_MS + anchor.hours * HOUR_MS
}

/** Newest anchor strictly before `t`, or null when no evidence supports t. */
function supportedBefore(t: number): PhaseAnchor | null {
  let found: PhaseAnchor | null = null
  for (const anchor of ANCHORS) {
    if (anchorMs(anchor) < t) found = anchor
  }
  return found
}

/** Supported state at the requested start plus in-range absolute changes. */
export function historicalPhotoperiodHistory(
  start: string,
  end: string
): Array<Record<string, unknown>> {
  const startMs = Date.parse(start)
  const endMs = Date.parse(end)
  const entries: Array<[number, PhaseAnchor]> = []
  const predecessor = supportedBefore(startMs)
  if (predecessor !== null) entries.push([startMs, predecessor])
  for (const anchor of ANCHORS) {
    const ts = anchorMs(anchor)
    if (ts >= startMs && ts < endMs) entries.push([ts, anchor])
  }
  return entries.map(([ts, anchor]) => ({
    timestamp: new Date(ts).toISOString(),
    phase: anchor.phase,
    provenance: {
      origin: anchor.origin,
      quality: anchor.quality,
      is_aggregated: false,
    },
  }))
}

/**
 * Independent basic publication for the projected span: its only series is
 * `light.photoperiod` (1 = SUN, 0 = MOON) with the existing publication
 * version/generation fields over `[T, T+1h)`.
 */
export function historicalPhotoperiodProjection(): Record<string, unknown> {
  const version = { contract_version: 1, config_version: 7, revision: 'f1c7a11' }
  return {
    quality: 'estimated',
    value: [
      {
        version,
        generated_at: T0,
        valid_from: T0,
        valid_until: '2026-08-20T13:00:00.000Z',
        series: [
          {
            series_id: { value: 'light.photoperiod' },
            value: 1,
            quality: 'estimated',
            valid_from: T0,
            valid_until: '2026-08-20T12:30:00.000Z',
          },
        ],
      },
      {
        version,
        generated_at: T0,
        valid_from: '2026-08-20T12:30:00.000Z',
        valid_until: '2026-08-20T13:00:00.000Z',
        series: [
          {
            series_id: { value: 'light.photoperiod' },
            value: 0,
            quality: 'estimated',
            valid_from: '2026-08-20T12:30:00.000Z',
            valid_until: '2026-08-20T13:00:00.000Z',
          },
        ],
      },
    ],
  }
}
