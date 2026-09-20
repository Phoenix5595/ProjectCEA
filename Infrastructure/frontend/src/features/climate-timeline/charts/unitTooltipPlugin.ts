import type uPlot from 'uplot'
import { UNIT_LABELS, type TimelineScale, type TimelineSeriesMeta } from './timelineOptions'

export type TimelineTooltipState = {
  readonly data: uPlot.AlignedData
  readonly meta: readonly TimelineSeriesMeta[]
}

/** Render one series value with its family unit, e.g. "23.5 °C". */
export function formatTooltipValue(scale: TimelineScale, value: number): string {
  if (scale === 'co2') return `${Math.round(value)} ${UNIT_LABELS.co2}`
  if (scale === 'vpd') return `${value.toFixed(2)} ${UNIT_LABELS.vpd}`
  return `${value.toFixed(1)} ${UNIT_LABELS.temp}`
}

const TOOLTIP_PADDING_PX = 12
const EDGE_MARGIN_PX = 4

/**
 * Hover tooltip listing every series value at the cursor with its family unit.
 * The units left the axes; they live here, next to the values they qualify.
 */
export function unitTooltipPlugin(getState: () => TimelineTooltipState): uPlot.Plugin {
  let tooltip: HTMLDivElement | null = null

  const hide = (): void => {
    if (tooltip !== null) tooltip.style.display = 'none'
  }

  const render = (plot: uPlot): void => {
    if (tooltip === null) return
    const left = plot.cursor.left ?? -1
    const top = plot.cursor.top ?? -1
    const idx = plot.cursor.idx ?? null
    if (left < 0 || idx === null) {
      hide()
      return
    }
    const { data, meta } = getState()
    const lines: string[] = []
    for (let seriesIndex = 1; seriesIndex < data.length; seriesIndex += 1) {
      const value = data[seriesIndex]?.[idx]
      const entry = meta[seriesIndex - 1]
      if (value == null || !Number.isFinite(value) || entry === undefined) continue
      lines.push(
        `<span style="color:${entry.stroke}">●</span> ${formatTooltipValue(entry.scale, value)}`,
      )
    }
    if (lines.length === 0) {
      hide()
      return
    }
    tooltip.innerHTML = lines.join('<br>')

    const width = tooltip.offsetWidth
    const height = tooltip.offsetHeight
    const flip = left + width + TOOLTIP_PADDING_PX > plot.over.clientWidth
    const x = flip
      ? Math.max(left - width - TOOLTIP_PADDING_PX, EDGE_MARGIN_PX)
      : left + TOOLTIP_PADDING_PX
    const y = Math.max(
      Math.min(top - height - EDGE_MARGIN_PX, plot.over.clientHeight - height),
      EDGE_MARGIN_PX,
    )
    tooltip.style.left = `${x}px`
    tooltip.style.top = `${y}px`
    tooltip.style.display = 'block'
  }

  return {
    hooks: {
      init(u: uPlot) {
        tooltip = document.createElement('div')
        tooltip.className = 'timeline-unit-tooltip'
        tooltip.style.position = 'absolute'
        tooltip.style.display = 'none'
        tooltip.style.pointerEvents = 'none'
        tooltip.style.zIndex = '20'
        tooltip.style.background = 'rgba(8, 10, 14, 0.92)'
        tooltip.style.border = '1px solid rgba(128, 128, 128, 0.4)'
        tooltip.style.borderRadius = '3px'
        tooltip.style.padding = '3px 6px'
        tooltip.style.font = '10px "JetBrains Mono", ui-monospace, monospace'
        tooltip.style.color = '#e2e8f0'
        tooltip.style.whiteSpace = 'nowrap'
        tooltip.style.lineHeight = '1.4'
        u.over.appendChild(tooltip)
        u.over.addEventListener('mouseleave', hide)
      },
      setCursor: (plot: uPlot) => render(plot),
    },
  }
}
