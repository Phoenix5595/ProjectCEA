import type uPlot from 'uplot'

import { CURRENT_MARKER_DIAMETER_PX, UNIT_LABELS, type TimelineScale, type TimelineSeriesMeta } from './timelineOptions'
import type { TimelineSampleQualities } from './timelineSources'

export type TimelineTooltipState = {
  readonly data: uPlot.AlignedData
  readonly meta: readonly TimelineSeriesMeta[]
  /** Per-series sample quality at the cursor, keyed by series key. */
  readonly qualities?: TimelineSampleQualities
  /** Window-start instant (epoch ms) used to render sample timestamps. */
  readonly windowStartMs?: number
}

/** Render one series value with its family unit, e.g. "23.5 °C". */
export function formatTooltipValue(scale: TimelineScale, value: number): string {
  if (scale === 'co2') return `${Math.round(value)} ${UNIT_LABELS.co2}`
  if (scale === 'vpd') return `${value.toFixed(2)} ${UNIT_LABELS.vpd}`
  return `${value.toFixed(1)} ${UNIT_LABELS.temp}`
}

/** Render one sample's observation timestamp from its window-minute x value. */
export function formatTooltipTimestamp(
  windowStartMs: number,
  minute: number
): string {
  const instant = new Date(windowStartMs + minute * 60_000)
  const hours = String(instant.getUTCHours()).padStart(2, '0')
  const minutesText = String(instant.getUTCMinutes()).padStart(2, '0')
  return `${hours}:${minutesText}:${String(instant.getUTCSeconds()).padStart(2, '0')}.${String(instant.getUTCMilliseconds()).padStart(3, '0')} UTC`
}

/** One provenance line fragment: role and quality, never color alone. */
export function tooltipProvenanceLabel(
  entry: Pick<TimelineSeriesMeta, 'role' | 'kind' | 'label'>,
  quality: string | null
): string {
  const qualityText =
    quality === 'exact' || quality === 'estimated' || quality === 'unavailable'
      ? ` · ${quality}`
      : ''
  return `${entry.label} · ${entry.role}${qualityText}`
}

const TOOLTIP_PADDING_PX = 12
const HTML_ENTITIES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
const escapeTooltipText = (value: string) => value.replace(/[&<>"']/g, character => HTML_ENTITIES[character]!)
const EDGE_MARGIN_PX = 4

/**
 * Hover values with units, authority role, and per-sample quality. A row
 * renders its label plus `role · quality` provenance so tooltip readers can
 * distinguish observation, estimated running forecast, saved hypothetical and
 * draft without relying on color.
 */
export function unitTooltipPlugin(getState: () => TimelineTooltipState): uPlot.Plugin {
  let tooltip: HTMLDivElement | null = null
  let indexedData: uPlot.AlignedData | null = null
  const currentIndices: number[] = []

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
    const { data, meta, qualities, windowStartMs } = getState()
    if (indexedData !== data) {
      indexedData = data
      currentIndices.length = data.length
      currentIndices.fill(-1)
      for (let seriesIndex = 1; seriesIndex < data.length; seriesIndex += 1) {
        if (meta[seriesIndex - 1]?.role === 'active-current') {
          currentIndices[seriesIndex] = data[seriesIndex]?.findIndex(value => value != null && Number.isFinite(value)) ?? -1
        }
      }
    }
    const lines: string[] = []
    if (windowStartMs !== undefined && data[0] !== undefined) {
      const minute = data[0]?.[idx]
      if (minute != null && Number.isFinite(minute)) {
        lines.push(
          `<span style="color:#94a3b8">${formatTooltipTimestamp(windowStartMs, minute)}</span>`
        )
      }
    }
    for (let seriesIndex = 1; seriesIndex < data.length; seriesIndex += 1) {
      const entry = meta[seriesIndex - 1]
      if (entry === undefined) continue
      let sampleIndex = idx
      if (entry.role === 'active-current') {
        sampleIndex = currentIndices[seriesIndex] ?? -1
        if (sampleIndex < 0) continue
        const minute = data[0]?.[sampleIndex]
        if (minute == null || Math.abs(plot.valToPos(minute, 'x') - left) > CURRENT_MARKER_DIAMETER_PX / 2) continue
      }
      const value = data[seriesIndex]?.[sampleIndex]
      if (value == null || !Number.isFinite(value)) continue
      const quality = qualities?.get(entry.key)?.[sampleIndex] ?? null
      const timestamp = entry.role === 'active-current' && windowStartMs !== undefined
        ? ` · ${formatTooltipTimestamp(windowStartMs, data[0]![sampleIndex]!)}`
        : ''
      lines.push(
        `<span style="color:${entry.stroke}">●</span> ${formatTooltipValue(entry.scale, value)} — ${escapeTooltipText(tooltipProvenanceLabel(entry, quality) + timestamp)}`
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
      EDGE_MARGIN_PX
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
