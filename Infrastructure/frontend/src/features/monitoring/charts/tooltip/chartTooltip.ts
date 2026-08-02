/**
 * Cursor tooltip plugin.
 *
 * Renders a floating tooltip near the cursor listing each visible series'
 * value, unit, origin, and quality at the hovered x index. The tooltip element
 * is created on `ready`, updated on `setCursor`, and removed on `destroy`.
 */
import uPlot from 'uplot'
import type { AlignedSeries } from '../../data'
import { isEnvelopeSeries, seriesColor } from '../options/seriesOptions'

export interface TooltipColors {
  bg: string
  border: string
  text: string
}

interface TooltipSeries {
  index: number
  label: string
  unit?: string
  color: string
  origin: string
  quality: string
}

/** Build a uPlot plugin that renders a value/provenance cursor tooltip. */
export function tooltipPlugin(
  series: AlignedSeries[],
  colors: TooltipColors,
): uPlot.Plugin {
  let el: HTMLDivElement | null = null

  const meta: TooltipSeries[] = series
    .map((s, i) => ({
      index: i + 1,
      label: s.label,
      unit: s.unit,
      color: seriesColor(s),
      origin: s.origin,
      quality: s.quality,
    }))
    .filter((s) => !isEnvelopeSeries(series[s.index - 1]))

  return {
    hooks: {
      ready: (u) => {
        el = document.createElement('div')
        el.className = 'mon-tooltip'
        el.style.position = 'absolute'
        el.style.pointerEvents = 'none'
        el.style.background = colors.bg
        el.style.border = `1px solid ${colors.border}`
        el.style.color = colors.text
        el.style.padding = '4px 8px'
        el.style.fontSize = '12px'
        el.style.zIndex = '10'
        el.style.display = 'none'
        u.root.appendChild(el)
      },
      setCursor: (u) => {
        if (el === null) return
        const dataIdx = u.cursor.idx
        if (dataIdx === null || dataIdx === undefined || dataIdx < 0) {
          el.style.display = 'none'
          return
        }
        el.textContent = ''
        for (const s of meta) {
          if (u.series[s.index].show === false) continue
          const value = u.data[s.index]?.[dataIdx]
          const row = document.createElement('div')
          const swatch = document.createElement('span')
          swatch.style.color = s.color
          swatch.textContent = s.label
          row.appendChild(swatch)
          const text =
            value === null || value === undefined
              ? ' —'
              : ` ${value.toFixed(1)}${s.unit !== undefined ? ` ${s.unit}` : ''}`
          row.appendChild(document.createTextNode(text))
          const prov = document.createElement('em')
          prov.textContent = ` ${s.origin}/${s.quality}`
          row.appendChild(prov)
          el.appendChild(row)
        }
        el.style.display = el.childNodes.length > 0 ? 'block' : 'none'
      },
      destroy: (u) => {
        if (el !== null && el.parentNode === u.root) u.root.removeChild(el)
        el = null
      },
    },
  }
}
