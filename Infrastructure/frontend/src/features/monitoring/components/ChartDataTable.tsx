/**
 * Chart "View data as table" disclosure.
 *
 * The accessible data alternative for a uPlot chart region. It consumes the
 * exact `AlignedData` model the chart renders and presents it as a collapsible
 * semantic table: one timestamp column plus one column per visible non-envelope
 * series, with each cell showing the value, unit, and provenance (origin /
 * quality). A button toggles visibility with `aria-expanded`.
 */
import { useState } from 'react'
import type { AlignedData } from '../data'
import { formatTimestamp, formatValue } from './tables/tableFormat'

export interface ChartDataTableProps {
  title: string
  /** The exact aligned model consumed by the chart. */
  data: AlignedData
}

const TH =
  'px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-b border-border-default bg-surface-secondary'
const TD = 'px-1 py-1 border-b border-border-subtle'

function slugify(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

export function ChartDataTable({ title, data }: ChartDataTableProps) {
  const [open, setOpen] = useState(false)
  const tableId = `chart-data-${slugify(title)}`

  // Envelope (min/max) series feed bands and are not data columns.
  const envelopeKeys = new Set<string>()
  for (const band of data.bands) {
    envelopeKeys.add(band.minKey)
    envelopeKeys.add(band.maxKey)
  }
  const visible = data.series.filter((s) => !envelopeKeys.has(s.key))

  return (
    <div>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={tableId}
        onClick={() => setOpen((o) => !o)}
        className="px-2 py-1 text-xs font-medium rounded-md bg-surface-secondary text-text-default border border-border-default hover:bg-surface-primary transition-colors"
      >
        View data as table
      </button>
      {open && (
        <div className="overflow-x-auto mt-2">
          <table id={tableId} className="w-full text-xs" aria-label={`${title} data`}>
            <caption className="sr-only">{title} data table</caption>
            <thead>
              <tr>
                <th scope="col" className={TH}>
                  Time
                </th>
                {visible.map((s) => (
                  <th key={s.key} scope="col" className={TH}>
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.x.map((t, i) => (
                <tr key={t}>
                  <td className={TD}>{formatTimestamp(new Date(t))}</td>
                  {visible.map((s) => {
                    const v = s.y[i]
                    return (
                      <td key={s.key} className={TD}>
                        {v === null || v === undefined ? (
                          <span>—</span>
                        ) : (
                          <span>
                            {formatValue(v, s.unitFamily ?? '')}
                            {s.unit ?? ''}
                            <span className="text-text-subtle"> ({s.origin}/{s.quality})</span>
                          </span>
                        )}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
