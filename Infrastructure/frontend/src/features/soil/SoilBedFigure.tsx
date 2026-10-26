/**
 * One Flower bed on the Soil page (R9): a square with the bed's aspect
 * ratio holding the heatmap canvas, solid markers for placed probes and
 * hollow markers for placements closed within 72 h, then the colour legend
 * and the bed's probe cards (`children`).
 */
import { useEffect, useRef, type ReactNode } from 'react'

import { MonitoringFreshness } from '../monitoring/components'

import type { SoilMetricName } from './api/contracts'
import { formatPastAge } from './format'
import {
  HEATMAP_GRID,
  legendGradient,
  paintHeatmap,
  type BedGeometry,
  type HeatPoint,
} from './heatmap'

export type HeatmapMetric = 'off' | SoilMetricName

const METRIC_FORMAT: Record<SoilMetricName, { decimals: number; unit: string }> = {
  water_content: { decimals: 1, unit: ' %' },
  temperature: { decimals: 1, unit: ' °C' },
  ec: { decimals: 0, unit: ' µS/cm' },
  ph: { decimals: 2, unit: '' },
}

export function formatSoilMetric(metric: SoilMetricName, value: number): string {
  const { decimals, unit } = METRIC_FORMAT[metric]
  return `${value.toFixed(decimals)}${unit}`
}

export interface ActiveMarker {
  readonly address: number
  readonly x_frac: number
  readonly y_frac: number
  readonly value: number | null
}

export interface ClosedMarker {
  readonly address: number
  readonly x_frac: number
  readonly y_frac: number
  readonly removed_at: Date
}

function markerStyle(x: number, y: number): React.CSSProperties {
  return {
    left: `${x * 100}%`,
    top: `${y * 100}%`,
    transform: 'translate(-50%, -50%)',
  }
}

export default function SoilBedFigure({
  name,
  testId,
  geometry,
  metric,
  heatPoints,
  domain,
  activeMarkers,
  closedMarkers,
  nowMs,
  lastGoodAt,
  errorAt,
  children,
}: {
  name: string
  testId: 'back' | 'front'
  geometry: BedGeometry | null
  metric: HeatmapMetric
  heatPoints: readonly HeatPoint[]
  domain: [number, number] | null
  activeMarkers: readonly ActiveMarker[]
  closedMarkers: readonly ClosedMarker[]
  nowMs: number
  lastGoodAt: Date | null
  errorAt: Date | null
  children?: ReactNode
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const valueMetric: SoilMetricName = metric === 'off' ? 'water_content' : metric

  useEffect(() => {
    const ctx = canvasRef.current?.getContext('2d') ?? null
    if (ctx === null) return
    if (metric === 'off' || geometry === null) {
      ctx.clearRect(0, 0, HEATMAP_GRID, HEATMAP_GRID)
      return
    }
    paintHeatmap(ctx, geometry, heatPoints, metric, domain, nowMs)
  }, [metric, geometry, heatPoints, domain, nowMs])

  const empty = activeMarkers.length === 0 && closedMarkers.length === 0
  const showLegend = metric !== 'off' && heatPoints.length > 0 && domain !== null

  return (
    <figure aria-label={`${name} bed schematic`} className="mon-card w-full">
      <figcaption className="mon-card__title text-xs font-semibold uppercase tracking-wide text-mon-text-secondary">
        {name}
      </figcaption>
      <MonitoringFreshness lastGoodAt={lastGoodAt} errorAt={errorAt} />
      <div
        className="relative w-full rounded border border-border-emphasis bg-surface-base"
        style={{
          aspectRatio: geometry ? `${geometry.width_ft} / ${geometry.length_ft}` : '1 / 1',
        }}
      >
        <canvas
          ref={canvasRef}
          width={HEATMAP_GRID}
          height={HEATMAP_GRID}
          data-testid={`soil-heatmap-${testId}`}
          aria-hidden="true"
          className="absolute inset-0 h-full w-full"
          style={{ imageRendering: 'auto' }}
        />
        {empty && (
          <p className="absolute inset-0 grid place-items-center text-sm text-mon-text-secondary">
            No placed probes
          </p>
        )}
        {closedMarkers.map(marker => (
          <div
            key={`closed-${marker.address}-${marker.removed_at.getTime()}`}
            data-testid={`soil-marker-closed-${marker.address}`}
            className="absolute flex items-center gap-0.5 whitespace-nowrap font-mono text-[10px] text-mon-text-secondary"
            style={markerStyle(marker.x_frac, marker.y_frac)}
          >
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 rounded-full border border-current"
            />
            {`#${marker.address} · ${formatPastAge(nowMs - marker.removed_at.getTime())}`}
          </div>
        ))}
        {activeMarkers.map(marker => (
          <div
            key={marker.address}
            data-testid={`soil-marker-${marker.address}`}
            className="absolute flex items-center gap-0.5 whitespace-nowrap font-mono text-[10px] font-semibold text-text-default"
            style={markerStyle(marker.x_frac, marker.y_frac)}
          >
            <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full bg-current" />
            {`#${marker.address} ${
              marker.value === null ? '—' : formatSoilMetric(valueMetric, marker.value)
            }`}
          </div>
        ))}
      </div>
      {showLegend && domain !== null && (
        <div data-testid="soil-heatmap-legend" className="mt-1 text-[10px] text-mon-text-secondary">
          <div
            aria-hidden="true"
            className="h-2 w-full rounded-sm"
            style={{ backgroundImage: legendGradient(valueMetric) }}
          />
          <div className="flex justify-between font-mono">
            <span>{formatSoilMetric(valueMetric, domain[0])}</span>
            <span>{formatSoilMetric(valueMetric, domain[1])}</span>
          </div>
        </div>
      )}
      {children}
    </figure>
  )
}
