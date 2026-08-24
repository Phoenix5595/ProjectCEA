/**
 * Flower Room averages table.
 *
 * Averages are computed only from matching Front and Back measurements paired
 * by base sensor name (e.g. `dry_bulb_f` + `dry_bulb_b`). If either side is
 * missing the average is unavailable and renders as an em dash — a missing
 * Front cluster is a valid empty state, never a fabricated value. The "Last
 * Update" row uses the most recent live timestamp across both clusters.
 */
import type { LiveSensorValue } from '../api'
import { FAMILY_TO_UNIT, formatTimestamp, formatValue } from './tables/tableFormat'
import { baseLabelForAverage, familyForRow, ROW_TO_BASE } from './tables/tableManifest'

export interface RoomAveragesTableProps {
  title: string
  /** Canonical average row labels (e.g. "Dry Bulb Avg", ..., "Last Update"). */
  rows: string[]
  /** Live values from the Front cluster. */
  front: LiveSensorValue[]
  /** Live values from the Back cluster. */
  back: LiveSensorValue[]
}

const TH =
  'px-1 py-1 text-left text-xs uppercase tracking-wider text-[color:var(--mon-text-secondary)] font-semibold border-b border-border-default bg-surface-secondary'
const TD = 'px-1 py-1 border-b border-border-subtle'

export function RoomAveragesTable({ title, rows, front, back }: RoomAveragesTableProps) {
  const frontBySensor = new Map(front.map((v) => [v.sensor, v]))
  const backBySensor = new Map(back.map((v) => [v.sensor, v]))
  const all = [...front, ...back]
  const lastUpdate = all.reduce<Date | null>(
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
            const base = ROW_TO_BASE[baseLabelForAverage(row)]
            const f = base ? frontBySensor.get(`${base}_f`) : undefined
            const b = base ? backBySensor.get(`${base}_b`) : undefined
            const family = familyForRow(baseLabelForAverage(row))
            const unit = family ? (FAMILY_TO_UNIT[family] ?? '') : ''
            const value = f !== undefined && b !== undefined ? (f.value + b.value) / 2 : null
            return (
              <tr key={row}>
                <td className={TD}>{row}</td>
                <td className={TD}>
                  {value === null ? (
                    <span aria-label={`${row} unavailable`}>—</span>
                  ) : (
                    <span>
                      {formatValue(value, family ?? '')}
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
