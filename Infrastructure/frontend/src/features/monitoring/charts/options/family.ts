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
  | 'water_content'
  | 'conductivity'
  | 'ph'

export function resolveFamily(series: AlignedSeries): ChartFamily {
  return series.family
}

export function chartFamilyForUnit(unitFamily: UnitFamily): ChartFamily {
  switch (unitFamily) {
    case 'celsius':
      return 'temperature'
    case 'kpa':
      return 'vpd'
    case 'ppm':
      return 'co2'
    case 'hpa':
      return 'pressure'
    case 'percent':
      return 'water_content'
    case 'mm':
      return 'device'
  }
}
