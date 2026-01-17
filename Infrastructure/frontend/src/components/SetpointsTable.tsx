import type { ModeParameters } from '../types/modes'

interface SetpointsTableProps {
  params: ModeParameters
  currentParams?: ModeParameters
  isConstant: boolean
  onChange: (updates: Partial<ModeParameters>) => void
}

export default function SetpointsTable({
  params,
  currentParams,
  isConstant,
  onChange
}: SetpointsTableProps) {
  const inputClass = "w-full h-6 px-1 text-xs text-center bg-gray-800 border border-gray-700 rounded text-gray-200 focus:border-cyan-500"
  
  function renderCell(value: number, field: keyof ModeParameters, currentValue?: number, step = 0.1, colorClass = '') {
    return (
      <td className="px-1 py-1 relative">
        <input
          type="number"
          step={step}
          value={value}
          onChange={(e) => onChange({ [field]: parseFloat(e.target.value) || 0 })}
          className={`${inputClass} ${colorClass}`}
        />
        {currentValue !== undefined && currentValue !== value && (
          <span className="absolute -top-1 right-0 text-[8px] text-gray-500">{currentValue}</span>
        )}
      </td>
    )
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 h-full">
      <div className="text-xs text-gray-400 uppercase font-bold tracking-wider mb-2">Setpoints</div>
      
      <table className="w-full text-xs">
        <thead>
          <tr className="text-gray-500 uppercase text-[10px]">
            <th className="text-left px-1 py-1 w-12"></th>
            <th className="px-1 py-1">Heat</th>
            <th className="px-1 py-1">Cool</th>
            <th className="px-1 py-1">VPD</th>
            <th className="px-1 py-1">CO2</th>
            <th className="px-1 py-1">Leaf</th>
          </tr>
        </thead>
        <tbody>
          {!isConstant && (
            <tr>
              <td className="px-1 py-1 text-amber-400 font-medium">Day</td>
              {renderCell(params.day_heat_temp, 'day_heat_temp', currentParams?.day_heat_temp, 0.1, 'text-orange-200')}
              {renderCell(params.day_cool_temp, 'day_cool_temp', currentParams?.day_cool_temp, 0.1, 'text-blue-200')}
              {renderCell(params.day_vpd, 'day_vpd', currentParams?.day_vpd, 0.01, 'text-emerald-200')}
              {renderCell(params.day_co2, 'day_co2', currentParams?.day_co2, 1, 'text-purple-200')}
              {renderCell(params.day_leaf_delta, 'day_leaf_delta', currentParams?.day_leaf_delta, 0.1, 'text-green-200')}
            </tr>
          )}
          <tr>
            <td className="px-1 py-1 text-indigo-400 font-medium">{isConstant ? 'Set' : 'Night'}</td>
            {renderCell(params.night_heat_temp, 'night_heat_temp', currentParams?.night_heat_temp, 0.1, 'text-orange-200')}
            {renderCell(params.night_cool_temp, 'night_cool_temp', currentParams?.night_cool_temp, 0.1, 'text-blue-200')}
            {renderCell(params.night_vpd, 'night_vpd', currentParams?.night_vpd, 0.01, 'text-emerald-200')}
            {renderCell(params.night_co2, 'night_co2', currentParams?.night_co2, 1, 'text-purple-200')}
            {renderCell(params.night_leaf_delta, 'night_leaf_delta', currentParams?.night_leaf_delta, 0.1, 'text-green-200')}
          </tr>
        </tbody>
      </table>
    </div>
  )
}
