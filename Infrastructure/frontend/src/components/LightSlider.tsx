import { useState, useEffect, useRef } from 'react'

interface LightSliderProps {
 label: string
 value: number
 currentValue?: number
 onChange: (value: number) => void
 min?: number
 max?: number
 disabled?: boolean
}

export default function LightSlider({
 label,
 value,
 currentValue,
 onChange,
 min = 0,
 max = 100,
 disabled = false
}: LightSliderProps) {
 const [localValue, setLocalValue] = useState(value)
 const sliderRef = useRef<HTMLDivElement>(null)
 
 useEffect(() => {
 setLocalValue(value)
 }, [value])
 
 const percentage = ((localValue - min) / (max - min)) * 100
 const currentPercentage = currentValue !== undefined 
 ? ((currentValue - min) / (max - min)) * 100 
 : null
 
 function handleSliderClick(e: React.MouseEvent<HTMLDivElement>) {
 if (disabled || !sliderRef.current) return
 const rect = sliderRef.current.getBoundingClientRect()
 const y = rect.bottom - e.clientY // Invert for vertical (top = 100%, bottom = 0%)
 const newPercentage = Math.max(0, Math.min(100, (y / rect.height) * 100))
 const newValue = Math.round(min + (newPercentage / 100) * (max - min))
 setLocalValue(newValue)
 onChange(newValue)
 }
 
 function handleInputChange(e: React.ChangeEvent<HTMLInputElement>) {
 const newValue = parseInt(e.target.value) || 0
 const clampedValue = Math.max(min, Math.min(max, newValue))
 setLocalValue(clampedValue)
 onChange(clampedValue)
 }

 return (
 <div className="flex items-center gap-2">
 <span className="text-xs text-text-muted w-16 truncate">{label}</span>
 <div className="flex items-center gap-2 flex-1">
 <div 
 ref={sliderRef}
 onClick={handleSliderClick}
 className={`relative w-6 h-24 bg-surface-secondary rounded-sm cursor-pointer ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
 >
 <div 
 className="absolute bottom-0 left-0 w-full bg-linear-to-t from-accent-setpoint-dim to-accent-setpoint rounded-sm transition-all"
 style={{ height: `${percentage}%` }}
 />
 {currentPercentage !== null && (
 <div 
 className="absolute left-0 w-full h-0.5 bg-surface-primary/60"
 style={{ bottom: `${currentPercentage}%` }}
 title={`Current: ${currentValue}%`}
 />
 )}
 <div 
 className="absolute left-1/2 -translate-x-1/2 w-3 h-3 bg-surface-primary rounded-full shadow-md border border-border-input"
 style={{ bottom: `calc(${percentage}% - 6px)` }}
 />
 </div>
 <div className="flex flex-col items-center gap-1">
 <input
 type="number"
 min={min}
 max={max}
 value={localValue}
 onChange={handleInputChange}
 disabled={disabled}
 className="w-12 h-6 px-1 text-xs text-center bg-surface-secondary border border-border-default rounded-sm text-text-input disabled:opacity-50"
 />
 <span className="text-xs text-text-subtle">%</span>
 </div>
 </div>
 </div>
 )
}
