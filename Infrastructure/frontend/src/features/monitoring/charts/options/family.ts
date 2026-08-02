/**
 * Metric-family resolution for the monitoring chart.
 *
 * Every aligned series maps to exactly one chart family. Temperature is the
 * only left-axis family; all others render on the right. Families are resolved
 * from the series key prefix (light/climate), then the label keywords, then the
 * sensor unit family as a fallback.
 */
import type { UnitFamily } from '../../api'
import type { AlignedSeries } from '../../data'

export type ChartFamily =
  | 'temperature'
  | 'rh'
  | 'vpd'
  | 'co2'
  | 'pressure'
  | 'device'
  | 'light'

/** Label keyword matchers, checked in order. */
const FAMILY_BY_LABEL: Array<[RegExp, ChartFamily]> = [
  [/light/i, 'light'],
  [/vpd/i, 'vpd'],
  [/co2/i, 'co2'],
  [/humidity|\brh\b/i, 'rh'],
  [/pressure/i, 'pressure'],
  [/temp/i, 'temperature'],
]

/** Unit-family fallback for sensor series without a recognizable label. */
const FAMILY_BY_UNIT: Record<UnitFamily, ChartFamily> = {
  celsius: 'temperature',
  kpa: 'vpd',
  ppm: 'co2',
  hpa: 'pressure',
  percent: 'device',
  mm: 'device',
}

/** Resolve the chart family for an aligned series. */
export function resolveFamily(series: AlignedSeries): ChartFamily {
  if (series.key.startsWith('light:')) return 'light'
  for (const [re, family] of FAMILY_BY_LABEL) {
    if (re.test(series.label)) return family
  }
  if (series.key.startsWith('climate:')) return 'device'
  if (series.unitFamily !== undefined) return FAMILY_BY_UNIT[series.unitFamily]
  return 'device'
}
