/**
 * Semantic data table for the monitoring chart.
 *
 * The visible legend is gone — the sensor rail boxes are the toggle UI. This
 * component now only renders the visually-hidden-but-discoverable table that
 * doubles as the chart's accessible data alternative; rail toggles keep its
 * Visible column in sync through the shared visibility store.
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
}

const visuallyHidden: CSSProperties = {
  position: 'absolute',
  width: '1px',
  height: '1px',
  overflow: 'hidden',
  clip: 'rect(0 0 0 0)',
  whiteSpace: 'nowrap',
}

export function ExternalLegend({ entries }: ExternalLegendProps) {
  return (
    <div className="mon-legend" role="group" aria-label="Chart series legend">
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
              <td>{entry.label}{entry.projected ? ' (Projected)' : ''}</td>
              <td>{entry.visible ? 'Yes' : 'No'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
