/**
 * External semantic legend for the monitoring chart.
 *
 * Renders each visible series as a real button swatch (click / Enter / Space
 * toggle via `aria-pressed`), a reset action that restores every series, and a
 * visually-hidden-but-discoverable semantic table that doubles as the chart's
 * accessible data alternative.
 */
import type { CSSProperties } from 'react'
export interface LegendEntry {
  key: string
  label: string
  color: string
  projected: boolean
  /** uPlot series index (1-based; 0 is the time axis). */
  index: number
  visible: boolean
}

export interface ExternalLegendProps {
  entries: LegendEntry[]
  onToggle: (index: number, show: boolean) => void
  onReset: () => void
}

const visuallyHidden: CSSProperties = {
  position: 'absolute',
  width: '1px',
  height: '1px',
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
}

export function ExternalLegend({ entries, onToggle, onReset }: ExternalLegendProps) {
  return (
    <div className="mon-legend">
      <ul className="mon-legend__list">
        {entries.map((entry) => (
          <li key={entry.key}>
            <button
              type="button"
              aria-pressed={entry.visible}
              onClick={() => onToggle(entry.index, !entry.visible)}
              className="mon-legend__swatch"
            >
              <span
                className="mon-legend__color"
                style={{ background: entry.color }}
                aria-hidden="true"
              />
              <span>
                {entry.label}
                {entry.projected ? ' (Projected)' : ''}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={onReset} className="mon-legend__reset">
        Reset
      </button>
      <table className="mon-legend__table" style={visuallyHidden}>
        <caption>Chart series data</caption>
        <thead>
          <tr>
            <th scope="col">Series</th>
            <th scope="col">Visible</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((entry) => (
            <tr key={entry.key}>
              <td>{entry.label}</td>
              <td>{entry.visible ? 'Yes' : 'No'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
