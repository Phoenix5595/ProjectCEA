/**
 * Exact min/max/avg/stddev statistics table.
 *
 * Renders the canonical "Statistics - All Available Sensors" panel as a
 * semantic HTML table. Rows follow the manifest's canonical order; each row
 * maps to a `SensorStatistics` entry by sensor name. Missing statistics render
 * as an em dash. Numeric column headers are sortable (ascending/descending);
 * the Sensor column is not.
 */
import { useState } from 'react'
import type { SensorStatistics } from '../api'
import { formatValue } from './tables/tableFormat'
import { familyForStatRow, sensorNameForStatRow } from './tables/tableManifest'

export interface StatisticsTableProps {
  title: string
  /** Canonical display labels in order (e.g. "Dry Bulb (°C) - Front"). */
  rows: string[]
  /** Exact statistics from the store. */
  statistics: SensorStatistics[]
}

type SortKey = 'minimum' | 'maximum' | 'average' | 'stddev_samp'
type SortDir = 'asc' | 'desc'

const NUMERIC_COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'minimum', label: 'Min' },
  { key: 'maximum', label: 'Max' },
  { key: 'average', label: 'Average' },
  { key: 'stddev_samp', label: 'Std Dev' },
]

const TH =
  'px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-b border-border-default bg-surface-secondary'
const TD = 'px-1 py-1 border-b border-border-subtle'

export function StatisticsTable({ title, rows, statistics }: StatisticsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null)
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  const bySensor = new Map(statistics.map((s) => [s.sensor, s]))
  const dataRows = rows.map((row) => ({
    row,
    stat: bySensor.get(sensorNameForStatRow(row) ?? ''),
  }))

  const sorted = [...dataRows].sort((a, b) => {
    if (sortKey === null) return 0
    if (a.stat === undefined && b.stat === undefined) return 0
    if (a.stat === undefined) return 1
    if (b.stat === undefined) return -1
    const cmp = a.stat[sortKey] - b.stat[sortKey]
    return sortDir === 'asc' ? cmp : -cmp
  })

  const toggleSort = (key: SortKey): void => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" aria-label={title}>
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr>
            <th scope="col" className={TH}>
              Sensor
            </th>
            {NUMERIC_COLUMNS.map((col) => (
              <th key={col.key} scope="col" className={TH}>
                <button
                  type="button"
                  onClick={() => toggleSort(col.key)}
                  aria-sort={
                    sortKey === col.key ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'
                  }
                  className="uppercase tracking-wider text-text-muted font-semibold hover:text-text-default"
                >
                  {col.label}
                  {sortKey === col.key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map(({ row, stat }) => (
            <tr key={row}>
              <td className={TD}>{row}</td>
              {NUMERIC_COLUMNS.map((col) => (
                <td key={col.key} className={TD}>
                  {stat === undefined ? (
                    <span aria-label={`${row} ${col.label} unavailable`}>—</span>
                  ) : (
                    formatValue(stat[col.key], familyForStatRow(row) ?? '')
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
