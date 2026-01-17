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
    const x = e.clientX - rect.left
    const newPercentage = Math.max(0, Math.min(100, (x / rect.width) * 100))
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
      <span className="text-xs text-gray-400 w-16 truncate">{label}</span>
      <div 
        ref={sliderRef}
        onClick={handleSliderClick}
        className={`relative flex-1 h-6 bg-gray-800 rounded cursor-pointer ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}
      >
        <div 
          className="absolute top-0 left-0 h-full bg-gradient-to-r from-amber-600 to-amber-400 rounded transition-all"
          style={{ width: `${percentage}%` }}
        />
        {currentPercentage !== null && (
          <div 
            className="absolute top-0 w-0.5 h-full bg-white/60"
            style={{ left: `${currentPercentage}%` }}
            title={`Current: ${currentValue}%`}
          />
        )}
        <div 
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full shadow-md border border-gray-400"
          style={{ left: `calc(${percentage}% - 6px)` }}
        />
      </div>
      <input
        type="number"
        min={min}
        max={max}
        value={localValue}
        onChange={handleInputChange}
        disabled={disabled}
        className="w-12 h-6 px-1 text-xs text-center bg-gray-800 border border-gray-700 rounded text-gray-200 disabled:opacity-50"
      />
      <span className="text-xs text-gray-500">%</span>
    </div>
  )
}
