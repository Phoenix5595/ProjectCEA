import LightSlider from './LightSlider'
import type { ModeParameters } from '../types/modes'

interface ScheduleLightsPanelProps {
  params: ModeParameters
  currentParams?: ModeParameters
  isConstant: boolean
  onChange: (updates: Partial<ModeParameters>) => void
}

export default function ScheduleLightsPanel({
  params,
  currentParams,
  isConstant,
  onChange
}: ScheduleLightsPanelProps) {
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-3 h-full flex flex-col">
      <div className="text-xs text-gray-400 uppercase font-bold tracking-wider mb-3">Schedule & Lights</div>
      
      <div className="space-y-3 flex-1">
        {!isConstant && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-gray-500 uppercase block mb-1">Day Start</label>
              <input 
                type="time" 
                value={params.day_start_time}
                onChange={(e) => onChange({ day_start_time: e.target.value })}
                className="w-full h-7 px-2 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 focus:border-cyan-500"
              />
            </div>
            <div>
              <label className="text-[10px] text-gray-500 uppercase block mb-1">Night Start</label>
              <input 
                type="time" 
                value={params.night_start_time}
                onChange={(e) => onChange({ night_start_time: e.target.value })}
                className="w-full h-7 px-2 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 focus:border-cyan-500"
              />
            </div>
          </div>
        )}
        
        {!isConstant && (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-[10px] text-gray-500 uppercase block mb-1">Pre-Day (min)</label>
              <div className="flex gap-1">
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={params.pre_day_minutes}
                  onChange={(e) => onChange({ pre_day_minutes: parseInt(e.target.value) || 0 })}
                  className="w-full h-7 px-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 text-center"
                />
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={params.ramp_up_minutes}
                  onChange={(e) => onChange({ ramp_up_minutes: parseInt(e.target.value) || 0 })}
                  className="w-full h-7 px-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 text-center"
                  title="Ramp Up"
                />
              </div>
            </div>
            <div>
              <label className="text-[10px] text-gray-500 uppercase block mb-1">Pre-Night (min)</label>
              <div className="flex gap-1">
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={params.pre_night_minutes}
                  onChange={(e) => onChange({ pre_night_minutes: parseInt(e.target.value) || 0 })}
                  className="w-full h-7 px-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 text-center"
                />
                <input
                  type="number"
                  min="0"
                  max="120"
                  value={params.ramp_down_minutes}
                  onChange={(e) => onChange({ ramp_down_minutes: parseInt(e.target.value) || 0 })}
                  className="w-full h-7 px-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-200 text-center"
                  title="Ramp Down"
                />
              </div>
            </div>
          </div>
        )}
        
        <div className="pt-2 border-t border-gray-800">
          <label className="text-[10px] text-gray-500 uppercase block mb-2">Light Intensity</label>
          <div className="space-y-2">
            <LightSlider
              label="Main"
              value={params.main_light_intensity}
              currentValue={currentParams?.main_light_intensity}
              onChange={(v) => onChange({ main_light_intensity: v })}
            />
            <LightSlider
              label="Supp"
              value={params.supplemental_light_intensity}
              currentValue={currentParams?.supplemental_light_intensity}
              onChange={(v) => onChange({ supplemental_light_intensity: v })}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
