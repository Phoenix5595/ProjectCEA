/** Shared aligned-feed empty shape for the soil history chart lifecycle. */
import type { AlignedData } from '../monitoring/data'

export function emptyAlignedData(): AlignedData {
  return {
    x: [],
    series: [],
    bands: [],
    photoperiod: [],
    nowIndex: -1,
    aggregated: false,
  }
}
