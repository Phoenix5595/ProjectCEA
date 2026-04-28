import type { ClimatePeriod } from '../types/climatePeriod'

export type { ClimatePeriod }

/** Header / cell / input backgrounds only — text colors stay on design tokens. */
const ZONE = {
  heat: {
    th: 'bg-red-500/15',
    td: 'bg-red-500/[0.07] group-hover:bg-red-500/12',
    input: 'bg-red-500/10'
  },
  cool: {
    th: 'bg-blue-500/15',
    td: 'bg-blue-500/[0.07] group-hover:bg-blue-500/12',
    input: 'bg-blue-500/10'
  },
  vpd: {
    th: 'bg-emerald-500/15',
    td: 'bg-emerald-500/[0.07] group-hover:bg-emerald-500/12',
    input: 'bg-emerald-500/10'
  },
  co2: {
    th: 'bg-slate-500/18',
    td: 'bg-slate-500/[0.08] group-hover:bg-slate-500/14',
    input: 'bg-slate-500/12'
  }
} as const

const inputBase =
  'px-1 py-0 text-xs border border-border-default rounded text-text-default focus:border-accent-primary focus:ring-1 focus:ring-accent-primary focus:outline-none placeholder:text-text-subtle'

function isAllDayPeriod(period: ClimatePeriod): boolean {
  return period.start_time === period.end_time
}

interface ClimatePeriodsTableProps {
  periods: ClimatePeriod[]
  onChange: (periods: ClimatePeriod[]) => void
  validationErrors?: string[]
}

export default function ClimatePeriodsTable({
  periods,
  onChange,
  validationErrors = []
}: ClimatePeriodsTableProps) {
  const addPeriod = () => {
    if (periods.length >= 7) return
    const newPeriod: ClimatePeriod = {
      period_name: `Period ${periods.length + 1}`,
      start_time: '00:00',
      end_time: '00:00',
      ramp_minutes: 0,
      heating_setpoint: null,
      cooling_setpoint: null,
      vpd_setpoint: null,
      co2_setpoint: null,
      details: ''
    }
    onChange([...periods, newPeriod])
  }

  const removePeriod = (index: number) => {
    if (periods.length <= 1) return
    onChange(periods.filter((_, i) => i !== index))
  }

  const updatePeriod = (index: number, field: keyof ClimatePeriod, value: string | number | null) => {
    const updated = [...periods]
    updated[index] = { ...updated[index], [field]: value }
    onChange(updated)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div>
          <h3 className="text-[14px] text-text-muted uppercase font-bold tracking-wider">Climate Periods</h3>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={addPeriod}
            disabled={periods.length >= 7}
            className="px-3 py-1 text-xs font-medium rounded-md bg-accent-primary text-surface-base hover:bg-accent-data disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            + Add Period
          </button>
        </div>
      </div>

      {validationErrors.length > 0 && (
        <div className="bg-red-900/20 border border-red-700 rounded-md p-2">
          <p className="text-xs font-medium text-red-400 mb-1">Validation Errors:</p>
          <ul className="text-xs text-red-300 space-y-0.5">
            {validationErrors.map((err, i) => (
              <li key={i}>• {err}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border-default">
              <th className="px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-32 bg-surface-secondary">
                Period
              </th>
              <th className="px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-16 bg-surface-secondary">
                Start
              </th>
              <th className="px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-16 bg-surface-secondary">
                End
              </th>
              <th className="px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-14 bg-surface-secondary">
                Ramp
              </th>
              <th
                className={`px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-14 ${ZONE.heat.th}`}
              >
                Heat
              </th>
              <th
                className={`px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-14 ${ZONE.cool.th}`}
              >
                Cool
              </th>
              <th
                className={`px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-18 ${ZONE.vpd.th}`}
              >
                VPD
              </th>
              <th
                className={`px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default w-18 ${ZONE.co2.th}`}
              >
                CO₂
              </th>
              <th className="px-1 py-1 text-left text-xs uppercase tracking-wider text-text-muted font-semibold border-r border-border-default bg-surface-secondary">
                Details
              </th>
              <th className="px-1 py-1 w-2 bg-surface-secondary"></th>
            </tr>
          </thead>
          <tbody>
            {periods.map((period, index) => (
              <tr
                key={index}
                className="group border-b border-border-subtle transition-colors"
              >
                <td className="px-1 py-1 border-r border-border-subtle group-hover:bg-surface-secondary/50">
                  <input
                    type="text"
                    value={period.period_name}
                    onChange={(e) => updatePeriod(index, 'period_name', e.target.value)}
                    className={`w-full text-xs bg-surface-secondary ${inputBase}`}
                    placeholder="Period name"
                  />
                </td>
                <td className="px-1 py-1 border-r border-border-subtle group-hover:bg-surface-secondary/50">
                  <div className="flex flex-col gap-0.5">
                    <input
                      type="text"
                      pattern="[0-9]{2}:[0-9]{2}"
                      placeholder="HH:MM"
                      value={period.start_time}
                      onChange={(e) => updatePeriod(index, 'start_time', e.target.value)}
                      className={`w-16 text-xs bg-surface-secondary ${inputBase}`}
                    />
                    {isAllDayPeriod(period) && (
                      <span className="text-[9px] uppercase tracking-wide text-text-subtle">All day</span>
                    )}
                  </div>
                </td>
                <td className="px-1 py-1 border-r border-border-subtle group-hover:bg-surface-secondary/50">
                  <div className="flex flex-col gap-0.5">
                    <input
                      type="text"
                      pattern="[0-9]{2}:[0-9]{2}"
                      placeholder="HH:MM"
                      value={period.end_time}
                      onChange={(e) => updatePeriod(index, 'end_time', e.target.value)}
                      className={`w-16 text-xs bg-surface-secondary ${inputBase}`}
                    />
                    {isAllDayPeriod(period) && (
                      <span className="text-[9px] uppercase tracking-wide text-text-subtle">24h</span>
                    )}
                  </div>
                </td>
                <td className="px-1 py-1 border-r border-border-subtle group-hover:bg-surface-secondary/50">
                  <input
                    type="number"
                    min="0"
                    max="240"
                    value={period.ramp_minutes}
                    onChange={(e) => updatePeriod(index, 'ramp_minutes', parseInt(e.target.value) || 0)}
                    className={`w-14 text-xs bg-surface-secondary ${inputBase}`}
                  />
                </td>
                <td className={`px-1 py-1 border-r border-border-subtle ${ZONE.heat.td}`}>
                  <input
                    type="number"
                    min="10"
                    max="35"
                    step="0.5"
                    value={period.heating_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'heating_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
                    className={`w-14 text-xs ${ZONE.heat.input} ${inputBase}`}
                    placeholder="°C"
                  />
                </td>
                <td className={`px-1 py-1 border-r border-border-subtle ${ZONE.cool.td}`}>
                  <input
                    type="number"
                    min="10"
                    max="35"
                    step="0.5"
                    value={period.cooling_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'cooling_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
                    className={`w-14 text-xs ${ZONE.cool.input} ${inputBase}`}
                    placeholder="°C"
                  />
                </td>
                <td className={`px-1 py-1 border-r border-border-subtle ${ZONE.vpd.td}`}>
                  <input
                    type="number"
                    min="0"
                    max="5"
                    step="0.01"
                    value={period.vpd_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'vpd_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
                    className={`w-18 text-xs ${ZONE.vpd.input} ${inputBase}`}
                    placeholder="kPa"
                  />
                </td>
                <td className={`px-1 py-1 border-r border-border-subtle ${ZONE.co2.td}`}>
                  <input
                    type="number"
                    min="400"
                    max="2000"
                    value={period.co2_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'co2_setpoint', e.target.value ? parseInt(e.target.value) : null)}
                    className={`w-18 text-xs ${ZONE.co2.input} ${inputBase}`}
                    placeholder="ppm"
                  />
                </td>
                <td className="px-1 py-1 border-r border-border-subtle group-hover:bg-surface-secondary/50">
                  <input
                    type="text"
                    value={period.details}
                    onChange={(e) => updatePeriod(index, 'details', e.target.value)}
                    className={`w-full text-xs bg-surface-secondary ${inputBase}`}
                    placeholder="Notes"
                  />
                </td>
                <td className="px-1 py-1 group-hover:bg-surface-secondary/50">
                  <button
                    type="button"
                    onClick={() => removePeriod(index)}
                    disabled={periods.length <= 1}
                    className="text-text-subtle hover:text-status-danger disabled:opacity-30 disabled:cursor-not-allowed text-xs transition-colors"
                    title="Remove period"
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
