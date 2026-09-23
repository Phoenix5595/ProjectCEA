import type { TrendMetric } from './dashboardStatus'

interface DashboardMiniTrendProps {
  metric: TrendMetric | undefined
  compact?: boolean
}

function formatDelta(metric: TrendMetric): string {
  if (metric.delta10m == null) return 'Δ10m unavailable'
  const precision = metric.metric === 'co2' ? 0 : 2
  const sign = metric.delta10m >= 0 ? '+' : ''
  return `Δ10m ${sign}${metric.delta10m.toFixed(precision)} ${metric.unit}`
}

function trendTooltip(metric: TrendMetric): string {
  const delta =
    metric.delta10m == null
      ? 'The 10-minute change is unavailable because history does not span enough time.'
      : `The 10-minute change is newest minus the closest sample at or before 10 minutes ago: ${formatDelta(metric)}.`
  return `${metric.label} (${metric.unit}): trailing 60-minute sensor history. The sparkline uses bucket averages and auto-scales vertically. ${delta}`
}

export function DashboardMiniTrend({ metric, compact = false }: DashboardMiniTrendProps) {
  if (!metric || metric.points.length < 2) {
    return (
      <div
        title={`${metric?.label ?? 'Sensor'} trend unavailable. Historical samples are missing or insufficient to draw a meaningful trend.`}
        className="flex min-w-[6.5rem] flex-col justify-center rounded-sm bg-surface-secondary px-1.5 py-1 text-10 text-text-muted cursor-help"
      >
        <span>{metric?.label ?? 'Trend'}</span>
        <span>Trend unavailable</span>
      </div>
    )
  }

  const width = compact ? 84 : 108
  const height = 28
  const min = Math.min(...metric.points.map(point => point.value))
  const max = Math.max(...metric.points.map(point => point.value))
  const range = max - min || 1
  const points = metric.points
    .map((point, index) => {
      const x = (index / Math.max(1, metric.points.length - 1)) * width
      const y = height - ((point.value - min) / range) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  return (
    <div
      title={trendTooltip(metric)}
      aria-label={`${metric.label} 60 minute trend; ${formatDelta(metric)}`}
      className="flex min-w-[6.5rem] flex-col rounded-sm bg-surface-secondary px-1.5 py-1 text-10 cursor-help"
    >
      <div className="flex items-center justify-between gap-1 text-text-muted">
        <span>{metric.label}</span>
        <span className="font-mono tabular-nums">{formatDelta(metric)}</span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-hidden="true"
        className="mt-0.5 h-7 w-full"
      >
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          className="text-accent-data"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  )
}
