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
  formatLastUpdate,
  formatTimestamp,
  formatValue,
  isStale,
} from './tables/tableFormat'
import { familyForRow, sensorNameForRow } from './tables/tableManifest'
import { seriesKey } from '../data/alignSeries.types'
import { getSeriesVisibilitySnapshot, isSeriesHidden, subscribeSeriesVisibility, toggleSeries } from '../charts/seriesVisibility'
import { useSyncExternalStore } from 'react'

export interface SensorValueTableProps {
  title: string
  showTitle?: boolean
  firstColumnLabel?: string
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
  'px-1 py-1 text-left text-xs uppercase tracking-wider text-mon-text-secondary font-semibold border-b border-border-default bg-surface-secondary'
const TD = 'px-1 py-1 border-b border-border-subtle'
const LABEL_TD = 'w-[75px] min-w-[75px] max-w-[75px] px-1 py-1 border-b border-border-subtle'

export function SensorValueTable({
  title,
  showTitle = true,
  firstColumnLabel = 'Sensor',
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

  const lastUpdateDisplay = lastUpdate === null ? null : formatLastUpdate(lastUpdate)
  const visibility = useSyncExternalStore(subscribeSeriesVisibility, getSeriesVisibilitySnapshot)

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" aria-label={title}>
        <caption className="sr-only">{title}</caption>
        <thead>
          {showTitle && (
            <tr>
              <th scope="col" colSpan={2} className={TH}>
                {title}
              </th>
            </tr>
          )}
          <tr>
            <th scope="col" className={TH}>
              {firstColumnLabel}
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
                  <td className={LABEL_TD}>{row}</td>
                  <td className={TD}>
                    {lastUpdateDisplay === null ? (
                      '—'
                    ) : (
                      <span className="mon-last-update">
                        <span className="block">{lastUpdateDisplay[0]}</span>
                        <span className="block">{lastUpdateDisplay[1]}</span>
                      </span>
                    )}
                  </td>
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
                <td className={LABEL_TD}>
                  <RowToggle row={row} nodeSuffix={nodeSuffix} visibility={visibility} />
                </td>
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

interface RowToggleProps {
  row: string
  nodeSuffix: 'f' | 'b' | 'v'
  visibility: ReturnType<typeof getSeriesVisibilitySnapshot>
}

/** The row label doubles as a series toggle once its chart series is registered:
 *  pressing it greys the box out and hides the whole sensor group (mean + min +
 *  max, which also drops the envelope band) so no orphaned "bucket" lines stay
 *  behind; the dot carries the series color. Layout stays untouched when no
 *  series exists yet. */
function RowToggle({ row, nodeSuffix, visibility }: RowToggleProps) {
  const sensor = sensorNameForRow(row, nodeSuffix)
  if (sensor === null) return <>{row}</>
  const keys = (['mean', 'min', 'max'] as const).map((role) => seriesKey('sensor', sensor, role))
  const key = keys[0]
  const color = visibility.known.get(key)
  if (color === undefined) return <>{row}</>
  const visible = !visibility.hidden.has(key)
  return (
    <button
      type="button"
      aria-pressed={!visible}
      onClick={() => {
        // Toggle only roles whose state differs from the target (show===hidden reads inverted).
        const show = !visible
        for (const k of keys) {
          if (show === isSeriesHidden(k)) toggleSeries(k)
        }
      }}
      title={visible ? `Hide ${row} lines` : `Show ${row} lines`}
      className="flex w-full min-w-0 items-center gap-1 overflow-hidden text-left whitespace-nowrap text-ellipsis text-text-default transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent-vivid"
      style={visible ? undefined : { opacity: 0.4 }}
    >
      <span
        aria-hidden
        className="inline-block size-2 shrink-0 rounded-[2px]"
        style={{ background: color }}
      />
      <span className="truncate">{row}</span>
    </button>
  )
}
