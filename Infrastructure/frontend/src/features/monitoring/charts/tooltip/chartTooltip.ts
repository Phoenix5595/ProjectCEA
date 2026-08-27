/**
 * Cursor tooltip plugin.
 *
 * Renders a floating tooltip near the cursor listing each visible series'
 * value, unit, origin, and quality at the hovered x index. The tooltip element
 * is created on `ready`, updated on `setCursor`, and removed on `destroy`.
 */
import uPlot from 'uplot'
import type { AlignedSeries, SeriesKind, SeriesPresentation } from '../../data'
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
  kind: SeriesKind
  presentation?: SeriesPresentation
}

function finiteValue(value: number | null | undefined): number | null {
  return value === null || value === undefined || !Number.isFinite(value) ? null : value
}

/** Resolves the appropriate series value at the exact cursor time. */
export function valueAtCursor(
  kind: SeriesKind,
  xValues: ArrayLike<number>,
  yValues: ArrayLike<number | null | undefined>,
  cursorTime: number,
): number | null {
  let rightIndex = 0
  while (rightIndex < xValues.length && xValues[rightIndex] < cursorTime) rightIndex += 1

  const rightTime = xValues[rightIndex]
  if (rightTime === cursorTime) return finiteValue(yValues[rightIndex])
  if (rightIndex === 0 || rightIndex === xValues.length) return null

  const leftIndex = rightIndex - 1
  const leftTime = xValues[leftIndex]
  const leftValue = finiteValue(yValues[leftIndex])
  const rightValue = finiteValue(yValues[rightIndex])
  if (leftTime === undefined || rightTime === undefined || leftValue === null || rightValue === null) {
    return null
  }

  switch (kind) {
    case 'step':
      return leftValue
    case 'linear':
    case 'sensor':
      return leftValue + ((rightValue - leftValue) * (cursorTime - leftTime)) / (rightTime - leftTime)
    case 'point':
      return cursorTime - leftTime <= rightTime - cursorTime ? leftValue : rightValue
  }
}

/** Formats one rendered tooltip value, preserving configured presentation precision. */
export function formatTooltipValue(
  value: number | null,
  presentation: SeriesPresentation | undefined,
  unit: string | undefined,
): string {
  if (value === null) return ' —'
  const decimals = presentation?.decimals ?? 1
  return ` ${value.toFixed(decimals)}${unit !== undefined ? ` ${unit}` : ''}`
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
      kind: s.kind,
      presentation: s.presentation,
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
        const cursorLeft = u.cursor.left
        const cursorTop = u.cursor.top
        const xValues = u.data[0]
        if (
          cursorLeft === null || cursorLeft === undefined || cursorLeft < 0 ||
          cursorTop === null || cursorTop === undefined || cursorTop < 0 ||
          xValues === undefined
        ) {
          el.style.display = 'none'
          return
        }
        const cursorTime = u.posToVal(cursorLeft, 'x')
        el.textContent = ''
        for (const s of meta) {
          if (u.series[s.index].show === false) continue
          const value = valueAtCursor(s.kind, xValues, u.data[s.index] ?? [], cursorTime)
          const row = document.createElement('div')
          const swatch = document.createElement('span')
          swatch.style.color = s.color
          swatch.textContent = s.label
          row.appendChild(swatch)
          const text = formatTooltipValue(value, s.presentation, s.unit)
          row.appendChild(document.createTextNode(text))
          const prov = document.createElement('em')
          prov.textContent = ` ${s.origin}/${s.quality}`
          row.appendChild(prov)
          el.appendChild(row)
        }
        if (el.childNodes.length === 0) {
          el.style.display = 'none'
          return
        }
        el.style.display = 'block'
        const inset = 12
        const maxLeft = Math.max(0, u.root.clientWidth - el.offsetWidth)
        const maxTop = Math.max(0, u.root.clientHeight - el.offsetHeight)
        el.style.left = `${Math.min(maxLeft, Math.max(0, cursorLeft + inset))}px`
        el.style.top = `${Math.min(maxTop, Math.max(0, cursorTop + inset))}px`
      },
      destroy: (u) => {
        if (el !== null && el.parentNode === u.root) u.root.removeChild(el)
        el = null
      },
    },
  }
}
