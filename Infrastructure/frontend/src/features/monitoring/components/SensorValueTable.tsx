/**
 * Current live sensor value table.
 *
 * Renders a manifest table panel (e.g. Flower "Front Cluster" / "Back Cluster"
 * or Veg "Sensor Values") as a semantic HTML table. Rows follow the manifest's
 * canonical order; each row maps to a live sensor value for the given node
 * suffix. Missing values render as an em dash, stale values use the
 * `--mon-stale` token, and a "Last Update" row shows the most recent live
 * observation timestamp.
 */
import type { LiveSensorValue } from '../api'
import {
  DEFAULT_STALE_AFTER_MS,
  FAMILY_TO_UNIT,
  formatTimestamp,
  formatValue,
  isStale,
} from './tables/tableFormat'
import { familyForRow, sensorNameForRow } from './tables/tableManifest'

export interface SensorValueTableProps {
  title: string
  /** Canonical row labels in display order (may include "Last Update"). */
  rows: string[]
  /** Live values for this node. */
  values: LiveSensorValue[]
  /** Node suffix used to build sensor names (`f`, `b`, or `v`). */
  nodeSuffix: 'f' | 'b' | 'v'
  /** Reference "now" for staleness; defaults to the current time. */
  now?: Date
  /** Staleness threshold in ms; defaults to 60 s. */
  staleAfterMs?: number
}

const TH =
  'px-1 py-1 text-left text-xs uppercase tracking-wider text-[color:var(--mon-text-secondary)] font-semibold border-b border-border-default bg-surface-secondary'
const TD = 'px-1 py-1 border-b border-border-subtle'

export function SensorValueTable({
  title,
  rows,
  values,
  nodeSuffix,
  now = new Date(),
  staleAfterMs = DEFAULT_STALE_AFTER_MS,
}: SensorValueTableProps) {
  const bySensor = new Map(values.map((v) => [v.sensor, v]))
  const lastUpdate = values.reduce<Date | null>(
    (acc, v) => (acc === null || v.timestamp > acc ? v.timestamp : acc),
    null,
  )

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" aria-label={title}>
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr>
            <th scope="col" colSpan={2} className={TH}>
              {title}
            </th>
          </tr>
          <tr>
            <th scope="col" className={TH}>
              Sensor
            </th>
            <th scope="col" className={TH}>
              Value
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            if (row === 'Last Update') {
              return (
                <tr key={row}>
                  <td className={TD}>{row}</td>
                  <td className={TD}>{lastUpdate ? formatTimestamp(lastUpdate) : '—'}</td>
                </tr>
              )
            }
            const sensor = sensorNameForRow(row, nodeSuffix)
            const live = sensor ? bySensor.get(sensor) : undefined
            const family = familyForRow(row)
            const unit = family ? (FAMILY_TO_UNIT[family] ?? '') : ''
            const stale = live !== undefined && isStale(live.timestamp, now, staleAfterMs)
            return (
              <tr key={row}>
                <td className={TD}>{row}</td>
                <td className={TD}>
                  {live === undefined ? (
                    <span aria-label={`${row} unavailable`}>—</span>
                  ) : (
                    <span
                      style={stale ? { color: 'var(--mon-stale)' } : undefined}
                      title={stale ? `Stale (last update ${formatTimestamp(live.timestamp)})` : undefined}
                      aria-label={stale ? `${row} stale` : undefined}
                    >
                      {formatValue(live.value, family ?? '')}
                      {unit}
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
