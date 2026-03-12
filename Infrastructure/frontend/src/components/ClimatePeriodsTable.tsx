export interface ClimatePeriod {
  id?: number
  period_name: string
  start_time: string
  end_time: string
  ramp_minutes: number
  heating_setpoint: number | null
  cooling_setpoint: number | null
  vpd_setpoint: number | null
  co2_setpoint: number | null
  details: string
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
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-text-primary !mb-0">Climate Periods</h3>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={addPeriod}
            disabled={periods.length >= 7}
            className="px-3 py-1.5 text-sm font-medium rounded-md bg-accent-primary/20 text-accent-primary hover:bg-accent-primary/30 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            + Add Period
          </button>
        </div>
      </div>

      {validationErrors.length > 0 && (
        <div className="bg-red-900/20 border border-red-700 rounded-md p-2">
          <p className="text-sm font-medium text-red-400 mb-1">Validation Errors:</p>
          <ul className="text-sm text-red-300 space-y-0.5">
            {validationErrors.map((err, i) => (
              <li key={i}>• {err}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-surface-primary border-b border-border-subtle">
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-32">Period</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-16">Start</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-16">End</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-14">Ramp</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-14">Heat</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-14">Cool</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-18">VPD</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle w-18">CO₂</th>
              <th className="px-1 py-0 text-left text-text-muted font-medium border-r border-border-subtle">Details</th>
              <th className="px-1 py-0 w-2"></th>
            </tr>
          </thead>
          <tbody>
            {periods.map((period, index) => (
              <tr key={index} className="border-b border-border-subtle hover:bg-surface-primary/50 text-left">
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="text"
                    value={period.period_name}
                    onChange={(e) => updatePeriod(index, 'period_name', e.target.value)}
                    className="w-full px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                    placeholder="Period name"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="text"
                    pattern="[0-9]{2}:[0-9]{2}"
                    placeholder="HH:MM"
                    value={period.start_time}
                    onChange={(e) => updatePeriod(index, 'start_time', e.target.value)}
                    className="w-16 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="text"
                    pattern="[0-9]{2}:[0-9]{2}"
                    placeholder="HH:MM"
                    value={period.end_time}
                    onChange={(e) => updatePeriod(index, 'end_time', e.target.value)}
                    className="w-16 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="number"
                    min="0"
                    max="240"
                    value={period.ramp_minutes}
                    onChange={(e) => updatePeriod(index, 'ramp_minutes', parseInt(e.target.value) || 0)}
                    className="w-14 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="number"
                    min="10"
                    max="35"
                    step="0.5"
                    value={period.heating_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'heating_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
                    className="w-14 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                    placeholder="°C"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="number"
                    min="10"
                    max="35"
                    step="0.5"
                    value={period.cooling_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'cooling_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
                    className="w-14 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                    placeholder="°C"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="number"
                    min="0"
                    max="5"
                    step="0.1"
                    value={period.vpd_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'vpd_setpoint', e.target.value ? parseFloat(e.target.value) : null)}
                    className="w-18 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                    placeholder="kPa"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="number"
                    min="400"
                    max="2000"
                    value={period.co2_setpoint ?? ''}
                    onChange={(e) => updatePeriod(index, 'co2_setpoint', e.target.value ? parseInt(e.target.value) : null)}
                    className="w-18 px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                    placeholder="ppm"
                  />
                </td>
                <td className="px-1 py-0 border-r border-border-subtle">
                  <input
                    type="text"
                    value={period.details}
                    onChange={(e) => updatePeriod(index, 'details', e.target.value)}
                    className="w-full px-1 py-0 text-sm bg-surface-primary border border-border-subtle rounded text-text-primary focus:border-accent-primary focus:outline-none"
                    placeholder="Notes"
                  />
                </td>
                <td className="px-0.5 py-0">
                  <button
                    type="button"
                    onClick={() => removePeriod(index)}
                    disabled={periods.length <= 1}
                    className="text-text-muted hover:text-red-400 disabled:opacity-30 disabled:cursor-not-allowed text-xs"
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
