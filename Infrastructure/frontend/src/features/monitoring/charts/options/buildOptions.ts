/**
 * Assembles the full themed uPlot options for the monitoring chart.
 *
 * Wires the family series, per-family scales/axes, min/max bands, and the
 * photoperiod / now-divider / tooltip plugins into one `uPlot.Options`, while
 * preserving the adapter's `ms: 1`, `tzDate`, and `setScale`/`setSeries` hooks.
 */
import uPlot from 'uplot'
import type { AlignedData } from '../../data'
import { nowDividerPlugin } from '../plugins/nowDividerPlugin'
import { photoperiodPlugin } from '../plugins/photoperiodPlugin'
import { tooltipPlugin } from '../tooltip/chartTooltip'
import { buildBands } from './bands'
import { buildScales } from './scales'
import { buildSeries } from './seriesOptions'
import { readToken } from './tokens'

/** Callbacks the adapter wires into uPlot hooks. */
export interface ChartCallbacks {
  onSetScale: (self: uPlot, scaleKey: string) => void
  onSetSeries: (self: uPlot, seriesIdx: number | null, opts: uPlot.Series) => void
}

/** Build the complete themed uPlot options for the aligned data. */
export function buildOptions(
  data: AlignedData,
  width: number,
  height: number,
  callbacks: ChartCallbacks,
): uPlot.Options {
  const { scales, axes } = buildScales(data)
  const series = buildSeries(data)
  const bands = buildBands(data)

  const nowX =
    data.nowIndex >= 0 && data.nowIndex < data.x.length ? data.x[data.nowIndex] : null

  const plugins: uPlot.Plugin[] = [
    photoperiodPlugin(data.photoperiod, {
      sunBg: readToken('sunBg'),
      moonBg: readToken('moonBg'),
    }),
    tooltipPlugin(data.series, {
      bg: readToken('tooltipBg'),
      border: readToken('tooltipBorder'),
      text: readToken('tooltipText'),
    }),
  ]
  if (nowX !== null) {
    plugins.push(nowDividerPlugin(nowX, readToken('focusRing')))
  }

  return {
    width,
    height,
    ms: 1,
    tzDate: (ts) => new Date(ts),
    series,
    scales,
    axes,
    bands,
    plugins,
    hooks: {
      setScale: [callbacks.onSetScale],
      setSeries: [callbacks.onSetSeries],
    },
  }
}
