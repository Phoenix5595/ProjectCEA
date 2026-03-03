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
 if (isConstant) {
 return (
  <div className="bg-surface-primary rounded-lg border border-border-subtle p-2">
 <div className="text-xs text-text-muted uppercase font-bold tracking-wider mb-2">Constant Setpoints</div>
 <Setpoints2x2 params={params} currentParams={currentParams} onChange={onChange} prefix="night" />
 </div>
 )
 }

  return (
  <div className="grid grid-cols-4 gap-1.5">
  <PeriodCard title="Pre-Day" colorClass="border-period-preday/50 bg-period-preday-bg/20" titleColor="text-accent-setpoint-dim">
  <TimingRow>
  <TimingInput label="Duration" value={params.pre_day_minutes} field="pre_day_minutes" onChange={onChange} tooltip="Pre-day period duration" />
  <TimingInput label="Ramp" value={params.pre_day_ramp_minutes} field="pre_day_ramp_minutes" onChange={onChange} tooltip="Pre-day ramp transition time" />
  </TimingRow>
  <Setpoints2x2 params={params} currentParams={currentParams} onChange={onChange} prefix="pre_day" />
  </PeriodCard>

  <PeriodCard title="Day" colorClass="border-period-day/50 bg-period-day-bg/20" titleColor="text-accent-setpoint">
  <TimingRow>
  <TimingInput label="Ramp" value={params.ramp_up_minutes} field="ramp_up_minutes" onChange={onChange} tooltip="Ramp up transition time" />
  <LeafInput label="Leaf Δ" value={params.day_leaf_delta} field="day_leaf_delta" currentValue={currentParams?.day_leaf_delta} onChange={onChange} tooltip="Leaf temp offset (Day + Pre-Night)" />
  </TimingRow>
  <Setpoints2x2 params={params} currentParams={currentParams} onChange={onChange} prefix="day" />
  </PeriodCard>

  <PeriodCard title="Pre-Night" colorClass="border-indigo-900/50 bg-indigo-950/20" titleColor="text-indigo-400">
  <TimingRow>
  <TimingInput label="Duration" value={params.pre_night_minutes} field="pre_night_minutes" onChange={onChange} tooltip="Pre-night period duration" />
  <TimingInput label="Ramp" value={params.pre_night_ramp_minutes} field="pre_night_ramp_minutes" onChange={onChange} tooltip="Pre-night ramp transition time" />
  </TimingRow>
  <Setpoints2x2 params={params} currentParams={currentParams} onChange={onChange} prefix="pre_night" />
  </PeriodCard>

  <PeriodCard title="Night" colorClass="border-indigo-700/50 bg-indigo-900/20" titleColor="text-indigo-300">
  <TimingRow>
  <TimingInput label="Ramp" value={params.ramp_down_minutes} field="ramp_down_minutes" onChange={onChange} tooltip="Ramp down transition time" />
  <LeafInput label="Leaf Δ" value={params.night_leaf_delta} field="night_leaf_delta" currentValue={currentParams?.night_leaf_delta} onChange={onChange} tooltip="Leaf temp offset (Night + Pre-Day)" />
  </TimingRow>
  <Setpoints2x2 params={params} currentParams={currentParams} onChange={onChange} prefix="night" />
  </PeriodCard>
  </div>
  )
}

 function PeriodCard({ title, colorClass, titleColor, children }: { title: string; colorClass: string; titleColor: string; children: React.ReactNode }) {
  return (
  <div className={`rounded-lg border p-1 ${colorClass}`}>
  <div className={`text-[12px] font-bold uppercase tracking-wider mb-2 ${titleColor}`}>{title}</div>
  {children}
  </div>
  )
}

function TimingRow({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-2 gap-1 mb-1">{children}</div>
}

function TimingInput({ label, value, field, onChange, tooltip }: { label: string; value: number; field: keyof ModeParameters; onChange: (u: Partial<ModeParameters>) => void; tooltip: string }) {
 return (
 <div>
 <div className="text-[11px] text-text-muted mb-0.5 cursor-help" title={tooltip}>{label}</div>
 <div className="flex items-center gap-1">
 <input
 type="number"
 min={0}
 max={120}
 value={value}
 onChange={(e) => onChange({ [field]: parseInt(e.target.value) || 0 })}
 className="w-full h-6 px-1.5 text-[14px] text-center bg-surface-secondary border border-border-default rounded-sm text-text-input"
 />
 <span className="text-[11px] text-text-subtle w-5">min</span>
 </div>
 </div>
 )
}

function LeafInput({ label, value, field, currentValue, onChange, tooltip }: { label: string; value: number; field: keyof ModeParameters; currentValue?: number; onChange: (u: Partial<ModeParameters>) => void; tooltip: string }) {
 const changed = currentValue !== undefined && currentValue !== value
 return (
 <div>
 <div className="text-[12px] text-status-success mb-1 cursor-help" title={tooltip}>{label}</div>
 <div className="flex items-center gap-1">
 <input
 type="number"
 step={0.5}
 value={value}
 onChange={(e) => onChange({ [field]: parseFloat(e.target.value) || 0 })}
 className={`w-full h-7 px-2 text-[16px] text-center bg-surface-secondary border rounded-sm text-status-success ${changed ? 'border-accent-vivid' : 'border-border-default'}`}
 />
 <span className="text-[12px] text-text-subtle w-6">°C</span>
 </div>
 </div>
 )
}

function Setpoints2x2({ params, currentParams, onChange, prefix }: { params: ModeParameters; currentParams?: ModeParameters; onChange: (u: Partial<ModeParameters>) => void; prefix: 'pre_day' | 'day' | 'pre_night' | 'night' }) {
 const h = `${prefix}_heat_temp` as keyof ModeParameters
 const c = `${prefix}_cool_temp` as keyof ModeParameters
 const v = `${prefix}_vpd` as keyof ModeParameters
 const co2 = `${prefix}_co2` as keyof ModeParameters

 return (
 <div>
 <div className="text-[12px] text-text-subtle mb-1">Setpoints</div>
 <div className="grid grid-cols-2 gap-2">
 <SetpointInput label="Heating" value={Math.round(params[h] as number * 100) / 100} current={currentParams?.[h] as number} onChange={(val) => onChange({ [h]: Math.round(val * 100) / 100 })} step={0.01} color="text-orange-300" unit="°C" tip="Heater activates below this" />
 <SetpointInput label="Cooling" value={Math.round(params[c] as number * 100) / 100} current={currentParams?.[c] as number} onChange={(val) => onChange({ [c]: Math.round(val * 100) / 100 })} step={0.01} color="text-btn-primary-text" unit="°C" tip="Cooling activates above this" />
 <SetpointInput label="VPD" value={Math.round(params[v] as number * 100) / 100} current={currentParams?.[v] as number} onChange={(val) => onChange({ [v]: Math.round(val * 100) / 100 })} step={0.01} color="text-emerald-300" unit="kPa" tip="Vapor Pressure Deficit target" />
 <SetpointInput label="CO2" value={params[co2] as number} current={currentParams?.[co2] as number} onChange={(val) => onChange({ [co2]: val })} step={50} color="text-mode-auto-text" unit="ppm" tip="CO2 concentration target" />
 </div>
 </div>
 )
}

function SetpointInput({ label, value, current, onChange, step, color, unit, tip }: { label: string; value: number; current?: number; onChange: (v: number) => void; step: number; color: string; unit: string; tip: string }) {
 const changed = current !== undefined && current !== value
 return (
 <div>
 <div className={`text-[12px] ${color} font-medium mb-1 cursor-help`} title={tip}>{label}</div>
 <div className="flex items-center gap-1">
 <input
 type="number"
 step={step}
 value={value}
 onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
 className={`w-full h-7 px-2 text-[16px] text-center bg-surface-secondary border rounded-sm ${color} ${changed ? 'border-accent-vivid' : 'border-border-default'}`}
 />
 <span className="text-[12px] text-text-subtle w-6">{unit}</span>
 </div>
 </div>
 )
}
