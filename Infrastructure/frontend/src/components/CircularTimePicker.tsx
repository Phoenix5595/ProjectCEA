import { useState, useEffect, useRef } from 'react'
import CircularClockFace from './CircularClockFace'
import { useClockInteraction } from '../hooks/useClockInteraction'
import { calculatePhotoperiod, minutesToTime, timeToMinutes } from '../utils/timeMath'

interface CircularTimePickerProps {
  dayStartTime: string
  dayEndTime: string
  onDayStartChange: (time: string) => void
  onDayEndChange: (time: string) => void
  label?: string
  period?: 'day' | 'night'
  rampUpDuration?: number | null
  rampDownDuration?: number | null
  onRampUpChange?: (duration: number | null) => void
  onRampDownChange?: (duration: number | null) => void
  showPresetButtons?: boolean
  lockedPhotoperiodHours?: number | null
  size?: number
}

export default function CircularTimePicker({
  dayStartTime,
  dayEndTime,
  onDayStartChange,
  onDayEndChange,
  label,
  period = 'day',
  rampUpDuration,
  rampDownDuration,
  onRampUpChange,
  onRampDownChange,
  showPresetButtons: _showPresetButtons = true,
  lockedPhotoperiodHours = null,
  size: propsSize,
}: CircularTimePickerProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState(propsSize || 300)

  // Handle responsive sizing
  useEffect(() => {
    if (propsSize) {
      setSize(propsSize)
      return
    }

    if (!containerRef.current) return

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect
        const newSize = Math.max(100, Math.min(width, height > 100 ? height : width))
        if (newSize > 100) {
          setSize(newSize)
        }
      }
    })

    observer.observe(containerRef.current)
    return () => observer.disconnect()
  }, [propsSize])

  // Use clock interaction hook
  const { handleMouseDown } = useClockInteraction({
    canvasRef,
    dayStartTime,
    dayEndTime,
    onDayStartChange,
    onDayEndChange,
    lockedPhotoperiodHours,
  })

  // Enforce locked photoperiod duration
  useEffect(() => {
    if (lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined) {
      const startMinutes = timeToMinutes(dayStartTime)
      const lockedDurationMinutes = lockedPhotoperiodHours * 60
      const expectedEndMinutes = (startMinutes + lockedDurationMinutes) % 1440
      const expectedEndTime = minutesToTime(expectedEndMinutes)

      if (dayEndTime !== expectedEndTime) {
        onDayEndChange(expectedEndTime)
      }
    }
  }, [dayStartTime, lockedPhotoperiodHours, dayEndTime, onDayEndChange])

  return (
    <div ref={containerRef} className="flex items-center gap-3 w-full h-full">
      <div className="flex flex-col items-center flex-1 min-w-0">
        {label && (
          <label className="block text-sm font-medium text-text-secondary mb-2">
            {label}
          </label>
        )}
        <div className="relative w-full aspect-square flex items-center justify-center">
          <canvas
            ref={canvasRef}
            width={size}
            height={size}
            onMouseDown={handleMouseDown}
            className="cursor-pointer max-w-full max-h-full"
          />
          <CircularClockFace
            canvasRef={canvasRef}
            size={size}
            dayStartTime={dayStartTime}
            dayEndTime={dayEndTime}
            period={period}
          />
        </div>
      </div>
      <div className="pt-1 flex flex-col justify-center w-fit">
        <div className="flex flex-col gap-1">
          <div className="flex flex-col">
            <label className="text-[12px] text-text-muted shrink-0">Start</label>
            <input
              type="text"
              pattern="[0-2][0-9]:[0-5][0-9]"
              value={dayStartTime}
              onChange={(e) => {
                if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
                  onDayStartChange(e.target.value)
                }
              }}
              disabled={lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined}
              className={`w-16 h-6 text-[16px] text-center bg-surface-secondary border border-border-default rounded text-text-input ${
                lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined
                  ? 'opacity-50 cursor-not-allowed'
                  : ''
              }`}
              style={{ padding: '0 2px' }}
            />
          </div>
          <div className="flex flex-col">
            <label className="text-[12px] text-text-muted shrink-0">End</label>
            <input
              type="text"
              pattern="[0-2][0-9]:[0-5][0-9]"
              value={dayEndTime}
              onChange={(e) => {
                if (lockedPhotoperiodHours === null || lockedPhotoperiodHours === undefined) {
                  onDayEndChange(e.target.value)
                }
              }}
              disabled={lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined}
              className={`w-16 h-6 text-[16px] text-center bg-surface-secondary border border-border-default rounded text-text-input ${
                lockedPhotoperiodHours !== null && lockedPhotoperiodHours !== undefined
                  ? 'opacity-50 cursor-not-allowed'
                  : ''
              }`}
              style={{ padding: '0 2px' }}
            />
          </div>
          {onRampUpChange && rampUpDuration !== undefined && (
            <div className="flex flex-col">
              <label className="text-[12px] text-text-muted shrink-0">Ramp ↑</label>
              <input
                type="number"
                min="0"
                max="180"
                value={rampUpDuration ?? ''}
                onChange={(e) => {
                  const val = e.target.value ? parseInt(e.target.value) : null
                  onRampUpChange(val !== null && val > 180 ? 180 : val)
                }}
                className="w-12 h-6 text-[16px] text-center bg-surface-secondary border border-border-default rounded-sm text-text-input [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              />
            </div>
          )}
          {onRampDownChange && rampDownDuration !== undefined && (
            <div className="flex flex-col">
              <label className="text-[12px] text-text-muted shrink-0">Ramp ↓</label>
              <input
                type="number"
                min="0"
                max="180"
                value={rampDownDuration ?? ''}
                onChange={(e) => {
                  const val = e.target.value ? parseInt(e.target.value) : null
                  onRampDownChange(val !== null && val > 180 ? 180 : val)
                }}
                className="w-12 h-6 text-[16px] text-center bg-surface-secondary border border-border-default rounded-sm text-text-input [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
              />
            </div>
          )}
        </div>
        <div className="flex flex-col items-center mt-2 text-text-muted">
          <span className="text-[12px]">Photoperiod</span>
          <span className="text-sm font-medium text-text-input font-mono tabular-nums">
            {calculatePhotoperiod(dayStartTime, dayEndTime).toFixed(1)}h
          </span>
        </div>
      </div>
    </div>
  )
}
