/**
 * Genuine selected-range summary tables for the history overview.
 *
 * Rows use the backend sample-level sensor statistics for the selected range.
 * Control summaries are deliberately not presented here. The table fits its
 * parent without an internal scroll container.
 */
import type { HistoryOverviewSensorResponse } from './contracts'
import type { MonitoringManifest, SeriesSpec } from '../monitoring/config'
import type { ReactElement } from 'react'

const TH =
  'px-1 py-1 text-left text-xs uppercase tracking-wider text-mon-text-secondary font-semibold border-b border-border-default bg-surface-secondary'
const TD = 'px-1 py-1 border-b border-border-subtle'
const SENSOR_HEADERS = ['Sensor', 'Count', 'Min', 'Average', 'Max', 'Std Dev'] as const

export interface HistorySummaryStatistic {
  readonly sensor: string
  readonly sample_count: number
  readonly minimum: number | null
  readonly maximum: number | null
  readonly average: number | null
  readonly stddev_samp: number | null
  readonly stddev_quality?: 'exact' | 'approximate'
}

export interface HistoryOverviewTablesProps {
  readonly sensor?: HistoryOverviewSensorResponse | null
  /** Exact detailed or overview sensor statistics from an archive graph response. */
  readonly statistics?: readonly HistorySummaryStatistic[] | null
  readonly manifest?: MonitoringManifest
}

function formatValue(value: number | null, decimals = 1): string {
  if (value === null) return '—'
  return value.toFixed(decimals)
}

function manifestSeriesSpecs(manifest: MonitoringManifest | undefined): SeriesSpec[] {
  if (manifest === undefined) return []
  return manifest.panels.flatMap(panel => (panel.kind === 'timeseries' ? panel.series : []))
}

function specLabel(specs: SeriesSpec[], name: string, fallback: string): string {
  const spec = specs.find(candidate => candidate.name === name || candidate.displayName === name)
  return spec?.displayName ?? fallback
}

export function HistoryOverviewTables({
  sensor,
  statistics: suppliedStatistics,
  manifest,
}: HistoryOverviewTablesProps): ReactElement | null {
  const statistics = suppliedStatistics ?? sensor?.statistics ?? []
  if (statistics.length === 0) return null
  const specs = manifestSeriesSpecs(manifest)
  return (
    <table className="w-full text-xs">
      <caption className="sr-only">Selected range summary</caption>
      <thead>
        <tr>
          {SENSOR_HEADERS.map(label => (
            <th key={label} scope="col" className={TH}>
              {label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {statistics.map(statistics => {
          const stdDevText =
            statistics.stddev_samp === null
              ? '—'
              : `${statistics.stddev_samp.toFixed(2)}${
                  statistics.stddev_quality === 'approximate' ? ' ≈' : ''
                }`
          return (
            <tr key={statistics.sensor}>
              <td className={TD}>{specLabel(specs, statistics.sensor, statistics.sensor)}</td>
              <td className={TD}>{statistics.sample_count}</td>
              <td className={TD}>{formatValue(statistics.minimum)}</td>
              <td className={TD}>{formatValue(statistics.average)}</td>
              <td className={TD}>{formatValue(statistics.maximum)}</td>
              <td className={TD}>{stdDevText}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
